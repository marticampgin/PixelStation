import asyncio
import json

import pytest

from pixel_station.app import create_app
from pixel_station.chat import (
    ConversationCreate,
    MessageInput,
    exact_document_dates,
    generate_response,
    new_conversation,
)
from pixel_station.files import ingest


class NoInference:
    def __init__(self):
        self.embedding_calls = 0

    async def embed(self, *args, **kwargs):
        self.embedding_calls += 1
        raise AssertionError("The index-wide literal scan needs no semantic query embedding")

    async def stream(self, *args, **kwargs):
        raise AssertionError("Literal dates must be copied from actual parsed source text")
        yield ""


def excerpts(text):
    return [
        {"file_id": "fixture", "filename": "fixture-contract.txt", "location": "L1", "text": text}
    ]


@pytest.mark.parametrize(
    "value",
    ["16.–18.10.2099 (TEST)", "16.10.2099–18.10.2099 TEST", "2099-10-16 – 2099-10-18 (TEST)"],
)
def test_explicit_exact_dates_copy_whole_ranges_and_qualifiers(value):
    text = "Signature date: 02.10.2099 TEST\nRental date: " + value
    answer = exact_document_dates("Give the exact signature and rental dates", excerpts(text))
    assert value in answer and "02.10.2099 TEST" in answer
    assert "[fixture-contract.txt, L1]" in answer


def test_ordinary_questions_and_unavailable_date_text_keep_regular_answer_path():
    assert (
        exact_document_dates("Summarize this contract", excerpts("Rental: 16.–18.10.2099")) is None
    )
    assert (
        exact_document_dates("What is the rental date?", excerpts("Rental: 16.–18.10.2099")) is None
    )
    assert exact_document_dates("Give the exact dates", excerpts("No dates are supplied")) is None
    assert exact_document_dates("Quote the exact verification code", excerpts("2099-10-16")) is None
    assert (
        exact_document_dates(
            "Give exact rental date and membership", excerpts("Rental: 16.–18.10.2099")
        )
        is None
    )


def test_multiple_dates_are_not_silently_assigned_to_requested_field():
    answer = exact_document_dates(
        "Give the signature date verbatim",
        excerpts("Party one: 02.10.2099\nParty two: 03.10.2099\nRental: 16.–18.10.2099 TEST"),
    )
    assert "02.10.2099" in answer and "03.10.2099" in answer
    assert "not assigned to a field unless the source labels them" in answer
    assert "Signature date:" not in answer


def test_long_date_line_keeps_full_range_and_marks_excerpt_bounds():
    value = "16.–18.10.2099 TEST"
    answer = exact_document_dates(
        "Quote the rental date exactly", excerpts("x " * 500 + value + " y" * 500)
    )
    assert value in answer and "…" in answer and len(answer) < 1000


async def test_production_chat_quotes_exact_dates_without_weak_model_rewriting(tmp_path):
    app = create_app(tmp_path, llm=NoInference(), discover=False)
    settings = app.state.settings()
    settings.roles["embedding"] = "fixture:embedding"
    app.state.set_settings(settings)
    with app.state.database.session() as session:
        attachment = ingest(
            session,
            tmp_path,
            "fixture-contract.txt",
            b"Signature date: 02.10.2099 TEST\nRental date: 16.-18.10.2099 TEST",
        )
        identity = attachment.id
        conversation = new_conversation(ConversationCreate(), session)
    events = [
        json.loads(value)
        async for value in generate_response(
            app,
            conversation["id"],
            MessageInput(
                content="Give the exact signature and rental dates", attachment_ids=[identity]
            ),
        )
    ]
    message = events[-1]["message"]
    assert message["status"] == "complete"
    assert "16.-18.10.2099 TEST" in message["content"] and "02.10.2099 TEST" in message["content"]
    assert any(trace.get("validation") == "exact_document_dates" for trace in message["traces"])
    assert app.state.llm.embedding_calls == 0
    from sqlalchemy import select

    from pixel_station.database import AgentRun

    with app.state.database.session() as session:
        run = session.scalar(select(AgentRun))
        assert run.evidence["metrics"]["fallback_count"] == 0
    if app.state.background_tasks:
        await asyncio.gather(*app.state.background_tasks)
    app.state.database.engine.dispose()


async def date_chat(app, attachment_id):
    with app.state.database.session() as session:
        conversation = new_conversation(ConversationCreate(), session)
    events = [
        json.loads(value)
        async for value in generate_response(
            app,
            conversation["id"],
            MessageInput(
                content="Give the exact signature and rental dates", attachment_ids=[attachment_id]
            ),
        )
    ]
    if app.state.background_tasks:
        await asyncio.gather(*app.state.background_tasks)
    return events[-1]["message"]


async def test_date_scan_finds_late_signature_outside_six_relevance_ranked_chunks(tmp_path):
    from sqlalchemy import delete

    from pixel_station.database import DocumentChunk
    from pixel_station.files import retrieve_files

    app = create_app(tmp_path, llm=NoInference(), discover=False)
    with app.state.database.session() as session:
        attachment = ingest(session, tmp_path, "fixture-contract.txt", b"Fixture contract")
        identity = attachment.id
        session.execute(delete(DocumentChunk).where(DocumentChunk.attachment_id == identity))
        session.add(
            DocumentChunk(
                attachment_id=identity,
                number=0,
                location="body",
                text="Rental date: 16.-18.10.2099 TEST",
            )
        )
        for number in range(1, 9):
            session.add(
                DocumentChunk(
                    attachment_id=identity,
                    number=number,
                    location="body",
                    text="Exact signature rental dates instructions. Give the exact signature and rental dates.",
                )
            )
        session.add(
            DocumentChunk(
                attachment_id=identity,
                number=9,
                location="signature",
                text="Signed: 02.10.2099 TEST",
            )
        )
        session.commit()
        ordinary = retrieve_files(session, [identity], "Give the exact signature and rental dates")
        assert len(ordinary) == 6 and all("02.10.2099" not in row["text"] for row in ordinary)
    message = await date_chat(app, identity)
    assert message["status"] == "complete"
    assert "02.10.2099 TEST" in message["content"] and "16.-18.10.2099 TEST" in message["content"]
    assert "fixture-contract.txt, signature" in message["content"]
    scan = next(trace["date_scan"] for trace in message["traces"] if "date_scan" in trace)
    assert scan["candidate_chunks_scanned"] == 2 and not scan["scan_limit_reached"]
    app.state.database.engine.dispose()


async def test_more_than_eight_date_excerpts_reports_explicit_partial_result(tmp_path):
    app = create_app(tmp_path, llm=NoInference(), discover=False)
    content = "\n".join(f"Date {number}: {number:02}.10.2099 TEST" for number in range(1, 10))
    with app.state.database.session() as session:
        attachment = ingest(session, tmp_path, "fixture-contract.txt", content.encode())
        identity = attachment.id
    message = await date_chat(app, identity)
    assert message["status"] == "interrupted"
    assert "08.10.2099 TEST" in message["content"] and "09.10.2099" not in message["content"]
    assert "omitted by the eight-excerpt output limit" in message["content"]
    app.state.database.engine.dispose()


@pytest.mark.parametrize("limit", ["chunks", "characters"])
async def test_date_scan_resource_limits_are_visible_and_never_report_complete(
    tmp_path, monkeypatch, limit
):
    from sqlalchemy import delete

    from pixel_station import chat
    from pixel_station.database import DocumentChunk

    app = create_app(tmp_path, llm=NoInference(), discover=False)
    with app.state.database.session() as session:
        attachment = ingest(session, tmp_path, "fixture-contract.txt", b"Fixture contract")
        identity = attachment.id
        session.execute(delete(DocumentChunk).where(DocumentChunk.attachment_id == identity))
        for number in range(2):
            session.add(
                DocumentChunk(
                    attachment_id=identity,
                    number=number,
                    location="body",
                    text=f"Signature date: 0{number + 1}.10.2099 TEST",
                )
            )
        session.commit()
    if limit == "chunks":
        monkeypatch.setattr(chat, "MAX_DATE_SCAN_CHUNKS", 1)
    else:
        monkeypatch.setattr(chat, "MAX_DATE_SCAN_CHARS", 20)
    message = await date_chat(app, identity)
    assert message["status"] == "interrupted"
    assert "scan" in message["content"].lower() and "characters" in message["content"]
    assert "02.10.2099 TEST" not in message["content"]
    scan = next(trace["date_scan"] for trace in message["traces"] if "date_scan" in trace)
    assert scan["scan_limit_reached"]
    app.state.database.engine.dispose()


async def test_missing_selected_index_is_explicit_instead_of_model_guessing(tmp_path):
    from sqlalchemy import delete

    from pixel_station.database import DocumentChunk

    app = create_app(tmp_path, llm=NoInference(), discover=False)
    with app.state.database.session() as session:
        attachment = ingest(session, tmp_path, "fixture-contract.txt", b"Date: 02.10.2099 TEST")
        identity = attachment.id
        session.execute(delete(DocumentChunk).where(DocumentChunk.attachment_id == identity))
        session.commit()
    message = await date_chat(app, identity)
    assert message["status"] == "interrupted"
    assert "no complete text index" in message["content"]
    assert "02.10.2099" not in message["content"]
    app.state.database.engine.dispose()


def test_date_scan_streams_database_rows_and_closes_cursor_at_character_limit(
    tmp_path, monkeypatch
):
    import sqlite3

    from sqlalchemy import delete

    from pixel_station.chat import indexed_date_context
    from pixel_station.database import DocumentChunk

    app = create_app(tmp_path, llm=NoInference(), discover=False)
    db = app.state.database
    with db.session() as session:
        attachment = ingest(session, tmp_path, "fixture-contract.txt", b"Fixture contract")
        identity = attachment.id
        session.execute(delete(DocumentChunk).where(DocumentChunk.attachment_id == identity))
        for number in range(11):
            session.add(
                DocumentChunk(
                    attachment_id=identity,
                    number=number,
                    location="body",
                    text="Date: 02.10.2099 TEST\n" + "x" * 250000,
                )
            )
        session.commit()
    fetched = []
    closed = []

    class TrackingCursor(sqlite3.Cursor):
        def execute(self, statement, parameters=()):
            self.date_scan = "substr(document_chunks.text" in statement.lower()
            return super().execute(statement, parameters)

        def fetchall(self):
            rows = super().fetchall()
            if self.date_scan:
                fetched.append(("all", len(rows)))
            return rows

        def fetchmany(self, size=None):
            rows = super().fetchmany(self.arraysize if size is None else size)
            if self.date_scan:
                fetched.append(("many", len(rows)))
            return rows

        def close(self):
            if getattr(self, "date_scan", False):
                closed.append(True)
            return super().close()

    class TrackingConnection(sqlite3.Connection):
        def cursor(self, *args, **kwargs):
            kwargs["factory"] = TrackingCursor
            return super().cursor(*args, **kwargs)

    # Track the real database driver, so eager ORM buffering fails this
    # regression even if the application subsequently counts only one row.
    db.engine.dispose()
    monkeypatch.setattr(
        db.engine.pool,
        "_creator",
        lambda: sqlite3.connect(
            str(db.path),
            check_same_thread=False,
            factory=TrackingConnection,
        ),
    )
    with db.session() as session:
        context, metadata = indexed_date_context(session, [identity])
    assert metadata["scan_limit_reached"] and metadata["candidate_chunks_scanned"] == 1
    assert sum(size for _, size in fetched) <= 1
    assert fetched and all(method == "many" for method, _ in fetched)
    assert closed == [True]
    assert len(context) == 1
    db.engine.dispose()
