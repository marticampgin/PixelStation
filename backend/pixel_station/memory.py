import asyncio
import re
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .database import (
    Conversation,
    FrictionEvent,
    Memory,
    MemoryLink,
    MemoryRevision,
    Message,
    get_session,
    now,
    record_dict,
)
from .vectors import SqliteVectorStore

router = APIRouter(prefix="/api/memory", tags=["memory"])


class MemoryInput(BaseModel):
    text: str = Field(min_length=1, max_length=10000)
    category: Literal[
        "preference",
        "fact",
        "event",
        "project",
        "workflow",
        "person/entity",
        "instruction",
        "learned_pattern",
    ] = "fact"
    scope: str = Field(default="personal", max_length=100)
    importance: float = Field(default=0.5, ge=0, le=1)
    confidence: float = Field(default=1, ge=0, le=1)
    tags: list[str] = Field(default_factory=list, max_length=20)
    pinned: bool = False
    source_conversation_id: str | None = None
    source_message_id: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    expiry: str | None = None


class MemoryPatch(BaseModel):
    text: str | None = Field(default=None, min_length=1, max_length=10000)
    category: str | None = None
    scope: str | None = None
    importance: float | None = Field(default=None, ge=0, le=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    tags: list[str] | None = None
    pinned: bool | None = None
    expiry: str | None = None


def fts_query(query: str) -> str:
    words = re.findall(r"\w+", query, re.UNICODE)[:30]
    return " OR ".join('"' + word.replace('"', '""') + '"' for word in words)


class LocalVectorStore(SqliteVectorStore):
    def __init__(self, session: Session, model_class=Memory):
        super().__init__(session, model_class)


def search_memory(
    session: Session,
    query: str,
    limit: int = 4,
    scope: str | None = None,
    embedding: list[float] | None = None,
) -> list[dict]:
    lexical = {}
    if expression := fts_query(query):
        matches = session.execute(
            text(
                "SELECT id, bm25(memories_fts) AS rank FROM memories_fts WHERE memories_fts MATCH :query ORDER BY rank LIMIT 30"
            ),
            {"query": expression},
        )
        lexical = {row.id: 1 / (index + 1) for index, row in enumerate(matches)}
    vectors = dict(LocalVectorStore(session).search(embedding, 30)) if embedding else {}
    query_stmt = (
        select(Memory)
        .where((Memory.expiry.is_(None)) | (Memory.expiry > now()))
        .order_by(Memory.updated_at.desc())
        .limit(500)
    )
    ranked = []
    for row in session.scalars(query_stmt):
        if not row.pinned and row.id not in lexical and row.id not in vectors:
            continue
        age_days = max(
            0, (datetime.now(UTC) - datetime.fromisoformat(row.updated_at)).total_seconds() / 86400
        )
        score = (
            lexical.get(row.id, 0)
            + max(0, vectors.get(row.id, 0))
            + row.importance * 0.15
            + (0.7 if row.pinned else 0)
            + (0.15 if scope and row.scope == scope else 0)
            + 0.1 / (1 + age_days / 30)
        )
        entry = record_dict(row)
        entry.pop("embedding", None)
        entry["retrieval_score"] = round(score, 3)
        entry["retrieval_reason"] = ", ".join(
            reason
            for reason, condition in [
                ("pinned", row.pinned),
                ("lexical match", row.id in lexical),
                ("semantic match", row.id in vectors),
                ("active scope", scope and row.scope == scope),
            ]
            if condition
        )
        ranked.append((score, row, entry))
    ranked.sort(key=lambda item: (item[0], item[1].updated_at), reverse=True)
    chosen = ranked[:limit]
    for _, row, _ in chosen:
        row.last_accessed_at = now()
    session.commit()
    return [entry for _, _, entry in chosen]


def create_memory(session: Session, payload: MemoryInput) -> Memory:
    normalized = " ".join(payload.text.casefold().split())
    for old in session.scalars(select(Memory)):
        if " ".join(old.text.casefold().split()) == normalized:
            return old
    if payload.source_message_id and not session.get(Message, payload.source_message_id):
        raise HTTPException(422, "Source message does not exist")
    if payload.source_conversation_id and not session.get(
        Conversation, payload.source_conversation_id
    ):
        raise HTTPException(422, "Source conversation does not exist")
    row = Memory(**payload.model_dump())
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


@router.get("")
def list_memory(q: str = "", category: str | None = None, session: Session = Depends(get_session)):
    if q:
        items = search_memory(session, q, 100)
        return [item for item in items if not category or item["category"] == category]
    statement = select(Memory).order_by(Memory.pinned.desc(), Memory.updated_at.desc())
    if category:
        statement = statement.where(Memory.category == category)
    return [
        {key: value for key, value in record_dict(row).items() if key != "embedding"}
        for row in session.scalars(statement)
    ]


@router.get("/search")
def query_memory(q: str, limit: int = 4, session: Session = Depends(get_session)):
    return search_memory(session, q, max(1, min(limit, 8)))


@router.post("")
def add_memory(payload: MemoryInput, session: Session = Depends(get_session)):
    return record_dict(create_memory(session, payload))


@router.get("/{memory_id}")
def inspect_memory(memory_id: str, session: Session = Depends(get_session)):
    row = session.get(Memory, memory_id)
    if not row:
        raise HTTPException(404, "Memory not found")
    return {
        **record_dict(row),
        "revisions": [
            record_dict(rev)
            for rev in session.scalars(
                select(MemoryRevision).where(MemoryRevision.memory_id == memory_id)
            )
        ],
        "links": [
            record_dict(link)
            for link in session.scalars(
                select(MemoryLink).where(
                    (MemoryLink.source_id == memory_id) | (MemoryLink.target_id == memory_id)
                )
            )
        ],
    }


@router.patch("/{memory_id}")
def edit_memory(memory_id: str, payload: MemoryPatch, session: Session = Depends(get_session)):
    row = session.get(Memory, memory_id)
    if not row:
        raise HTTPException(404, "Memory not found")
    updates = payload.model_dump(exclude_unset=True)
    if updates.get("text") and updates["text"] != row.text:
        session.add(MemoryRevision(memory_id=memory_id, text=row.text))
        row.embedding = None
    for key, value in updates.items():
        if value is None and key != "expiry":
            continue
        setattr(row, key, value)
    row.updated_at = now()
    session.commit()
    return record_dict(row)


@router.delete("/{memory_id}")
def delete_memory(memory_id: str, confirmed: bool = False, session: Session = Depends(get_session)):
    if not confirmed:
        raise HTTPException(409, "Confirm memory deletion")
    row = session.get(Memory, memory_id)
    if not row:
        raise HTTPException(404, "Memory not found")
    session.delete(row)
    session.commit()
    return {"deleted": True}


class LinkInput(BaseModel):
    target_id: str
    relation: Literal["supports", "updates", "contradicts", "relates_to", "supersedes"] = (
        "relates_to"
    )


@router.post("/{memory_id}/links")
def link_memory(memory_id: str, payload: LinkInput, session: Session = Depends(get_session)):
    if (
        memory_id == payload.target_id
        or not session.get(Memory, memory_id)
        or not session.get(Memory, payload.target_id)
    ):
        raise HTTPException(422, "Choose two existing distinct memories")
    row = MemoryLink(source_id=memory_id, **payload.model_dump())
    session.add(row)
    session.commit()
    return record_dict(row)


class SummaryOutput(BaseModel):
    summary: str = Field(max_length=4000)


class Candidate(MemoryInput):
    should_store: bool = True


class ExtractionOutput(BaseModel):
    candidates: list[Candidate] = Field(default_factory=list, max_length=8)


async def compact_conversation(app, conversation_id: str) -> None:
    settings = app.state.settings()
    with app.state.database.session() as session:
        conversation = session.get(Conversation, conversation_id)
        if not conversation:
            return
        rows = list(
            session.scalars(
                select(Message)
                .where(Message.conversation_id == conversation_id, Message.status == "complete")
                .order_by(Message.created_at)
            )
        )
        if len(rows) - conversation.summary_message_count < settings.summary_turns * 2:
            return
        model = settings.roles["summarizer"] or settings.roles["primary_chat"]
        if not model:
            return
        count = len(rows)
        transcript = "\n".join(
            f"[{row.id}] {row.role}: {row.content[:3000]}"
            for row in rows[-settings.summary_turns * 2 :]
        )
        old_summary = conversation.summary
    try:
        async with asyncio.timeout(180), app.state.model_queue.lock:
            summary = await app.state.llm.structured(
                model,
                [
                    {
                        "role": "system",
                        "content": "Summarize topics, decisions, unresolved questions and tasks. Preserve facts and avoid guesses.",
                    },
                    {
                        "role": "user",
                        "content": f"Previous summary: {old_summary}\nRecent transcript:\n{transcript}",
                    },
                ],
                SummaryOutput,
            )
            extraction = None
            if settings.auto_memory:
                extraction_model = settings.roles["memory_extractor"] or model
                extraction = await app.state.llm.structured(
                    extraction_model,
                    [
                        {
                            "role": "system",
                            "content": "Extract only explicitly stated stable user preferences, facts, projects, or events. No guesses, no assistant claims, no private email content. Skip trivial exchanges. Set should_store=false when uncertain. Set source_message_id to the actual user message ID in brackets that supports this memory, or null if unclear. Leave source_conversation_id null; the application assigns it.",
                        },
                        {"role": "user", "content": transcript},
                    ],
                    ExtractionOutput,
                )
        with app.state.database.session() as session:
            conversation = session.get(Conversation, conversation_id)
            if not conversation:
                return
            conversation.summary, conversation.summary_message_count = summary.summary, count
            session.commit()
            if extraction:
                for candidate in extraction.candidates:
                    if candidate.should_store and candidate.confidence >= 0.75:
                        data = candidate.model_dump(exclude={"should_store"})
                        user_ids = {row.id for row in rows if row.role == "user"}
                        data["source_conversation_id"] = conversation_id
                        if data.get("source_message_id") not in user_ids:
                            data["source_message_id"] = None
                        create_memory(session, MemoryInput(**data))
    except Exception as exc:
        with app.state.database.session() as session:
            session.add(FrictionEvent(kind="memory_compaction_error", details=str(exc)[:1000]))
            session.commit()
