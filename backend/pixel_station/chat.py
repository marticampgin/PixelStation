import asyncio
import base64
import json
import re
import time
from dataclasses import dataclass
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.orm import Session

from .context import approximate_tokens, build_context, clip, input_budget
from .database import (
    AgentRun,
    Attachment,
    Conversation,
    FrictionEvent,
    Message,
    get_session,
    now,
    record_dict,
)
from .file_edits import propose_chat_edit
from .files import IMAGE_EXTENSIONS, FileCreate, retrieve_files, write_generated
from .google_tools import google_chat_action
from .indexing import embed_query
from .memory import MemoryInput, compact_conversation, create_memory, search_memory
from .observability import provider_observations, run_metrics
from .orchestration import Plan, PlanStep, Route, classify_ambiguous, route_prompt
from .providers import UnsupportedToolCall
from .providers.web import canonical_url
from .research import run_research, validate_research_plan

router = APIRouter(prefix="/api", tags=["chat"])


class ConversationCreate(BaseModel):
    title: str = Field(default="New chat", min_length=1, max_length=200)


class ConversationPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    archived: bool | None = None


class SourceReference(BaseModel):
    url: str = Field(min_length=8, max_length=4000)
    title: str = Field(default="", max_length=1000)
    text: str = Field(default="", max_length=20000)
    snippet: str = Field(default="", max_length=4000)


class MessageInput(BaseModel):
    content: str = Field(min_length=1, max_length=100000)
    model: str | None = None
    attachment_ids: list[str] = Field(default_factory=list, max_length=10)
    web_sources: list[SourceReference] = Field(default_factory=list, max_length=4)


@router.get("/conversations")
def conversations(q: str = "", archived: bool = False, session: Session = Depends(get_session)):
    statement = (
        select(Conversation)
        .where(Conversation.archived == archived)
        .order_by(Conversation.updated_at.desc())
    )
    if q:
        matching_messages = select(Message.conversation_id).where(Message.content.ilike(f"%{q}%"))
        statement = statement.where(
            (Conversation.title.ilike(f"%{q}%")) | Conversation.id.in_(matching_messages)
        )
    return [record_dict(row) for row in session.scalars(statement)]


@router.post("/conversations")
def new_conversation(payload: ConversationCreate, session: Session = Depends(get_session)):
    row = Conversation(**payload.model_dump())
    session.add(row)
    session.commit()
    return record_dict(row)


@router.get("/conversations/{conversation_id}")
def read_conversation(conversation_id: str, session: Session = Depends(get_session)):
    row = session.get(Conversation, conversation_id)
    if not row:
        raise HTTPException(404, "Conversation not found")
    return {**record_dict(row), "messages": [record_dict(message) for message in row.messages]}


@router.patch("/conversations/{conversation_id}")
def update_conversation(
    conversation_id: str, payload: ConversationPatch, session: Session = Depends(get_session)
):
    row = session.get(Conversation, conversation_id)
    if not row:
        raise HTTPException(404, "Conversation not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(row, key, value)
    row.updated_at = now()
    session.commit()
    return record_dict(row)


@router.delete("/conversations/{conversation_id}")
def delete_conversation(
    conversation_id: str,
    request: Request,
    confirmed: bool = False,
    session: Session = Depends(get_session),
):
    if not confirmed:
        raise HTTPException(409, "Confirm chat deletion")
    if conversation_id in request.app.state.active_generations:
        raise HTTPException(409, "Cancel the active response before deleting this chat")
    row = session.get(Conversation, conversation_id)
    if not row:
        raise HTTPException(404, "Conversation not found")
    session.delete(row)
    session.commit()
    return {"deleted": True}


def ndjson(event: dict) -> str:
    return json.dumps(event, ensure_ascii=False) + "\n"


class ArtifactOutput(BaseModel):
    filename: str
    format: Literal["txt", "md", "csv", "xlsx", "docx", "pdf"]
    content: str = Field(min_length=1, max_length=100000)


class CriticOutput(BaseModel):
    valid: bool
    issues: list[str] = Field(default_factory=list, max_length=5)
    revised_response: str = Field(default="", max_length=100000)


class ResearchQueries(BaseModel):
    model_config = ConfigDict(extra="forbid")
    queries: list[
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    ] = Field(min_length=1, max_length=2)


def research_plan_from_queries(judgment: ResearchQueries, budget: int) -> Plan:
    """Models choose searches; application code owns IDs, dependencies, and fetch targets."""
    count = min(2, budget)
    if len(judgment.queries) != count:
        raise ValueError(f"Research needs exactly {count} focused search queries")
    if len({query.casefold() for query in judgment.queries}) != count:
        raise ValueError("Research needs distinct search queries")
    steps = [
        PlanStep(id=f"s{index}", tool="web_search", args={"query": query})
        for index, query in enumerate(judgment.queries, 1)
    ]
    if budget >= 3:
        steps.append(
            PlanStep(id="f1", tool="web_fetch", args_from="s1", depends_on=["s1"], result_index=0)
        )
    plan = Plan(steps=steps)
    validate_research_plan(plan, budget)
    return plan


def validated_citations(content: str, sources: list[dict]) -> tuple[str, list[str]]:
    import re

    allowed = {source.get("url", "").rstrip("/") for source in sources}
    removed = []

    def replace(match):
        label, url = match.group(1), match.group(2)
        if url.rstrip("/") not in allowed:
            removed.append(url)
            return label
        return match.group(0)

    return re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", replace, content), removed


def combine_research_sources(selected: list[dict], result: dict, budget: int) -> dict:
    """Keep selected pages in the bounded research evidence and count their reads once."""
    aliases = {
        canonical_url(source.get("requested_url") or source["url"]): canonical_url(source["url"])
        for source in selected
    }
    sources: dict[str, dict] = {}
    for source in [
        *({**source, "fetched": True} for source in selected),
        *result.get("sources", []),
    ]:
        url = canonical_url(source["url"])
        key = aliases.get(url, url)
        if key not in sources or (source.get("fetched") and not sources[key].get("fetched")):
            sources[key] = {
                **source,
                "title": source.get("title", "")[:1000],
                "text": source.get("text", "")[:8000],
                "snippet": source.get("snippet", "")[:4000],
            }
    bounded = sorted(sources.values(), key=lambda source: not source.get("fetched", False))[:8]
    counts = result["tool_steps"]
    return {
        **result,
        "sources": bounded,
        "tool_steps": {
            **counts,
            "fetch": counts["fetch"] + len(selected),
            "total": counts["total"] + len(selected),
            "budget": budget,
        },
        "content": "\n\n".join(
            f"[{index}] {source['title']}\n{source['url']}\n{source['text'] or source['snippet']}"
            for index, source in enumerate(bounded, 1)
        )[:32000],
    }


async def stream_with_cancel(provider, model, messages, cancel_event, stream_kwargs=None):
    iterator = provider.stream(model, messages, **(stream_kwargs or {})).__aiter__()
    try:
        while True:
            token_task = asyncio.create_task(anext(iterator))
            cancel_task = asyncio.create_task(cancel_event.wait())
            try:
                done, _ = await asyncio.wait(
                    {token_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED
                )
                if cancel_task in done and cancel_event.is_set():
                    token_task.cancel()
                    await asyncio.gather(token_task, return_exceptions=True)
                    raise asyncio.CancelledError
                try:
                    yield token_task.result()
                except StopAsyncIteration:
                    break
            finally:
                cancel_task.cancel()
                if not token_task.done():
                    token_task.cancel()
                await asyncio.gather(token_task, cancel_task, return_exceptions=True)
    finally:
        if hasattr(iterator, "aclose"):
            await iterator.aclose()


@dataclass(frozen=True)
class AnswerReset:
    detail: str = "Retrying with a direct answer to your latest request."


def answer_prose(content: str) -> str:
    """Mask Markdown code and links while preserving offsets for streaming validation."""
    masked = []
    fence = None
    for line in content.splitlines(keepends=True):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)", line)
        if fence:
            if (
                marker
                and marker[1][0] == fence[0]
                and len(marker[1]) >= fence[1]
                and not marker[2].strip()
            ):
                fence = None
            masked.append(re.sub(r"[^\r\n]", " ", line))
        elif marker:
            fence = (marker[1][0], len(marker[1]))
            masked.append(re.sub(r"[^\r\n]", " ", line))
        else:
            masked.append(line)
    prose = "".join(masked)

    def hide(match):
        return re.sub(r"[^\r\n]", " ", match[0])

    prose = re.sub(r"(`+).*?(?:\1(?!`)|\Z)", hide, prose, flags=re.DOTALL)
    return re.sub(r"\[[^\]\n]*\](?:\([^\n]*?\)|\[[^\]\n]*\])", hide, prose)


def unsupported_answer_protocol(prose: str, *, final: bool = False) -> bool:
    if "<|tool_call" in prose:
        return True
    invocation = re.compile(
        r"\[\s*(?:(?:functions|tools)\.)?(?:read|read_file|file_retrieve|write|write_file|file_create|file_edit|list_files|web_search|web_fetch|search|fetch|gmail_read|gmail_send|calendar_read|image_generate)\s*\([\s\S]*?\)\s*\](?![\[(])",
        re.IGNORECASE,
    )
    # At the end of an unfinished stream, wait for a possible Markdown link destination.
    return any(final or match.end() < len(prose) for match in invocation.finditer(prose))


def safe_answer_cut(prose: str, emitted: int) -> int:
    cut = len(prose)
    brackets = []
    for offset in range(emitted, len(prose)):
        if prose[offset] == "[":
            brackets.append(offset)
        elif prose[offset] == "]" and brackets:
            opening = brackets.pop()
            if offset == len(prose) - 1:
                cut = min(cut, opening)
    if brackets:
        cut = min(cut, brackets[0])
    opening = prose.rfind("<", emitted)
    if opening >= 0 and "<|tool_call".startswith(prose[opening:]):
        cut = min(cut, opening)
    return cut


def repair_answer_context(settings, context: list[dict]) -> list[dict]:
    """Replace the latest request with a bounded repair instruction, preserving its images."""
    instruction = "Your attempted response requested an unavailable tool. Answer only the latest user request below, honoring its format and length constraints. Earlier requests and completed artifacts are historical context; do not continue them unless requested here. Supplied evidence has already been read. Do not call tools, emit bracket tool invocations, or claim new actions. If needed evidence is absent, say so.\n\nLATEST USER REQUEST:\n"
    budget = input_budget(settings)
    latest_index = next(
        (index for index in range(len(context) - 1, -1, -1) if context[index]["role"] == "user"),
        None,
    )
    latest = (
        dict(context[latest_index]) if latest_index is not None else {"role": "user", "content": ""}
    )
    systems = []
    system_remaining = budget - min(512, budget // 2)
    for message in context:
        if message["role"] == "system" and system_remaining > 6:
            bounded = {**message, "content": clip(message["content"], system_remaining - 6)}
            systems.append(bounded)
            system_remaining -= approximate_tokens(bounded["content"]) + 6
    remaining = budget - sum(approximate_tokens(message["content"]) + 6 for message in systems)
    latest["content"] = instruction + clip(
        latest["content"], remaining - approximate_tokens(instruction) - 8
    )
    remaining -= approximate_tokens(latest["content"]) + 6
    history = []
    for message in reversed(context[:latest_index] if latest_index is not None else []):
        if message["role"] == "system":
            continue
        used = approximate_tokens(message["content"]) + 6
        if used > remaining:
            break
        history.append(message)
        remaining -= used
    return [*systems, *reversed(history), latest]


async def answer_stream(
    app, model, context, cancel_event, traces, *, stream_kwargs=None, settings=None
):
    """Validate throughout genuine streaming and replace a rejected attempt once."""

    for attempt in range(2):
        if stream_kwargs is not None:
            traces.append({"answer_attempt": attempt + 1})
        content = ""
        emitted = 0
        unsupported = False
        stream = stream_with_cancel(app.state.llm, model, context, cancel_event, stream_kwargs)
        try:
            async for token in stream:
                content += token
                if len(content) > 200000:
                    raise RuntimeError("Response exceeded the output size limit")
                prose = answer_prose(content)
                if unsupported_answer_protocol(prose):
                    unsupported = True
                    break
                cut = safe_answer_cut(prose, emitted)
                if cut > emitted:
                    yield content[emitted:cut]
                    emitted = cut
            if not unsupported:
                unsupported = unsupported_answer_protocol(answer_prose(content), final=True)
                if not unsupported and emitted < len(content):
                    yield content[emitted:]
        except UnsupportedToolCall:
            unsupported = True
        finally:
            await stream.aclose()
        if not unsupported:
            return
        traces.append({"validation": "unsupported_tool_protocol", "retry": attempt + 1})
        yield AnswerReset()
        if attempt == 1:
            raise RuntimeError(
                "The local model returned a tool call instead of an answer after one retry. Try another model or ask a more specific question about the supplied excerpt."
            )
        context = repair_answer_context(settings or app.state.settings(), context)


async def generate_response(
    app, conversation_id: str, payload: MessageInput, regenerate: bool = False
):
    started = time.monotonic()
    settings = app.state.settings()
    model = payload.model or settings.roles["primary_chat"]
    route = route_prompt(payload.content, payload.attachment_ids)
    if payload.web_sources and route.intent != "web_research":
        route = Route(intent="normal_chat", complexity="tool", tools_needed=["web_fetch"])
    assistant_id = None
    run_id = None
    result_content = ""
    final_status = "interrupted"
    traces: list[dict] = [{"route": route.model_dump()}]
    measurements: list[dict] = []
    first_token_ms = None
    tool_attempts = 0
    query_vector = None
    retrieval_completed = False
    cancel_event = asyncio.Event()
    if conversation_id in app.state.active_generations:
        yield ndjson({"type": "error", "error": "This chat already has an active response"})
        return
    measurement_scope = provider_observations.set(measurements)
    app.state.active_generations[conversation_id] = cancel_event
    app.state.generation_tasks[conversation_id] = asyncio.current_task()
    try:
        yield ndjson({"type": "status", "stage": "routing", "detail": "Selecting workflow"})
        with app.state.database.session() as session:
            conversation = session.get(Conversation, conversation_id)
            if not conversation:
                yield ndjson({"type": "error", "error": "Conversation no longer exists"})
                return
            if not regenerate:
                user = Message(
                    conversation_id=conversation_id,
                    role="user",
                    content=payload.content,
                    attachment_ids=payload.attachment_ids,
                    traces=[
                        {
                            "web_sources": [
                                {"url": source.url, "title": source.title}
                                for source in payload.web_sources
                            ]
                        }
                    ]
                    if payload.web_sources
                    else [],
                )
                session.add(user)
                session.flush()
            else:
                user = session.scalar(
                    select(Message)
                    .where(Message.conversation_id == conversation_id, Message.role == "user")
                    .order_by(Message.created_at.desc())
                )
            assistant = Message(
                conversation_id=conversation_id,
                role="assistant",
                content="",
                model=model or None,
                status="generating",
                traces=traces,
            )
            session.add(assistant)
            session.flush()
            assistant_id = assistant.id
            run = AgentRun(
                conversation_id=conversation_id,
                message_id=assistant.id,
                route=route.intent,
                model=model or None,
            )
            session.add(run)
            session.flush()
            run_id = run.id
            if regenerate:
                session.add(
                    FrictionEvent(
                        run_id=run.id,
                        kind="regeneration",
                        details=payload.content[:1000],
                        regression={"input": payload.content, "expected_route": route.intent},
                    )
                )
            if conversation.title == "New chat":
                conversation.title = " ".join(payload.content.split())[:70] or "Chat"
            conversation.updated_at, conversation.archived = now(), False
            session.commit()
            user_id = user.id
            query_vector = await embed_query(app, payload.content)
            # Recent conversation attachment references stay active on subsequent questions.
            messages = list(
                session.scalars(
                    select(Message)
                    .where(
                        Message.conversation_id == conversation_id,
                        Message.id != assistant.id,
                        Message.status == "complete",
                    )
                    .order_by(Message.created_at)
                )
            )
            if regenerate:
                user_index = next(
                    index for index, message in enumerate(messages) if message.id == user.id
                )
                messages = messages[: user_index + 1]
            active_ids = list(
                dict.fromkeys(
                    payload.attachment_ids
                    or [file_id for msg in messages[-4:] for file_id in msg.attachment_ids]
                )
            )
            if active_ids and route.intent == "normal_chat":
                route = route_prompt(payload.content, active_ids)
                run.route = route.intent
            memories = search_memory(
                session,
                payload.content,
                settings.retrieval_count,
                conversation_id,
                embedding=query_vector,
            )
            file_context = retrieve_files(
                session, active_ids, payload.content, embedding=query_vector
            )
            retrieval_completed = True
            files = (
                list(session.scalars(select(Attachment).where(Attachment.id.in_(active_ids))))
                if active_ids
                else []
            )
            history = [{"role": row.role, "content": row.content} for row in messages[-40:]]
            summary = conversation.summary
            assistant.memory_ids = [memory["id"] for memory in memories]
            assistant.attachment_ids = active_ids
            session.commit()
        if not payload.web_sources:
            route = await classify_ambiguous(app, payload.content, route)
        traces = [{"route": route.model_dump()}]
        with app.state.database.session() as session:
            run = session.get(AgentRun, run_id)
            run.route = route.intent
            session.commit()
        yield ndjson(
            {
                "type": "status",
                "stage": "context",
                "detail": "Gathering relevant context",
                "memories": memories,
                "files": [
                    {key: item[key] for key in ("file_id", "filename", "location", "page")}
                    for item in file_context
                ],
            }
        )
        evidence = ""
        web_sources = []
        fetched = []
        selected_sources = list(
            {canonical_url(source.url): source for source in payload.web_sources}.values()
        )
        research_budget = settings.max_steps - len(selected_sources)
        direct_content = None
        if selected_sources:
            if len(selected_sources) > settings.max_steps:
                raise ValueError(
                    f"Reading {len(selected_sources)} selected sources exceeds the {settings.max_steps}-step tool budget. Select fewer sources or increase max steps in Settings."
                )
            if route.intent == "web_research" and research_budget < 1:
                raise ValueError(
                    "Selected sources leave no tool budget for research searches. Select fewer sources or increase max steps in Settings."
                )
            yield ndjson(
                {
                    "type": "status",
                    "stage": "reading_sources",
                    "detail": f"Reading {len(selected_sources)} selected sources",
                }
            )
            async with asyncio.timeout(60):
                tool_attempts += len(selected_sources)
                fetched = await asyncio.gather(
                    *(
                        app.state.integration_services.web.fetch(source.url)
                        for source in selected_sources
                    )
                )
            web_sources = [{"url": item["url"], "title": item.get("title", "")} for item in fetched]
            evidence = json.dumps(fetched, ensure_ascii=False)
            traces.append({"tool": "web_fetch", "result": {"sources": web_sources}})
        if (
            route.intent == "system_help"
            and "delete" in payload.content.lower()
            and any(word in payload.content.lower() for word in ("email", "mail"))
        ):
            direct_content = "Email deletion is not supported by the current Gmail scopes. Open Gmail to delete the email."
        elif route.intent == "file_edit":
            yield ndjson(
                {"type": "status", "stage": "file_edit", "detail": "Preparing a file edit proposal"}
            )
            async with asyncio.timeout(120):
                proposal = await propose_chat_edit(app, active_ids, payload.content)
            direct_content = proposal["content"]
            traces.append({"tool": "file_edit", "result": proposal})
        elif route.intent == "memory_write":
            memory_text = payload.content.removeprefix("/remember").strip()
            if memory_text.lower().startswith("remember"):
                memory_text = memory_text[8:].lstrip(" :")
            with app.state.database.session() as session:
                saved = create_memory(
                    session,
                    MemoryInput(
                        text=memory_text or payload.content,
                        source_conversation_id=conversation_id,
                        source_message_id=user_id,
                    ),
                )
            direct_content = f"Saved memory: {saved.text}"
        elif route.intent.startswith(("web_", "image_", "gmail_", "calendar_")):
            yield ndjson(
                {
                    "type": "status",
                    "stage": route.intent,
                    "detail": route.intent.replace("_", " ").capitalize(),
                }
            )
            services = getattr(app.state, "integration_services", None)
            if services is None:
                raise RuntimeError("Integration provider is not available")
            integration_timeout = (
                app.state.tool_registry.tools["image_generate"].timeout
                if route.intent == "image_generate"
                else 180
            )
            async with asyncio.timeout(integration_timeout):
                if route.intent == "web_research" and model:
                    async with asyncio.timeout(90), app.state.model_queue.lock:
                        query_count = min(2, research_budget)
                        plan_messages = [
                            {
                                "role": "system",
                                "content": f"Choose exactly {query_count} distinct focused web search queries for the user's research request. Return only JSON with a queries list. No tools, steps, dependencies, URLs, or explanation.\n"
                                + json.dumps(
                                    {
                                        "queries": [
                                            f"focused query {index + 1}"
                                            for index in range(query_count)
                                        ]
                                    }
                                ),
                            },
                            {"role": "user", "content": payload.content},
                        ]
                        for plan_attempt in range(2):
                            returned_query_count = None
                            try:
                                judgment = await app.state.llm.structured(
                                    settings.roles["planner"] or model,
                                    plan_messages,
                                    ResearchQueries,
                                    validation_retries=0,
                                    num_predict=512,
                                )
                                returned_query_count = len(judgment.queries)
                                plan = research_plan_from_queries(judgment, research_budget)
                                break
                            except (ValueError, RuntimeError) as exc:
                                traces.append(
                                    {
                                        "validation": "research_plan_error",
                                        "retry": plan_attempt,
                                        "error": str(exc)[:500],
                                        **(
                                            {"query_count": returned_query_count}
                                            if returned_query_count is not None
                                            else {}
                                        ),
                                    }
                                )
                                if plan_attempt == 1:
                                    raise
                                plan_messages.append(
                                    {
                                        "role": "user",
                                        "content": f"Correct the queries once. {str(exc)[:500]}. Return exactly {query_count} distinct focused queries in the queries list.",
                                    }
                                )
                    traces.append({"plan": plan.model_dump()})
                    progress: asyncio.Queue[dict] = asyncio.Queue()

                    def on_step(step, phase, result):
                        nonlocal tool_attempts
                        if phase == "running":
                            tool_attempts += 1
                        progress.put_nowait(
                            {
                                "type": "status",
                                "stage": step.tool,
                                "detail": f"{step.tool.replace('_', ' ').capitalize()} · {phase}",
                                "step_id": step.id,
                            }
                        )

                    research_task = asyncio.create_task(
                        run_research(plan, app.state.tool_registry, research_budget, on_step)
                    )
                    waiter = None
                    try:
                        while not research_task.done() or not progress.empty():
                            waiter = asyncio.create_task(progress.get())
                            done, _ = await asyncio.wait(
                                {research_task, waiter}, return_when=asyncio.FIRST_COMPLETED
                            )
                            if waiter in done:
                                yield ndjson(waiter.result())
                            else:
                                waiter.cancel()
                                await asyncio.gather(waiter, return_exceptions=True)
                        tool_result = research_task.result()
                    finally:
                        if waiter and not waiter.done():
                            waiter.cancel()
                        if not research_task.done():
                            research_task.cancel()
                        await asyncio.gather(
                            research_task, *([waiter] if waiter else []), return_exceptions=True
                        )
                elif route.intent == "web_research":
                    result = await services.web.research(
                        [payload.content], limit=4, max_steps=research_budget
                    )
                    tool_result = {
                        "content": result["context"],
                        "sources": result["sources"],
                        "tool_steps": result["tool_steps"],
                    }
                elif route.intent in {
                    "gmail_search",
                    "gmail_read",
                    "gmail_send",
                    "gmail_draft",
                    "calendar_create",
                    "calendar_update",
                    "calendar_delete",
                    "calendar_read",
                }:
                    tool_result = await google_chat_action(app, route.intent, payload.content)
                else:
                    tool_result = await services.chat_context(route.intent, payload.content)
            if route.intent == "web_research" and fetched:
                tool_result = combine_research_sources(fetched, tool_result, settings.max_steps)
            traces.append({"tool": route.intent, "result": tool_result})
            web_sources = tool_result.get("sources", [])
            if (
                route.intent == "image_generate"
                or tool_result.get("proposal")
                or tool_result.get("approval")
            ):
                direct_content = (
                    tool_result.get("content")
                    or "Review the proposed action in the confirmation card."
                )
            else:
                evidence = json.dumps(tool_result, ensure_ascii=False)
        elif route.intent == "file_create":
            if not model:
                raise RuntimeError("Select an installed local model to generate file content")
            yield ndjson(
                {
                    "type": "status",
                    "stage": "file_create",
                    "detail": "Preparing and validating the file",
                }
            )
            async with asyncio.timeout(120), app.state.model_queue.lock:
                artifact = await app.state.llm.structured(
                    model,
                    [
                        {
                            "role": "system",
                            "content": "Create the requested artifact as structured JSON. For xlsx/csv return CSV text. Use supported output formats only. Filename must be a simple basename. Never output executable code for artifact creation.",
                        },
                        {"role": "user", "content": payload.content},
                    ],
                    ArtifactOutput,
                )
            with app.state.database.session() as session:
                created = write_generated(
                    session, app.state.data_dir, FileCreate(**artifact.model_dump())
                )
            direct_content = f"Created [{created.filename}](/api/files/{created.id}/content)."
            traces.append({"tool": "file_create", "file": record_dict(created)})
        if cancel_event.is_set():
            raise asyncio.CancelledError
        if direct_content is not None:
            result_content = direct_content
            yield ndjson({"type": "token", "content": direct_content})
        else:
            if not model:
                raise RuntimeError(
                    "No local chat model selected. Open Settings, select an installed model, or run ollama pull hf.co/LiquidAI/LFM2.5-2.6B-GGUF:Q4_K_M"
                )
            context, allocations = build_context(
                settings, history, summary, memories, file_context, evidence
            )
            traces.append(
                {
                    "context_allocation": allocations,
                    "memory_ids": [item["id"] for item in memories],
                    "document_chunk_ids": [item["id"] for item in file_context],
                }
            )
            image_files = [file for file in files if file.extension in IMAGE_EXTENSIONS]
            if image_files:
                vision_model = settings.roles["vision"] or model
                discovery = await app.state.llm.models()
                capability: dict = next(
                    (item for item in discovery["models"] if item["name"] == vision_model), {}
                )
                if "vision" not in capability.get("capabilities", []):
                    raise RuntimeError(
                        "Attached image requires an installed vision model assigned in Settings"
                    )
                model = vision_model
                from pathlib import Path

                context[-1]["images"] = [
                    base64.b64encode(Path(file.path).read_bytes()).decode("ascii")
                    for file in image_files
                ]
            yield ndjson(
                {"type": "status", "stage": "generating", "detail": f"Generating with {model}"}
            )
            async with asyncio.timeout(180), app.state.model_queue.lock:
                async for token in answer_stream(app, model, context, cancel_event, traces):
                    if isinstance(token, AnswerReset):
                        result_content = ""
                        yield ndjson({"type": "reset", "detail": token.detail})
                        continue
                    result_content += token
                    if first_token_ms is None and token.strip():
                        first_token_ms = int((time.monotonic() - started) * 1000)
                    yield ndjson({"type": "token", "content": token})
                    if len(result_content) > 200000:
                        raise RuntimeError("Response exceeded the output size limit")
            if web_sources:
                result_content, removed = validated_citations(result_content, web_sources)
                if removed:
                    traces.append({"validation": "removed_unretrieved_citations", "urls": removed})
                    with app.state.database.session() as session:
                        session.add(
                            FrictionEvent(
                                run_id=run_id,
                                kind="citation_validation",
                                details=json.dumps(removed),
                            )
                        )
                        session.commit()
            if route.complexity == "complex" and settings.critic_enabled:
                yield ndjson(
                    {
                        "type": "status",
                        "stage": "validating",
                        "detail": "Checking answer against evidence",
                    }
                )
                async with asyncio.timeout(90), app.state.model_queue.lock:
                    critique = await app.state.llm.structured(
                        settings.roles["critic"] or model,
                        [
                            {
                                "role": "system",
                                "content": "Perform one bounded factual validation against supplied evidence. Do not invent facts or sources. If valid set valid=true and revised_response empty. If unsupported claims need repair, return a concise revised response using only evidence.",
                            },
                            {
                                "role": "user",
                                "content": f"Evidence: {evidence[:14000]}\nAnswer: {result_content[:16000]}",
                            },
                        ],
                        CriticOutput,
                    )
                traces.append({"critic": {"valid": critique.valid, "issues": critique.issues}})
                if not critique.valid and critique.revised_response:
                    result_content = (
                        validated_citations(critique.revised_response, web_sources)[0]
                        if web_sources
                        else critique.revised_response
                    )
        if not result_content.strip():
            raise RuntimeError("The local model returned no answer. Check the model and try again.")
        final_status = "complete"
    except asyncio.CancelledError:
        final_status = "interrupted"
        # StreamingResponse propagates disconnect cancellation; persistence runs in finally.
    except Exception as exc:
        final_status = "error"
        error_text = str(exc)[:2000]
        traces.append({"error": error_text})
        if not result_content:
            result_content = error_text
        with app.state.database.session() as session:
            session.add(
                FrictionEvent(
                    run_id=run_id,
                    kind="response_error",
                    details=error_text,
                    regression={"input": payload.content, "expected_route": route.intent},
                )
            )
            session.commit()
        yield ndjson({"type": "error", "error": error_text})
    finally:
        app.state.active_generations.pop(conversation_id, None)
        app.state.generation_tasks.pop(conversation_id, None)
        try:
            if assistant_id:
                with app.state.database.session() as session:
                    assistant = session.get(Message, assistant_id)
                    run = session.get(AgentRun, run_id)
                    if assistant:
                        assistant.content, assistant.status, assistant.traces, assistant.model = (
                            result_content,
                            final_status,
                            traces,
                            model or None,
                        )
                    if run:
                        run.status, run.finished_at = final_status, now()
                        run.latency_ms = int((time.monotonic() - started) * 1000)
                        run.evidence = {
                            "traces": traces,
                            "metrics": run_metrics(
                                settings,
                                traces,
                                result_content,
                                measurements,
                                first_token_ms=first_token_ms,
                                tool_attempts=tool_attempts,
                                retrieval_fallback=bool(
                                    settings.roles["embedding"]
                                    and query_vector is None
                                    and retrieval_completed
                                ),
                            ),
                        }
                    session.commit()
        finally:
            provider_observations.reset(measurement_scope)
    if assistant_id:
        with app.state.database.session() as session:
            assistant = session.get(Message, assistant_id)
            if assistant:
                yield ndjson({"type": "done", "message": record_dict(assistant)})
    if final_status == "complete":
        task = asyncio.create_task(compact_conversation(app, conversation_id))
        app.state.background_tasks.add(task)
        task.add_done_callback(app.state.background_tasks.discard)


@router.post("/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: str,
    payload: MessageInput,
    request: Request,
    session: Session = Depends(get_session),
):
    if not session.get(Conversation, conversation_id):
        raise HTTPException(404, "Conversation not found")
    if conversation_id in request.app.state.active_generations:
        raise HTTPException(409, "This chat already has an active response")
    for file_id in payload.attachment_ids:
        if not session.get(Attachment, file_id):
            raise HTTPException(422, f"Attachment not found: {file_id}")
    return StreamingResponse(
        generate_response(request.app, conversation_id, payload),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


class RegenerateInput(BaseModel):
    model: str | None = None


@router.post("/conversations/{conversation_id}/regenerate")
async def regenerate(
    conversation_id: str,
    request: Request,
    payload: RegenerateInput,
    session: Session = Depends(get_session),
):
    user = session.scalar(
        select(Message)
        .where(Message.conversation_id == conversation_id, Message.role == "user")
        .order_by(Message.created_at.desc())
    )
    if not user:
        raise HTTPException(404, "No user message to regenerate")
    if conversation_id in request.app.state.active_generations:
        raise HTTPException(409, "Cancel the active response first")
    message = MessageInput(
        content=user.content,
        model=payload.model,
        attachment_ids=user.attachment_ids,
        web_sources=next(
            (trace["web_sources"] for trace in user.traces if "web_sources" in trace), []
        ),
    )
    return StreamingResponse(
        generate_response(request.app, conversation_id, message, True),
        media_type="application/x-ndjson",
    )


@router.post("/conversations/{conversation_id}/cancel")
async def cancel_response(conversation_id: str, request: Request):
    event = request.app.state.active_generations.get(conversation_id)
    if event:
        event.set()
        task = request.app.state.generation_tasks.get(conversation_id)
        if task and not task.done():
            task.cancel()
    return {"cancelled": bool(event)}


class FeedbackInput(BaseModel):
    feedback: Literal["up", "down", "problem"]
    details: str = Field(default="", max_length=3000)


@router.post("/messages/{message_id}/feedback")
def feedback(message_id: str, payload: FeedbackInput, session: Session = Depends(get_session)):
    message = session.get(Message, message_id)
    if not message:
        raise HTTPException(404, "Message not found")
    message.feedback = payload.feedback
    if payload.feedback in {"down", "problem"}:
        run = session.scalar(select(AgentRun).where(AgentRun.message_id == message_id))
        session.add(
            FrictionEvent(
                run_id=run.id if run else None,
                kind="negative_feedback" if payload.feedback == "down" else "manual_problem",
                details=payload.details or message.content[:1000],
            )
        )
    session.commit()
    return {"recorded": True}
