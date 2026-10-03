"""Bounded passive statistics and redacted diagnostic records."""

import math
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from .database import AgentRun, FrictionEvent, HarnessReport, now
from .observability import runtime_versions, safe_configuration


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
        if key not in {"findings", "regression_candidates", "cases", "private_error"}
    }
    if "findings" in report:
        public["findings"] = [
            {key: value for key, value in finding.items() if key != "examples"}
            for finding in report["findings"]
        ]
        public["regression_candidate_count"] = len(report.get("regression_candidates", []))
        public["regression_candidates"] = []
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
