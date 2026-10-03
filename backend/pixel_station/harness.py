import asyncio
from collections import Counter
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import (
    AgentRun,
    FrictionEvent,
    HarnessReport,
    ScheduledJob,
    get_session,
    now,
    record_dict,
)

router = APIRouter(prefix="/api/harness", tags=["harness"])
SUGGESTIONS = {
    "response_error": "Review provider setup, route arguments, and timeouts; replay the failed input.",
    "regeneration": "Compare the original answer with the repeated request; assess context quality.",
    "negative_feedback": "Review the reported answer and add a targeted regression case.",
    "memory_compaction_error": "Verify summarizer role and JSON-schema support; preserve source messages.",
    "invalid_poker_action": "Review the legal-action schema and retry/fallback policy.",
    "manual_problem": "Review the user report and reproduce the affected workflow.",
}


def create_report(session: Session) -> HarnessReport:
    since = (datetime.now(UTC) - timedelta(days=7)).isoformat()
    events = list(
        session.scalars(
            select(FrictionEvent)
            .where(FrictionEvent.created_at >= since)
            .order_by(FrictionEvent.created_at.desc())
            .limit(500)
        )
    )
    counts = Counter(event.kind for event in events)
    report = {
        "window_days": 7,
        "event_count": len(events),
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
    row = HarnessReport(report=report)
    session.add(row)
    session.commit()
    return row


@router.get("")
def inspect_harness(session: Session = Depends(get_session)):
    return {
        "reports": [
            record_dict(row)
            for row in session.scalars(
                select(HarnessReport).order_by(HarnessReport.created_at.desc()).limit(20)
            )
        ],
        "events": [
            record_dict(row)
            for row in session.scalars(
                select(FrictionEvent).order_by(FrictionEvent.created_at.desc()).limit(100)
            )
        ],
        "runs": [
            record_dict(row)
            for row in session.scalars(
                select(AgentRun).order_by(AgentRun.started_at.desc()).limit(30)
            )
        ],
    }


@router.post("/run")
def run_harness(session: Session = Depends(get_session)):
    return record_dict(create_report(session))


@router.get("/regressions")
def regressions(session: Session = Depends(get_session)):
    return [
        row.regression
        for row in session.scalars(select(FrictionEvent).where(FrictionEvent.regression != {}))
    ]


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
            create_report(session)
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
