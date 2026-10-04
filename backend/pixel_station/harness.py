import asyncio
import json
from collections import Counter
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import (
    AgentRun,
    FrictionEvent,
    HarnessReport,
    ScheduledJob,
    get_session,
    now,
)
from .observability import runtime_versions, safe_configuration
from .watchtower import diagnostic_bundle, failure_patterns, measurements, public_report, public_run

router = APIRouter(prefix="/api/harness", tags=["harness"])
SUGGESTIONS = {
    "response_error": "Review provider setup, route arguments, and timeouts; replay the failed input.",
    "regeneration": "Compare the original answer with the repeated request; assess context quality.",
    "negative_feedback": "Review the reported answer and add a targeted regression case.",
    "memory_compaction_error": "Verify summarizer role and JSON-schema support; preserve source messages.",
    "invalid_poker_action": "Review the legal-action schema and retry/fallback policy.",
    "manual_problem": "Review the user report and reproduce the affected workflow.",
}


def create_report(session: Session, settings=None) -> HarnessReport:
    days = settings.harness_window_days if settings else 7
    since = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    events = list(
        session.scalars(
            select(FrictionEvent)
            .where(FrictionEvent.created_at >= since)
            .order_by(FrictionEvent.created_at.desc())
            .limit(settings.harness_sample_limit if settings else 500)
        )
    )
    counts = Counter(event.kind for event in events)
    patterns = failure_patterns(session, events)
    report = {
        "kind": "passive",
        "window_days": days,
        "event_count": len(events),
        "patterns": patterns[:100],
        "pattern_count": len(patterns),
        "patterns_capped": len(patterns) > 100,
        "pattern_method": "Exact grouping by recorded kind, route, model, status and allowlisted structured error/validation/tool/stage observations. Raw error text is not parsed; unknown legacy errors remain unclassified.",
        "interpretation": "Repeated observations help choose a reproduction and regression case; they do not establish root cause or answer correctness. Counts describe the capped event window, not independent failed requests.",
        "findings": [
            {
                "kind": kind,
                "count": count,
                "proposal": SUGGESTIONS.get(
                    kind, "Inspect traces and reproduce the failure before proposing a patch."
                ),
                "examples": [event.details[:300] for event in events if event.kind == kind][:3],
            }
            for kind, count in counts.most_common()
        ],
        "regression_candidates": [event.regression for event in events if event.regression][:30],
        "patching_enabled": False,
    }
    if settings:
        report["metrics"] = measurements(session, settings)
    row = HarnessReport(report=report)
    session.add(row)
    session.commit()
    return row


@router.get("")
def inspect_harness(
    request: Request, include_content: bool = False, session: Session = Depends(get_session)
):
    return {
        "reports": [
            public_report(row, include_content)
            for row in session.scalars(
                select(HarnessReport).order_by(HarnessReport.created_at.desc()).limit(20)
            )
        ],
        "events": [
            {
                "id": row.id,
                "run_id": row.run_id,
                "kind": row.kind,
                "created_at": row.created_at,
                **(
                    {"details": row.details[:2000], "regression": row.regression}
                    if include_content
                    else {}
                ),
            }
            for row in session.scalars(
                select(FrictionEvent).order_by(FrictionEvent.created_at.desc()).limit(100)
            )
        ],
        "runs": [
            public_run(row, include_content)
            for row in session.scalars(
                select(AgentRun).order_by(AgentRun.started_at.desc()).limit(30)
            )
        ],
        "metrics": measurements(session, request.app.state.settings()),
        "defaults": {
            "max_steps": {
                "default": 6,
                "reason": "Conservative bound for a few research searches and fetched pages; not empirically tuned and not a universal tool/LLM quota.",
            },
            "retrieval_count": {
                "default": 4,
                "reason": "Conservative cap limiting competing memories and context cost; not empirically tuned. Relevance is query-dependent.",
            },
        },
    }


@router.post("/run")
def run_harness(request: Request, session: Session = Depends(get_session)):
    return public_report(create_report(session, request.app.state.settings()))


@router.get("/runs/{identity}")
def inspect_run(identity: str, include_content: bool = False, session: Session = Depends(get_session)):
    row = session.get(AgentRun, identity)
    if not row:
        raise HTTPException(404, "Recorded run not found")
    return public_run(row, include_content)


@router.get("/regressions")
def regressions(include_content: bool = False, session: Session = Depends(get_session)):
    return [
        row.regression
        if include_content
        else {
            "event_id": row.id,
            "kind": row.kind,
            "expected_route": row.regression.get("expected_route"),
        }
        for row in session.scalars(select(FrictionEvent).where(FrictionEvent.regression != {}))
    ]


@router.get("/diagnostics")
def diagnostics(
    request: Request, include_content: bool = False, session: Session = Depends(get_session)
):
    return Response(
        json.dumps(diagnostic_bundle(request.app, session, include_content), indent=2),
        media_type="application/json",
        headers={
            "Content-Disposition": 'attachment; filename="pixel-station-diagnostics.json"',
            "Cache-Control": "no-store",
        },
    )


def record_observation(
    app, *, route: str, model: str | None, status: str, latency_ms: int, metrics: dict
) -> None:
    """Record real non-chat workflow outcomes; caller must exclude content/cards/reasoning."""
    if latency_ms < 0:
        raise ValueError("Observed latency cannot be negative")
    finished = datetime.now(UTC)
    with app.state.database.session() as session:
        session.add(
            AgentRun(
                route=route,
                model=model,
                status=status,
                started_at=(finished - timedelta(milliseconds=latency_ms)).isoformat(),
                finished_at=finished.isoformat(),
                latency_ms=latency_ms,
                evidence={
                    "metrics": {
                        "version": 1,
                        "configuration": safe_configuration(app.state.settings()),
                        "timing_source": "elapsed_monotonic; start_timestamp_derived_from_finish",
                        **metrics,
                    }
                },
            )
        )
        session.commit()


class EvaluationRequest(BaseModel):
    native: bool = False
    poker_native: bool = False


def baseline_compatible(previous: dict, current: dict) -> bool:
    from .evaluations import known_native_identity

    if previous.get("kind") != "evaluation" or previous.get("status") != "COMPLETE":
        return False
    if any(
        previous.get(key) != current.get(key)
        for key in (
            "fixture",
            "runner_version",
            "native_requested",
            "poker_native_requested",
            "configuration",
            "versions",
        )
    ):
        return False
    if current.get("native_requested") or current.get("poker_native_requested"):
        return (
            known_native_identity(current.get("native_runtime"))
            and known_native_identity(previous.get("native_runtime"))
            and current["native_runtime"] == previous["native_runtime"]
        )
    return True


def recover_evaluations(app) -> None:
    with app.state.database.session() as session:
        for row in session.scalars(select(HarnessReport)):
            if row.report.get("kind") == "evaluation" and row.report.get("status") == "RUNNING":
                row.report = {**row.report, "status": "INTERRUPTED", "finished_at": now()}
        session.commit()


async def execute_evaluation(
    app, identity: str, native: bool, settings, poker_native=False
) -> None:
    from .evaluations import deterministic_cases, native_cases
    from .poker_evaluations import (
        deterministic_poker_cases,
        native_poker_cases,
        skipped_poker_cases,
    )

    try:
        cases = await asyncio.to_thread(deterministic_cases)
        cases += await asyncio.to_thread(deterministic_poker_cases)
        with app.state.database.session() as session:
            row = session.get(HarnessReport, identity)
            row.report = {**row.report, "cases": cases}
            session.commit()
        if native:
            cases += await native_cases(app, settings)
        else:
            cases += [
                {
                    "id": identifier,
                    "label": label,
                    "scope": "native_model",
                    "critical": True,
                    "status": "SKIP",
                    "reason": "Native probe was not requested; fixture gates do not establish model correctness.",
                }
                for identifier, label in (
                    ("native_plan", "Native latest-request plan"),
                    ("native_file_qa", "Native supplied-file answer"),
                )
            ]
        cases += await native_poker_cases(app, settings) if poker_native else skipped_poker_cases()
        with app.state.database.session() as session:
            row = session.get(HarnessReport, identity)
            runtimes = [
                case.get("runtime")
                for case in cases
                if case.get("scope") == "native_model" and case.get("status") != "SKIP"
            ]
            native_runtime = (
                runtimes[0] if runtimes and all(item == runtimes[0] for item in runtimes) else None
            )
            row.report = {**row.report, "native_runtime": native_runtime}
            previous = next(
                (
                    other
                    for other in session.scalars(
                        select(HarnessReport)
                        .where(
                            HarnessReport.id != identity,
                            HarnessReport.report["kind"].as_string() == "evaluation",
                            HarnessReport.report["status"].as_string() == "COMPLETE",
                        )
                        .order_by(HarnessReport.created_at.desc())
                        .limit(30)
                    )
                    if baseline_compatible(other.report, row.report)
                ),
                None,
            )
            baseline = {case["id"]: case for case in previous.report["cases"]} if previous else {}
            comparison = [
                {
                    "id": case["id"],
                    "previous_status": baseline[case["id"]]["status"],
                    "status": case["status"],
                    "regressed": baseline[case["id"]]["status"] == "PASS"
                    and case["status"] in {"FAIL", "ERROR"},
                    "duration_delta_ms": round(
                        case.get("duration_ms", 0) - baseline[case["id"]].get("duration_ms", 0), 2
                    ),
                }
                for case in cases
                if case["id"] in baseline
            ]
            counts = dict(Counter(case["status"] for case in cases))
            row.report = {
                **row.report,
                "status": "COMPLETE",
                "outcome": "FAIL" if counts.get("FAIL") or counts.get("ERROR") else "PASS",
                "finished_at": now(),
                "cases": cases,
                "counts": counts,
                "baseline_id": previous.id if previous else None,
                "comparison": comparison,
                "comparison_note": "Searches the latest 30 completed evaluations. Same runner, fixture, configuration and package versions; native runs also require known identical model digest and runtime. Unknown native identity prevents matching. One run per comparison; latency deltas do not establish statistical significance.",
            }
            session.commit()
    except asyncio.CancelledError:
        with app.state.database.session() as session:
            row = session.get(HarnessReport, identity)
            row.report = {**row.report, "status": "INTERRUPTED", "finished_at": now()}
            session.commit()
        raise
    except Exception as exc:
        with app.state.database.session() as session:
            row = session.get(HarnessReport, identity)
            row.report = {
                **row.report,
                "status": "ERROR",
                "error_type": type(exc).__name__,
                "finished_at": now(),
            }
            session.commit()
    finally:
        app.state.native_evaluation = False


@router.post("/evaluations", status_code=202)
async def start_evaluation(
    body: EvaluationRequest, request: Request, session: Session = Depends(get_session)
):
    from .evaluations import RUNNER_VERSION, fixture_identity

    app = request.app
    if getattr(app.state, "evaluation_task", None) and not app.state.evaluation_task.done():
        raise HTTPException(409, "An evaluation is already running")
    if (body.native or body.poker_native) and (
        app.state.active_generations or app.state.model_queue.lock.locked()
    ):
        raise HTTPException(
            409, "Local inference is busy. Run the native probe after current work finishes."
        )
    settings = app.state.settings()
    row = HarnessReport(
        report={
            "kind": "evaluation",
            "status": "RUNNING",
            "fixture": fixture_identity(),
            "runner_version": RUNNER_VERSION,
            "native_requested": body.native,
            "poker_native_requested": body.poker_native,
            "configuration": safe_configuration(settings),
            "versions": runtime_versions(),
            "cases": [],
            "scope": "isolated fixtures; native probe runs only when explicitly requested",
        }
    )
    session.add(row)
    session.commit()
    app.state.native_evaluation = body.native or body.poker_native
    task = asyncio.create_task(
        execute_evaluation(app, row.id, body.native, settings, body.poker_native)
    )
    app.state.evaluation_task = task
    app.state.background_tasks.add(task)
    task.add_done_callback(app.state.background_tasks.discard)
    return public_report(row)


@router.get("/evaluations/{identity}")
def read_evaluation(identity: str, session: Session = Depends(get_session)):
    row = session.get(HarnessReport, identity)
    if not row or row.report.get("kind") != "evaluation":
        raise HTTPException(404, "Evaluation not found")
    return public_report(row)


class PersistentScheduler:
    """Only lightweight due work while idle; missed runs execute once on startup."""

    def __init__(self, app):
        self.app = app

    async def run_due(self) -> None:
        from .indexing import index_pending

        await index_pending(self.app)
        from .memory import compact_due

        await compact_due(self.app)
        settings = self.app.state.settings()
        if not settings.harness_enabled or self.app.state.active_generations:
            return
        with self.app.state.database.session() as session:
            job = session.get(ScheduledJob, "harness")
            timestamp = now()
            if job and job.next_run > timestamp:
                return
            create_report(session, settings)
            if not job:
                job = ScheduledJob(id="harness", next_run=timestamp)
                session.add(job)
            job.last_run = timestamp
            job.next_run = (
                datetime.now(UTC) + timedelta(hours=settings.harness_interval_hours)
            ).isoformat()
            session.commit()

    async def loop(self) -> None:
        while True:
            await self.run_due()
            await asyncio.sleep(30)
