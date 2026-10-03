"""A read-only research DAG resolves fetched URLs from real search outputs."""

from .orchestration import Plan, ToolRegistry, execute_plan
from .providers.web import canonical_url


def validate_research_plan(plan: Plan, max_steps: int) -> None:
    plan.validate_dag({"web_search", "web_fetch"}, max_steps)
    searches = [step for step in plan.steps if step.tool == "web_search"]
    fetches = [step for step in plan.steps if step.tool == "web_fetch"]
    if not min(2, max_steps) <= len(searches) <= 4:
        raise ValueError("Research plan needs 2–4 searches within the configured tool budget")
    if max_steps >= 3 and not fetches:
        raise ValueError("Research plan must read at least one actual search result")
    identifiers = {step.id for step in searches}
    for step in fetches:
        if step.args_from not in identifiers or step.args:
            raise ValueError(
                "Fetch URLs must come from real search results, without model-provided URLs"
            )


async def run_research(plan: Plan, registry: ToolRegistry, max_steps: int, on_step=None) -> dict:
    validate_research_plan(plan, max_steps)
    outputs = await execute_plan(
        plan,
        registry,
        max_steps,
        timeout=90,
        allowed_tools={"web_search", "web_fetch"},
        continue_on_error=True,
        on_step=on_step,
    )
    results: dict[str, dict] = {}
    errors = []
    search_count = fetch_count = 0
    for step in plan.steps:
        output = outputs[step.id]
        if step.tool == "web_search":
            search_count += 1
            if isinstance(output, list):
                for source in output:
                    results.setdefault(
                        canonical_url(source["url"]), {**source, "text": "", "fetched": False}
                    )
        elif not output.get("skipped"):
            fetch_count += 1
        if isinstance(output, dict) and output.get("error"):
            errors.append({"step": step.id, "tool": step.tool, "error": output["error"]})
        if step.tool == "web_fetch" and isinstance(output, dict) and output.get("url"):
            original = str(output.get("requested_url") or output["url"])
            source = results.pop(canonical_url(original), {"snippet": ""})
            results[canonical_url(output["url"])] = {
                **source,
                "url": output["url"],
                "title": output.get("title") or source.get("title", ""),
                "text": output["text"][:8000],
                "fetched": True,
            }
    sources = sorted(results.values(), key=lambda source: not source.get("fetched", False))[:8]
    if not sources:
        raise ValueError(
            "Research produced no usable evidence. Check SearXNG and refine the query."
        )
    return {
        "sources": sources,
        "errors": errors,
        "tool_steps": {
            "search": search_count,
            "fetch": fetch_count,
            "total": search_count + fetch_count,
            "budget": max_steps,
        },
        "content": "\n\n".join(
            f"[{index}] {source['title']}\n{source['url']}\n{source['text'] or source.get('snippet', '')}"
            for index, source in enumerate(sources, 1)
        )[:32000],
    }
