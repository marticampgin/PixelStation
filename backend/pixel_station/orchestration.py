import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Route(BaseModel):
    intent: Literal[
        "normal_chat",
        "web_search",
        "web_research",
        "file_question",
        "file_summary",
        "file_create",
        "file_edit",
        "image_generate",
        "gmail_search",
        "gmail_read",
        "gmail_draft",
        "gmail_send",
        "calendar_read",
        "calendar_create",
        "calendar_update",
        "calendar_delete",
        "memory_query",
        "memory_write",
        "game",
        "system_help",
    ] = "normal_chat"
    complexity: Literal["simple", "tool", "complex"] = "simple"
    tools_needed: list[str] = Field(default_factory=list)
    requires_plan: bool = False
    requires_confirmation: bool = False


def route_prompt(content: str, attachments: list[str] | None = None) -> Route:
    text = content.lower().strip()
    tests = [
        (
            r"\b(edit|revise|update|replace)\b.{0,40}\b(file|document|spreadsheet|pdf|docx|xlsx|csv|notes)\b",
            "file_edit",
            ["file_edit"],
            True,
        ),
        (r"\bdelete\b.{0,30}\b(emails?|mail)\b", "system_help", [], False),
        (
            r"(^/image\b|\b(generate|create|draw|make)\b.{0,35}\b(image|picture|illustration)\b)",
            "image_generate",
            ["image_generate"],
            False,
        ),
        (r"\bsend\b.{0,30}\b(emails?|mail)\b", "gmail_send", ["gmail_send"], True),
        (r"\b(draft|reply)\b.{0,35}\b(emails?|mail)\b", "gmail_draft", ["gmail_draft"], False),
        (
            r"\b(read|open|summarize)\b.{0,35}\b(emails?|mail|gmail)\b",
            "gmail_read",
            ["gmail_search", "gmail_read"],
            False,
        ),
        (r"\b(emails?|gmail|inbox|mail)\b", "gmail_search", ["gmail_search"], False),
        (
            r"\b(delete|cancel)\b.{0,30}\b(event|appointment|meeting)\b",
            "calendar_delete",
            ["calendar_delete"],
            True,
        ),
        (
            r"\b(reschedule|update|move)\b.{0,30}\b(event|appointment|meeting)\b",
            "calendar_update",
            ["calendar_update"],
            True,
        ),
        (
            r"\b(schedule|book|create)\b.{0,35}\b(event|appointment|meeting)\b",
            "calendar_create",
            ["calendar_create"],
            True,
        ),
        (r"\b(calendar|agenda|appointments)\b", "calendar_read", ["calendar_read"], False),
        (
            r"\b(research|investigate|compare)\b.{0,70}\b(web|online|sources)\b|^/research\b",
            "web_research",
            ["web_search", "web_fetch"],
            False,
        ),
        (
            r"\b(search|look up|find)\b.{0,40}\b(web|internet|online)\b|^/search\b",
            "web_search",
            ["web_search"],
            False,
        ),
        (r"^(/remember\b|remember\b)", "memory_write", ["memory_write"], False),
        (
            r"\b(what do you remember|search memor|my memories)\b",
            "memory_query",
            ["memory_query"],
            False,
        ),
        (
            r"\b(create|generate|save|write)\b.{0,40}\b(file|docx|xlsx|pdf|csv|document|spreadsheet)\b",
            "file_create",
            ["file_create"],
            False,
        ),
    ]
    for pattern, intent, tools, confirmation in tests:
        if re.search(pattern, text):
            complex_route = intent == "web_research"
            return Route.model_validate(
                dict(
                    intent=intent,
                    complexity="complex" if complex_route else "tool",
                    tools_needed=tools,
                    requires_plan=complex_route,
                    requires_confirmation=confirmation,
                )
            )
    if attachments:
        return Route(
            intent="file_summary" if re.search(r"\bsummar", text) else "file_question",
            complexity="tool",
            tools_needed=["file_retrieve"],
        )
    return Route()


async def classify_ambiguous(app, content: str, route: Route) -> Route:
    """Only ambiguous action language merits a second model call; code owns permissions."""
    model = app.state.settings().roles["router"]
    if (
        route.intent != "normal_chat"
        or not model
        or not re.search(
            r"\b(check|look up|find|create|generate|show me|latest|research)\b", content.lower()
        )
    ):
        return route
    async with asyncio.timeout(60), app.state.model_queue.lock:
        result = await app.state.llm.structured(
            model,
            [
                {
                    "role": "system",
                    "content": "Classify the intent only. Use normal_chat for explanations, creative writing, and ordinary questions. Choose tools only for explicit requests to access web, user files, email, calendar, images, or saved memory. No reasoning prose.",
                },
                {"role": "user", "content": content},
            ],
            Route,
        )
    tool_map = {
        "web_research": ["web_search", "web_fetch"],
        "file_question": ["file_retrieve"],
        "file_summary": ["file_retrieve"],
        "normal_chat": [],
        "system_help": [],
        "game": [],
    }
    result.tools_needed = tool_map.get(result.intent, [result.intent])
    result.requires_confirmation = result.intent in {
        "gmail_send",
        "calendar_create",
        "calendar_update",
        "calendar_delete",
        "file_edit",
    }
    result.requires_plan = result.intent == "web_research"
    return result


@dataclass
class Tool:
    id: str
    display_name: str
    description: str
    category: str
    input_schema: dict
    execute: Callable
    permission: str = "read_only"
    timeout: float = 30
    enabled: bool = True
    required_integration: str | None = None
    output_schema: dict | None = None

    def metadata(self) -> dict:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "description": self.description,
            "category": self.category,
            "input_schema": self.input_schema,
            "permission": self.permission,
            "timeout": self.timeout,
            "enabled": self.enabled,
            "required_integration": self.required_integration,
            "read_only": self.permission == "read_only",
        }


class ToolRegistry:
    def __init__(self):
        self.tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.id in self.tools:
            raise ValueError(f"Duplicate tool: {tool.id}")
        self.tools[tool.id] = tool

    async def execute(self, tool_id: str, args: dict, approved: bool = False) -> Any:
        import jsonschema

        tool = self.tools.get(tool_id)
        if tool is None or not tool.enabled:
            raise ValueError(f"Tool unavailable: {tool_id}")
        if tool.permission == "external_or_destructive" and not approved:
            raise PermissionError("This action requires an application confirmation")
        jsonschema.validate(args, tool.input_schema)
        async with asyncio.timeout(tool.timeout):
            result = tool.execute(**args)
            if hasattr(result, "__await__"):
                result = await result
        if tool.output_schema:
            jsonschema.validate(result, tool.output_schema)
        return result


class PlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    tool: str
    args: dict = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    args_from: str | None = None
    result_index: int = Field(default=0, ge=0, le=19)


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    steps: list[PlanStep] = Field(min_length=1, max_length=12)

    def validate_dag(self, allowed: set[str], limit: int) -> None:
        if len(self.steps) > limit or len({step.id for step in self.steps}) != len(self.steps):
            raise ValueError("Plan exceeds limits or repeats step IDs")
        visited: set[str] = set()
        pending = list(self.steps)
        while pending:
            ready = [step for step in pending if set(step.depends_on) <= visited]
            if not ready:
                raise ValueError("Plan contains cycle or missing dependency")
            for step in ready:
                if step.tool not in allowed:
                    raise ValueError("Plan selected irrelevant or nonexistent tool")
                if step.args_from and (
                    step.args_from not in step.depends_on or step.tool != "web_fetch"
                ):
                    raise ValueError("Fetch evidence must reference a completed search dependency")
                visited.add(step.id)
                pending.remove(step)


async def execute_plan(
    plan: Plan,
    registry: ToolRegistry,
    limit: int,
    timeout: float = 90,
    allowed_tools: set[str] | None = None,
    continue_on_error: bool = False,
    on_step=None,
) -> dict:
    plan.validate_dag(allowed_tools if allowed_tools is not None else set(registry.tools), limit)
    evidence: dict[str, Any] = {}
    pending = list(plan.steps)
    async with asyncio.timeout(timeout):
        while pending:
            ready = [step for step in pending if set(step.depends_on) <= set(evidence)]

            async def run(step):
                args = step.args.copy()
                if step.args_from:
                    source = evidence[step.args_from]
                    if not isinstance(source, list) or step.result_index >= len(source):
                        return {
                            "error": "No search result available for this fetch",
                            "skipped": True,
                        }
                    chosen = source[step.result_index]
                    if not isinstance(chosen, dict) or not chosen.get("url"):
                        return {"error": "Search result has no source URL", "skipped": True}
                    args["url"] = chosen["url"]
                if on_step:
                    on_step(step, "running", None)
                result = await registry.execute(step.tool, args)
                return result

            tasks = [asyncio.create_task(run(step)) for step in ready]
            try:
                results = await asyncio.gather(*tasks, return_exceptions=continue_on_error)
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            for step, result in zip(ready, results, strict=True):
                if isinstance(result, asyncio.CancelledError):
                    raise result
                if isinstance(result, Exception):
                    result = {"error": str(result)[:1000]}
                evidence[step.id] = result
                if on_step:
                    on_step(step, "complete", result)
                pending.remove(step)
    return evidence
