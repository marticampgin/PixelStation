from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import Request
from sqlalchemy import JSON, ForeignKey, String, Text, create_engine, event
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)


def new_id() -> str:
    return uuid4().hex


def now() -> str:
    return datetime.now(UTC).isoformat()


class Base(DeclarativeBase):
    pass


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(default="New chat")
    created_at: Mapped[str] = mapped_column(default=now)
    updated_at: Mapped[str] = mapped_column(default=now, index=True)
    archived: Mapped[bool] = mapped_column(default=False, index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    summary_message_count: Mapped[int] = mapped_column(default=0)
    messages: Mapped[list["Message"]] = relationship(
        cascade="all, delete-orphan", order_by="Message.created_at"
    )


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str | None] = mapped_column(nullable=True)
    created_at: Mapped[str] = mapped_column(default=now)
    status: Mapped[str] = mapped_column(default="complete")
    attachment_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    memory_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    traces: Mapped[list[dict]] = mapped_column(JSON, default=list)
    feedback: Mapped[str | None] = mapped_column(nullable=True)


class Memory(Base):
    __tablename__ = "memories"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    text: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(default="fact", index=True)
    scope: Mapped[str] = mapped_column(default="personal")
    source_conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    source_message_id: Mapped[str | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[str] = mapped_column(default=now)
    updated_at: Mapped[str] = mapped_column(default=now)
    last_accessed_at: Mapped[str | None] = mapped_column(nullable=True)
    importance: Mapped[float] = mapped_column(default=0.5)
    confidence: Mapped[float] = mapped_column(default=1.0)
    pinned: Mapped[bool] = mapped_column(default=False)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    embedding: Mapped[list[float] | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    start_date: Mapped[str | None] = mapped_column(nullable=True)
    end_date: Mapped[str | None] = mapped_column(nullable=True)
    expiry: Mapped[str | None] = mapped_column(nullable=True)


class MemoryRevision(Base):
    __tablename__ = "memory_revisions"
    id: Mapped[str] = mapped_column(primary_key=True, default=new_id)
    memory_id: Mapped[str] = mapped_column(
        ForeignKey("memories.id", ondelete="CASCADE"), index=True
    )
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(default=now)


class MemoryLink(Base):
    __tablename__ = "memory_links"
    id: Mapped[str] = mapped_column(primary_key=True, default=new_id)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("memories.id", ondelete="CASCADE"), index=True
    )
    target_id: Mapped[str] = mapped_column(ForeignKey("memories.id", ondelete="CASCADE"))
    relation: Mapped[str] = mapped_column(default="relates_to")


class Attachment(Base):
    __tablename__ = "attachments"
    id: Mapped[str] = mapped_column(primary_key=True, default=new_id)
    filename: Mapped[str] = mapped_column()
    sha256: Mapped[str] = mapped_column(index=True)
    path: Mapped[str] = mapped_column()
    size: Mapped[int] = mapped_column()
    media_type: Mapped[str] = mapped_column()
    extension: Mapped[str] = mapped_column()
    source: Mapped[str] = mapped_column(default="uploaded")
    created_at: Mapped[str] = mapped_column(default=now)
    parse_status: Mapped[str] = mapped_column(default="ready")
    parse_error: Mapped[str | None] = mapped_column(nullable=True)
    parser: Mapped[str] = mapped_column(default="native")
    chunks: Mapped[list["DocumentChunk"]] = relationship(cascade="all, delete-orphan")


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    id: Mapped[str] = mapped_column(primary_key=True, default=new_id)
    attachment_id: Mapped[str] = mapped_column(
        ForeignKey("attachments.id", ondelete="CASCADE"), index=True
    )
    text: Mapped[str] = mapped_column(Text)
    number: Mapped[int] = mapped_column()
    location: Mapped[str] = mapped_column(default="")
    page: Mapped[int | None] = mapped_column(nullable=True)
    heading: Mapped[str] = mapped_column(default="")
    embedding: Mapped[list[float] | None] = mapped_column(JSON(none_as_null=True), nullable=True)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    id: Mapped[str] = mapped_column(primary_key=True, default=new_id)
    conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    message_id: Mapped[str | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    route: Mapped[str] = mapped_column()
    model: Mapped[str | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(default="running")
    started_at: Mapped[str] = mapped_column(default=now)
    finished_at: Mapped[str | None] = mapped_column(nullable=True)
    latency_ms: Mapped[int] = mapped_column(default=0)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)


class FrictionEvent(Base):
    __tablename__ = "friction_events"
    id: Mapped[str] = mapped_column(primary_key=True, default=new_id)
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[str] = mapped_column(index=True)
    details: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(default=now)
    regression: Mapped[dict] = mapped_column(JSON, default=dict)


class HarnessReport(Base):
    __tablename__ = "harness_runs"
    id: Mapped[str] = mapped_column(primary_key=True, default=new_id)
    created_at: Mapped[str] = mapped_column(default=now)
    report: Mapped[dict] = mapped_column(JSON)


class ScheduledJob(Base):
    __tablename__ = "scheduled_jobs"
    id: Mapped[str] = mapped_column(primary_key=True)
    next_run: Mapped[str] = mapped_column()
    last_run: Mapped[str | None] = mapped_column(nullable=True)


class Database:
    def __init__(self, data_dir: Path):
        data_dir.mkdir(parents=True, exist_ok=True)
        self.path = data_dir / "pixel_station.db"
        self.engine = create_engine(
            f"sqlite:///{self.path.as_posix()}",
            connect_args={"check_same_thread": False, "timeout": 30},
        )

        @event.listens_for(self.engine, "connect")
        def pragmas(connection, _):
            connection.execute("PRAGMA busy_timeout=30000")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")

        self.session = sessionmaker(self.engine, expire_on_commit=False)

    def migrate(self) -> None:
        from alembic import command
        from alembic.config import Config

        config = Config()
        config.set_main_option(
            "script_location", str(Path(__file__).resolve().parent.parent / "migrations")
        )
        with self.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.database.session() as session:
        yield session


def record_dict(record: Base) -> dict:
    return {column.name: getattr(record, column.name) for column in record.__table__.columns}
