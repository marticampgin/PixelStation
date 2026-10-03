"""Deferred local embeddings persist in SQLite; lexical retrieval remains available on failure."""

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from .database import DocumentChunk, FrictionEvent, Memory, ScheduledJob, now
from .vectors import SqliteVectorStore


async def embed_query(app, query: str) -> list[float] | None:
    model = app.state.settings().roles["embedding"]
    if not model:
        return None
    try:
        async with asyncio.timeout(45), app.state.model_queue.lock:
            vectors = await app.state.llm.embed(model, [query[:6000]])
        if app.state.settings().roles["embedding"] != model:
            return None
        return vectors[0]
    except Exception as exc:
        with app.state.database.session() as session:
            session.add(
                FrictionEvent(
                    kind="embedding_query_error",
                    details=f"Lexical retrieval used: {str(exc)[:500]}",
                )
            )
            session.commit()
        return None


async def index_pending(app) -> None:
    settings = app.state.settings()
    model = settings.roles["embedding"]
    if not model or app.state.active_generations or app.state.model_queue.lock.locked():
        return
    with app.state.database.session() as session:
        job = session.get(ScheduledJob, "vectors")
        if job and job.next_run > now():
            return
        pending: list[tuple[type[Memory] | type[DocumentChunk], str, str]] = [
            (Memory, row.id, row.text)
            for row in session.scalars(select(Memory).where(Memory.embedding.is_(None)).limit(8))
        ]
        pending.extend(
            (DocumentChunk, row.id, row.text)
            for row in session.scalars(
                select(DocumentChunk).where(DocumentChunk.embedding.is_(None)).limit(8)
            )
        )
    if not pending:
        return
    delay_minutes = 0
    try:
        async with asyncio.timeout(60), app.state.model_queue.lock:
            vectors = await app.state.llm.embed(model, [entry[2] for entry in pending])
        if len(vectors) != len(pending):
            raise ValueError("Embedding provider returned the wrong number of vectors")
        if app.state.settings().roles["embedding"] != model:
            return
        with app.state.database.session() as session:
            for (model_class, identity, source_text), vector in zip(pending, vectors, strict=True):
                row = session.get(model_class, identity)
                if row and row.text == source_text:
                    SqliteVectorStore(session, model_class).add(identity, vector)
            session.commit()
    except Exception as exc:
        delay_minutes = 60
        with app.state.database.session() as session:
            session.add(
                FrictionEvent(
                    kind="embedding_index_error",
                    details=f"Lexical indexing remains available: {str(exc)[:1000]}",
                )
            )
            session.commit()
    with app.state.database.session() as session:
        job = session.get(ScheduledJob, "vectors")
        if not job:
            job = ScheduledJob(id="vectors", next_run=now())
            session.add(job)
        job.last_run = now()
        job.next_run = (
            datetime.now(UTC) + timedelta(minutes=delay_minutes, seconds=30)
        ).isoformat()
        session.commit()
