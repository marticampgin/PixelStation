"""Bounded passive statistics and redacted diagnostic records."""

import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from .database import AgentRun, FrictionEvent, HarnessReport, now
from .observability import runtime_versions, safe_configuration

# Only application-controlled labels become public diagnostics. Error text,
# provider URLs, model answers, and user-supplied tool arguments are never parsed.
DIAGNOSTIC_CODES = frozenset({
    "google_not_connected", "google_reconnect", "google_access_denied", "google_api_failed",
    "google_timeout", "google_identity_unavailable", "google_scopes_missing", "keyring_unavailable",
    "approval_connection_changed", "approval_unavailable", "approval_changed", "accepted_unverified",
    "attachment_changed", "attachment_unavailable", "attachment_not_found", "attachment_too_large",
    "attachment_import_failed", "invalid_attachment", "invalid_email", "invalid_event", "event_changed",
    "search_forbidden", "search_engines_unavailable", "search_unavailable", "search_failed", "search_timeout",
    "searxng_missing", "invalid_search_response", "unsupported_content", "no_text", "redirect_limit",
    "fetch_failed", "fetch_timeout", "unsafe_url", "response_too_large", "invalid_url", "not_found",
    "comfyui_unavailable", "comfyui_failed", "image_timeout", "image_failed", "invalid_workflow",
    "unsupported_tool_protocol", "research_plan_error", "removed_unretrieved_citations",
    "invalid_poker_action", "embedding_query_error", "embedding_index_error", "memory_compaction_error",
    "adaptive_action_error", "adaptive_action_invalid", "adaptive_repeated_action", "adaptive_loop",
    "adaptive_observation_invalid", "adaptive_tool_not_allowed", "adaptive_foreign_identifier",
    "adaptive_context_limit", "adaptive_model_missing", "adaptive_timeout", "adaptive_budget_exhausted",
})
DIAGNOSTIC_TYPES = frozenset({
    "IntegrationError", "ValueError", "RuntimeError", "ValidationError", "UnsupportedToolCall",
    "TimeoutError", "ReadTimeout", "ConnectTimeout", "PoolTimeout", "HTTPStatusError",
    "ConnectError", "ConnectionError", "OSError", "PermissionError", "JSONDecodeError",
    "AdaptiveError",
})
DIAGNOSTIC_TOOLS = frozenset({
    "web_search", "web_fetch", "web_research", "file_create", "file_read", "file_edit",
    "image_generate", "gmail_search", "gmail_read", "gmail_reply", "gmail_send", "gmail_draft",
    "calendar_read", "calendar_create", "calendar_update", "calendar_delete", "poker_bot",
    "file_retrieve", "memory_query",
})
DIAGNOSTIC_STAGES = frozenset({
    "retrieval", "planning", "tool_execution", "response_generation", "response_validation",
    "feedback", "embedding_query", "embedding_index", "memory_compaction", "poker_decision",
    "adaptive_tool",
    "action_validation",
})


def failure_observations(run: AgentRun | None) -> dict:
    """Associated structured evidence; no inference from private prose or root-cause claim."""
    codes, types, tools, stages, validations = set(), set(), set(), set(), set()

    def known(value, allowed):
        return isinstance(value, str) and value in allowed

    if run:
        traces = run.evidence.get("traces", [])
        for trace in traces[:256] if isinstance(traces, list) else []:
            if not isinstance(trace, dict):
                continue
            if known(trace.get("error_code"), DIAGNOSTIC_CODES):
                codes.add(trace["error_code"])
            if known(trace.get("error_type"), DIAGNOSTIC_TYPES):
                types.add(trace["error_type"])
            if known(trace.get("stage"), DIAGNOSTIC_STAGES):
                stages.add(trace["stage"])
            if known(trace.get("validation"), DIAGNOSTIC_CODES):
                validations.add(trace["validation"])
                stages.add({"research_plan_error": "planning", "adaptive_action_error": "action_validation"}.get(trace["validation"], "response_validation"))
            if (trace.get("error") or trace.get("error_code") or trace.get("error_type")) and known(trace.get("tool"), DIAGNOSTIC_TOOLS):
                tools.add(trace["tool"])
            result = trace.get("result", {})
            errors = result.get("errors", []) if isinstance(result, dict) else []
            for error in errors[:20] if isinstance(errors, list) else []:
                if not isinstance(error, dict):
                    continue
                if known(error.get("tool"), DIAGNOSTIC_TOOLS):
                    tools.add(error["tool"])
                    stages.add("tool_execution")
                if known(error.get("code"), DIAGNOSTIC_CODES):
                    codes.add(error["code"])
    return {"error_codes": sorted(codes), "error_types": sorted(types),
            "failed_tools": sorted(tools), "stages": sorted(stages), "validations": sorted(validations)}


def pattern_guidance(kind: str, evidence: dict) -> tuple[str, str]:
    observed = set(evidence["error_codes"] + evidence["validations"])
    if observed & {"adaptive_action_error", "adaptive_action_invalid", "adaptive_tool_not_allowed",
                   "adaptive_foreign_identifier", "adaptive_repeated_action", "adaptive_loop"}:
        return ("Replay the observed task with the recorded model and inspect the next-action schema, allowed tools/identifiers and no-repeat guard. A repeated action stops without another repair.",
                "Return an invalid or repeated action; allow at most one schema repair; forbid foreign identifiers and duplicate tool execution.")
    if "unsupported_tool_protocol" in observed:
        return ("Replay a sanitized equivalent with the recorded model and check the single bounded repair, supported response protocol and replacement of partial output.",
                "Reject unsupported tool output; allow one repair; preserve supplied evidence; fail honestly if the second attempt remains invalid.")
    if "research_plan_error" in observed:
        return ("Check the bounded query schema, distinct-query validation and application-built research plan at the recorded tool budget.",
                "Use a malformed query response and verify one repair, deterministic dependencies and no invented fetch URLs.")
    if observed & {"google_not_connected", "google_reconnect", "google_access_denied", "google_scopes_missing", "keyring_unavailable"}:
        return ("Check the affected service's connection, granted scopes and enabled API, then retry a read-only request for the same account.",
                "Simulate missing/revoked credentials and verify an explicit setup error without account fallback or external writes.")
    if observed & {"approval_connection_changed", "approval_changed", "attachment_changed", "event_changed"}:
        return ("Inspect the reviewed account/artifact version. This rejection can be an expected integrity guard; prepare a fresh proposal before any retry.",
                "Change the account or reviewed bytes after proposing; confirmation must reject before sending a mutation.")
    if "accepted_unverified" in observed:
        return ("Inspect the returned artifact in Google before retrying; acceptance with failed verification must not cause a duplicate write.",
                "Simulate successful mutation followed by failed verification; preserve the accepted ID and do not automatically resubmit.")
    if observed & {"search_engines_unavailable", "search_forbidden", "search_unavailable", "search_failed", "searxng_missing", "invalid_search_response"}:
        return ("Run an actual SearXNG JSON search and inspect engine failure labels/access restrictions; distinguish a real empty result from upstream failure.",
                "Exercise forbidden, unavailable, partial-result and genuine-empty search responses with separate honest outcomes.")
    if set(evidence["error_types"]) & {"TimeoutError", "ReadTimeout", "ConnectTimeout"} or observed & {"google_timeout", "fetch_timeout", "image_timeout", "search_timeout"}:
        return ("Compare the recorded route/model latency and provider availability with a controlled warm/cold replay before changing a timeout.",
                "Use a delayed provider and verify bounded cancellation, an explicit timeout and no duplicate external write.")
    if "removed_unretrieved_citations" in observed:
        return ("Compare answer links with returned evidence URLs. This check identifies unsupported links; factual support and freshness need a separate reviewed content case.",
                "Generate a link absent from retrieved evidence; verify removal without treating surviving links as a factual-quality pass.")
    if kind in {"embedding_query_error", "embedding_index_error", "memory_compaction_error"}:
        return ("Check the configured embedding/summarizer role with a small isolated request and verify the recorded fallback or preserved source data.",
                "Fail the configured provider; retain source messages and distinguish unavailable vector retrieval from observed lexical fallback.")
    if kind == "invalid_poker_action":
        return ("Inspect legal-action validation and measured repair/fallback counts for the recorded decisions; action legality alone does not establish playing quality.",
                "Provide an illegal structured move and verify bounded repair plus a legal deterministic fallback.")
    if kind in {"manual_problem", "negative_feedback", "regeneration"}:
        return ("Review the linked local run and, only with private evidence enabled, the reported problem. Define the expected content outcome before adding a regression case.",
                "Create a sanitized reproduction with a concrete expected answer or artifact; grade content separately from workflow completion.")
    return ("Inspect the linked local run's structured evidence and reproduce the workflow. Missing error classification is unavailable evidence, not a diagnosed cause.",
            "Add a sanitized case for the observed route and status with an explicit expected outcome after reproducing the problem.")


def failure_patterns(session, events: list[FrictionEvent]) -> list[dict]:
    ids = {event.run_id for event in events if event.run_id}
    runs = {run.id: run for run in session.scalars(select(AgentRun).where(AgentRun.id.in_(ids)))} if ids else {}
    grouped = {}
    for event in events:
        run = runs.get(event.run_id)
        observed = failure_observations(run)
        identity = {"kind": event.kind, "route": run.route if run else None,
                    "model": run.model if run else None, "status": run.status if run else None, **observed}
        signature = json.dumps(identity, sort_keys=True, separators=(",", ":"))
        if signature not in grouped:
            recommendation, hint = pattern_guidance(event.kind, observed)
            grouped[signature] = {**identity, "id": "pattern-" + hashlib.sha256(signature.encode()).hexdigest()[:16],
                                  "count": 0, "run_ids": [], "event_ids": [], "run_count": 0,
                                  "first_seen": event.created_at, "last_seen": event.created_at,
                                  "classification": "structured_observations" if any(observed.values()) else "unclassified",
                                  "recommendation": recommendation, "regression_hint": hint,
                                  "private_examples": []}
        pattern = grouped[signature]
        pattern["count"] += 1
        pattern["first_seen"] = min(pattern["first_seen"], event.created_at)
        pattern["last_seen"] = max(pattern["last_seen"], event.created_at)
        if event.id not in pattern["event_ids"] and len(pattern["event_ids"]) < 20:
            pattern["event_ids"].append(event.id)
        if run and run.id not in pattern["run_ids"]:
            pattern["run_ids"].append(run.id)
        if len(pattern["private_examples"]) < 3 and event.details:
            pattern["private_examples"].append(event.details[:300])
    patterns = sorted(grouped.values(), key=lambda item: (-item["count"], item["id"]))
    for pattern in patterns:
        pattern["run_count"] = len(pattern["run_ids"])
        pattern["run_ids"] = pattern["run_ids"][:20]
        pattern["links_capped"] = pattern["count"] > len(pattern["event_ids"]) or pattern["run_count"] > len(pattern["run_ids"])
    return patterns


def distribution(values: list[float]) -> dict:
    ordered = sorted(values)

    def percentile(fraction):
        return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)] if ordered else None

    return {
        "sample_count": len(values),
        "median": percentile(0.5),
        "p95": percentile(0.95),
        "min": ordered[0] if ordered else None,
        "max": ordered[-1] if ordered else None,
        "method": "nearest_rank",
    }


def bounded_private(value, depth=0):
    if depth > 6:
        return "[depth limit]"
    if isinstance(value, str):
        return value[:4000]
    if isinstance(value, list):
        return [bounded_private(item, depth + 1) for item in value[:30]]
    if isinstance(value, dict):
        return {key: bounded_private(item, depth + 1) for key, item in list(value.items())[:60]}
    return value


def public_run(row: AgentRun, include_content=False) -> dict:
    result = {
        key: getattr(row, key)
        for key in (
            "id",
            "conversation_id",
            "message_id",
            "route",
            "model",
            "status",
            "started_at",
            "finished_at",
            "latency_ms",
        )
    }
    metrics = row.evidence.get("metrics", {})
    allowed = {
        "version",
        "timing_source",
        "configuration",
        "repair_count",
        "validation_rejections",
        "fallback_count",
        "fallback_kind",
        "fallback_used",
        "bot_actions",
        "first_public_token_ms",
        "public_output_chars",
        "public_output_tokens_estimated",
        "context_tokens_estimated",
        "retrieved_memories",
        "retrieved_chunks",
        "tool_steps",
        "tool_step_measurement",
        "tool_workflow_count",
        "budget_scope",
        "provider_calls",
        "strategy_version",
        "strategy_stage",
        "selected_action",
        "decision_basis",
        "policy_rejections",
        "provider_call_attempts",
        "raw_actions",
        "generation_tokens_per_attempt",
        "strategy_measurements",
    }
    result["metrics"] = {key: value for key, value in metrics.items() if key in allowed}
    result["metrics_available"] = bool(metrics)
    result["failure_observations"] = failure_observations(row)
    if include_content:
        result["private_local_evidence"] = bounded_private(row.evidence)
    return result


def public_report(row: HarnessReport, include_content=False) -> dict:
    if include_content:
        return {"id": row.id, "created_at": row.created_at, "report": bounded_private(row.report)}
    report = row.report
    public = {
        key: value
        for key, value in report.items()
        if key not in {"findings", "regression_candidates", "cases", "private_error", "patterns"}
    }
    if "findings" in report:
        public["findings"] = [
            {key: value for key, value in finding.items() if key != "examples"}
            for finding in report["findings"]
        ]
        public["regression_candidate_count"] = len(report.get("regression_candidates", []))
        public["regression_candidates"] = []
    if "patterns" in report:
        public["patterns"] = [
            {key: value for key, value in pattern.items() if not key.startswith("private_")}
            for pattern in report["patterns"]
        ]
    if "cases" in report:
        public["cases"] = [
            {key: value for key, value in case.items() if not key.startswith("private_")}
            for case in report["cases"]
        ]
    return {"id": row.id, "created_at": row.created_at, "report": public}


def measurements(session, settings) -> dict:
    days = settings.harness_window_days
    cap = settings.harness_sample_limit
    since = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    condition = AgentRun.started_at >= since
    total = session.scalar(select(func.count(AgentRun.id)).where(condition)) or 0
    runs = list(
        session.scalars(
            select(AgentRun).where(condition).order_by(AgentRun.started_at.desc()).limit(cap)
        )
    )
    terminal = [row for row in runs if row.finished_at]

    # Legacy/restart records can have the default zero without an observed elapsed time.
    def has_timing(row):
        return row.latency_ms > 0 or row.evidence.get("metrics", {}).get("version") == 1

    timed = [row for row in terminal if has_timing(row)]
    outcomes = Counter(row.status for row in runs)
    instrumented = [row for row in runs if row.evidence.get("metrics")]
    groups = defaultdict(list)
    for row in terminal:
        groups[(row.route, row.model)].append(row)
    provider_calls = [
        event for row in instrumented for event in row.evidence["metrics"].get("provider_calls", [])
    ]
    repairs = sum(row.evidence.get("metrics", {}).get("repair_count", 0) for row in instrumented)
    fallbacks = sum(
        row.evidence.get("metrics", {}).get(
            "fallback_count", int(row.evidence.get("metrics", {}).get("fallback_used", False))
        )
        for row in instrumented
    )
    poker_decisions = [
        row.evidence["metrics"]
        for row in instrumented
        if row.route == "poker_bot" and row.evidence["metrics"].get("selected_action")
    ]
    preflop = [item for item in poker_decisions if item.get("strategy_stage") == "preflop"]
    events = Counter(
        session.scalars(
            select(FrictionEvent.kind)
            .where(FrictionEvent.created_at >= since)
            .order_by(FrictionEvent.created_at.desc(), FrictionEvent.id.desc())
            .limit(cap)
        )
    )
    return {
        "window_days": days,
        "sample_limit": cap,
        "matching_runs": total,
        "sample_count": len(runs),
        "instrumented_runs": len(instrumented),
        "terminal_runs": len(terminal),
        "capped": total > cap,
        "outcomes": dict(outcomes),
        "completion_rate": outcomes["complete"] / len(terminal) if terminal else None,
        "repair_attempts": repairs,
        "fallbacks": fallbacks,
        "poker_strategy": {
            "sample_count": len(poker_decisions),
            "actions": dict(Counter(item["selected_action"] for item in poker_decisions)),
            "native_without_fallback": sum(
                not item.get("fallback_count") for item in poker_decisions
            ),
            "fallback_decisions": sum(bool(item.get("fallback_count")) for item in poker_decisions),
            "preflop_samples": len(preflop),
            "preflop_all_ins": sum(item["selected_action"] == "all_in" for item in preflop),
            "raw_preflop_all_in_attempts": sum(
                item.get("raw_actions", []).count("all_in") for item in preflop
            ),
            "validation_rejections": sum(
                item.get("validation_rejections", 0) for item in poker_decisions
            ),
            "note": "Newly measured decisions only; counts describe observed behavior, not poker skill or optimal frequencies.",
        },
        "latency_ms": distribution([row.latency_ms for row in timed]),
        "first_public_token_ms": distribution(
            [
                row.evidence["metrics"]["first_public_token_ms"]
                for row in instrumented
                if row.evidence["metrics"].get("first_public_token_ms") is not None
            ]
        ),
        "groups": [
            {
                "route": route,
                "model": model,
                "sample_count": sum(has_timing(row) for row in items),
                "recorded_runs": len(items),
                "outcomes": dict(Counter(row.status for row in items)),
                "latency_ms": distribution([row.latency_ms for row in items if has_timing(row)]),
            }
            for (route, model), items in groups.items()
        ],
        "native_usage": {
            "terminal_metadata_samples": len(provider_calls),
            "prompt_token_samples": sum("prompt_eval_count" in event for event in provider_calls),
            "generation_token_samples": sum("eval_count" in event for event in provider_calls),
            "prompt_tokens": sum(event.get("prompt_eval_count", 0) for event in provider_calls),
            "generated_tokens": sum(event.get("eval_count", 0) for event in provider_calls),
            "tokens_per_second": distribution(
                [
                    event["tokens_per_second"]
                    for event in provider_calls
                    if event.get("tokens_per_second") is not None
                ]
            ),
            "duration_unit": "nanoseconds",
            "note": "Ollama generated token counters include any private reasoning; reasoning text is excluded. Missing metadata is not zero usage.",
        },
        "budgets": [
            {
                "id": row.id,
                "route": row.route,
                "model": row.model,
                **{
                    key: value
                    for key, value in public_run(row)["metrics"].items()
                    if key
                    in {
                        "configuration",
                        "context_tokens_estimated",
                        "retrieved_memories",
                        "retrieved_chunks",
                        "public_output_tokens_estimated",
                        "generation_tokens_per_attempt",
                        "tool_steps",
                        "budget_scope",
                    }
                },
            }
            for row in instrumented[:30]
        ],
        "problem_counts": dict(events),
        "recent_problem_runs": [
            public_run(row)
            for row in runs
            if row.status in {"error", "interrupted"}
            or row.evidence.get("metrics", {}).get("repair_count")
            or row.evidence.get("metrics", {}).get("fallback_count")
        ][:20],
        "interpretation": "Completion is a workflow outcome, not an answer-correctness grade. Percentiles describe this capped recent sample; small samples and mixed cold/warm loads are not tuned benchmarks.",
    }


def diagnostic_bundle(app, session, include_content=False) -> dict:
    settings = app.state.settings()
    cap = settings.harness_sample_limit
    events = list(
        session.scalars(select(FrictionEvent).order_by(FrictionEvent.created_at.desc()).limit(cap))
    )
    cached = getattr(app.state.llm, "_cache", {}).get(settings.ollama_url)
    return {
        "schema_version": 1,
        "created_at": now(),
        "content_included": include_content,
        "telemetry": "none; local diagnostic export",
        "configuration": safe_configuration(settings),
        "versions": runtime_versions(),
        "model_discovery_snapshot": cached[1].get("models", []) if cached else [],
        "passive": measurements(session, settings),
        "runs": [
            public_run(row, include_content)
            for row in session.scalars(
                select(AgentRun).order_by(AgentRun.started_at.desc()).limit(cap)
            )
        ],
        "events": [
            {
                "id": row.id,
                "run_id": row.run_id,
                "kind": row.kind,
                "created_at": row.created_at,
                **(
                    {
                        "private_details": row.details[:2000],
                        "private_regression": bounded_private(row.regression),
                    }
                    if include_content
                    else {}
                ),
            }
            for row in events
        ],
        "reports": [
            public_report(row, include_content)
            for row in session.scalars(
                select(HarnessReport).order_by(HarnessReport.created_at.desc()).limit(20)
            )
        ],
        "privacy": "Default exports exclude prompts, answers, filenames, URLs, source bodies and raw traces. IDs/model names/configuration remain for correlation. Opt-in includes bounded private local evidence; review before sharing.",
    }
