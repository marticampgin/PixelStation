"""Content-free measurements scoped to one workflow; never collect model reasoning."""

import contextvars
from importlib.metadata import PackageNotFoundError, version
from platform import python_version, system
from typing import Any

from .context import approximate_tokens

provider_observations: contextvars.ContextVar[list[dict] | None] = contextvars.ContextVar(
    "pixel_station_provider_observations", default=None
)


def observe_provider(payload: dict, operation: str, model: str) -> None:
    collector = provider_observations.get()
    if collector is None:
        return
    event: dict[str, Any] = {
        "operation": operation,
        "model": model,
        "result_status": "complete",
        "done_reason": payload.get("done_reason")
        if isinstance(payload.get("done_reason"), str)
        else None,
    }
    for key in (
        "prompt_eval_count",
        "eval_count",
        "total_duration",
        "load_duration",
        "prompt_eval_duration",
        "eval_duration",
    ):
        value = payload.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            event[key] = value
    count, duration = event.get("eval_count"), event.get("eval_duration")
    event["tokens_per_second"] = (
        count * 1_000_000_000 / duration if count is not None and duration else None
    )
    event["measurement_source"] = "ollama_terminal_metadata"
    collector.append(event)


def safe_configuration(settings) -> dict:
    return {
        key: getattr(settings, key)
        for key in (
            "roles",
            "profile",
            "context_tokens",
            "bounded_response_tokens",
            "thinking_enabled",
            "retrieval_count",
            "max_steps",
            "critic_enabled",
            "auto_memory",
            "summary_turns",
            "harness_enabled",
            "harness_interval_hours",
            "harness_window_days",
            "harness_sample_limit",
        )
    }


def runtime_versions() -> dict:
    versions = {"python": python_version(), "os_family": system()}
    for name in (
        "pixel-station",
        "fastapi",
        "pydantic",
        "sqlalchemy",
        "httpx",
        "sqlite-vec",
        "docling",
    ):
        try:
            versions[name] = version(name)
        except PackageNotFoundError:
            versions[name] = "unavailable"
    return versions


def run_metrics(
    settings,
    traces: list[dict],
    public_output: str,
    observations: list[dict],
    *,
    first_token_ms=None,
    tool_attempts=0,
    retrieval_fallback=False,
) -> dict:
    context = next(
        (trace["context_allocation"] for trace in traces if "context_allocation" in trace), None
    )
    retrieval = next((trace for trace in traces if "memory_ids" in trace), {})
    counts = [
        trace.get("result", {}).get("tool_steps", {}).get("total")
        for trace in traces
        if isinstance(trace.get("result"), dict)
    ]
    measured_tool_count = sum(value for value in counts if isinstance(value, int))
    direct_tools = sum(
        "tool" in trace and not (trace.get("result") or {}).get("tool_steps")
        for trace in traces
        if not trace.get("result") or isinstance(trace.get("result"), dict)
    )
    return {
        "version": 1,
        "configuration": safe_configuration(settings),
        "repair_count": sum(
            (trace.get("validation") == "unsupported_tool_protocol" and trace.get("retry") == 1)
            or (trace.get("validation") == "research_plan_error" and trace.get("retry") == 0)
            for trace in traces
        ),
        "validation_rejections": sum(
            trace.get("validation") in {"unsupported_tool_protocol", "research_plan_error"}
            for trace in traces
        ),
        "fallback_count": int(retrieval_fallback),
        "fallback_kind": "lexical_retrieval" if retrieval_fallback else None,
        "first_public_token_ms": first_token_ms,
        "public_output_chars": len(public_output),
        "public_output_tokens_estimated": approximate_tokens(public_output) if public_output else 0,
        "context_tokens_estimated": context,
        "retrieved_memories": len(retrieval.get("memory_ids", [])),
        "retrieved_chunks": len(retrieval.get("document_chunk_ids", [])),
        "tool_steps": max(tool_attempts, measured_tool_count)
        if tool_attempts or measured_tool_count
        else None
        if direct_tools
        else 0,
        "tool_workflow_count": direct_tools,
        "tool_step_measurement": "DAG attempts or explicit provider counters"
        if tool_attempts or measured_tool_count
        else "not_available_for_direct_workflow"
        if direct_tools
        else "no_tool_workflow",
        "budget_scope": "max_steps bounds research DAG/search+fetch and selected-source reads; not a universal inference/tool quota",
        "provider_calls": observations,
    }
