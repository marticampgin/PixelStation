from __future__ import annotations

import hashlib
import io
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from pixel_station.app import create_app
from pixel_station.database import Attachment, DocumentChunk
from pixel_station.file_edits import (
    FileEditProposal,
    FileRevision,
    create_file_edit_router,
    propose_chat_edit,
)


class EditLLM:
    def __init__(self):
        self.file_id = ""

    async def models(self):
        return {"available": True, "models": []}

    async def structured(self, model, messages, schema, **kwargs):
        return schema(file_id=self.file_id, plan="Update the deadline and retain the budget.",
                      content="Deadline: Friday\nBudget: 100")


@pytest.fixture
def app(tmp_path):
    app = create_app(tmp_path, llm=EditLLM(), discover=False)
    if not hasattr(app.state, "file_edits"):
        app.include_router(create_file_edit_router(app))
    return app


@pytest.fixture
def client(app):
    with TestClient(app) as client:
        yield client


def upload(client, filename="notes.txt", content=b"Deadline: Monday\nBudget: 100"):
    response = client.post("/api/files/upload", files={"file": (filename, content)})
    assert response.status_code == 200, response.text
    return response.json()


def propose(client, file_id, content="Deadline: Friday\nBudget: 100"):
    response = client.post(f"/api/files/{file_id}/edit-proposals",
                           json={"content": content, "plan": "Move the deadline to Friday."})
    assert response.status_code == 200, response.text
    return response.json()


def confirm(client, proposal_id):
    return client.post(f"/api/files/edit-proposals/{proposal_id}/confirm", json={"confirmed": True})


def test_edit_requires_confirmation_preserves_original_and_rebuilds_search(client, app):
    original = upload(client)
    original_bytes = Path(original["path"]).read_bytes()
    before_chunks = client.get(f"/api/files/{original['id']}").json()["chunks"]
    proposal = propose(client, original["id"])
    assert client.get(f"/api/files/{original['id']}/content").content == original_bytes
    assert proposal["before_sha256"] == original["sha256"]
    assert proposal["preview_content"] == "Deadline: Friday\nBudget: 100"
    assert confirm(client, proposal["id"]).status_code == 200
    assert confirm(client, proposal["id"]).status_code == 409
    edited = client.get(f"/api/files/{original['id']}").json()
    assert edited["id"] == original["id"]
    assert edited["sha256"] != original["sha256"]
    assert edited["parser"] == "native-edit"
    assert edited["parse_status"] == "ready"
    assert edited["chunks"][0]["text"] == "Deadline: Friday\nBudget: 100"
    assert edited["chunks"][0]["id"] != before_chunks[0]["id"]
    assert edited["chunks"][0]["embedding"] is None
    assert Path(original["path"]).read_bytes() == original_bytes
    revisions = client.get(f"/api/files/{original['id']}/revisions").json()
    assert len(revisions) == 1
    assert "before_path" not in revisions[0]
    assert client.get(f"/api/files/{original['id']}/revisions/{revisions[0]['id']}/content").content == original_bytes
    with app.state.database.session() as session:
        assert session.execute(text("SELECT COUNT(*) FROM document_chunks_fts WHERE document_chunks_fts MATCH 'Friday'")).scalar() == 1
        assert session.execute(text("SELECT COUNT(*) FROM document_chunks_fts WHERE document_chunks_fts MATCH 'Monday'")).scalar() == 0


@pytest.mark.parametrize("format,content", [
    ("txt", "Revised plain text"), ("md", "# Revised heading\n\nParagraph"),
    ("csv", "name,amount\nAlice,23"), ("xlsx", "name,amount\nAlice,23"),
    ("docx", "Revised title\nA second paragraph"), ("pdf", "Revised title\nA second paragraph")
])
def test_edit_supported_export_formats_create_valid_replacements(client, format, content):
    original = client.post("/api/files/create", json={"filename": "report", "format": format,
                                                    "content": "Original title\nSecond paragraph"}).json()
    editable = client.get(f"/api/files/{original['id']}/edit-content")
    assert editable.status_code == 200, editable.text
    proposal = propose(client, original["id"], content)
    response = confirm(client, proposal["id"])
    assert response.status_code == 200, response.text
    reread = client.get(f"/api/files/{original['id']}/edit-content").json()
    assert "Revised" in reread["content"] or "Alice" in reread["content"]
    assert client.get(f"/api/files/{original['id']}/content").content


def test_xlsx_edit_preserves_other_sheets_styles_and_blocks_formula_input(client):
    from openpyxl import Workbook, load_workbook
    from openpyxl.styles import Font
    workbook = Workbook()
    workbook.active.title = "Costs"
    workbook.active.append(["Name", "Cost"])
    workbook.active.append(["Alice", 10])
    workbook.active["A1"].font = Font(bold=True)
    workbook.create_sheet("Summary")["A1"] = "=SUM(Costs!B2:B10)"
    output = io.BytesIO()
    workbook.save(output)
    original = upload(client, "costs.xlsx", output.getvalue())
    proposal = propose(client, original["id"], "Name,Cost\n=HYPERLINK(1),20")
    assert "first worksheet 'Costs'" in proposal["scope"]
    assert confirm(client, proposal["id"]).status_code == 200
    updated = load_workbook(io.BytesIO(client.get(f"/api/files/{original['id']}/content").content))
    assert updated.sheetnames == ["Costs", "Summary"]
    assert updated["Summary"]["A1"].value == "=SUM(Costs!B2:B10)"
    assert updated["Costs"]["A1"].font.bold
    assert updated["Costs"]["A2"].value == "'=HYPERLINK(1)"


def test_docx_edit_preserves_tables_and_paragraph_styles(client):
    from docx import Document
    document = Document()
    document.add_paragraph("Original heading", style="Heading 1")
    document.add_paragraph("Existing body")
    document.add_table(rows=1, cols=1).cell(0, 0).text = "Keep this table"
    output = io.BytesIO()
    document.save(output)
    original = upload(client, "document.docx", output.getvalue())
    proposal = propose(client, original["id"], "Updated heading\nUpdated body")
    assert "Inline formatting" in proposal["plan"]
    assert confirm(client, proposal["id"]).status_code == 200
    updated = Document(io.BytesIO(client.get(f"/api/files/{original['id']}/content").content))
    assert updated.paragraphs[0].text == "Updated heading"
    assert updated.paragraphs[0].style.name == "Heading 1"
    assert updated.tables[0].cell(0, 0).text == "Keep this table"


def test_invalid_csv_edit_leaves_original_and_index_unchanged(client, app):
    original = upload(client, "costs.csv", b"name,amount\nAlice,10")
    response = client.post(f"/api/files/{original['id']}/edit-proposals", json={"content": 'name,amount\n"unclosed'})
    assert response.status_code == 422
    assert client.get(f"/api/files/{original['id']}/content").content == b"name,amount\nAlice,10"
    with app.state.database.session() as session:
        assert list(session.scalars(select(FileEditProposal))) == []
        assert list(session.scalars(select(FileRevision))) == []


def test_stale_proposal_after_another_revision_cannot_apply(client):
    original = upload(client)
    first, second = propose(client, original["id"]), propose(client, original["id"], "Competing edit")
    assert confirm(client, first["id"]).status_code == 200
    stale = confirm(client, second["id"])
    assert stale.status_code == 409
    assert "changed after review" in stale.text
    assert client.get(f"/api/files/{original['id']}/content").content == b"Deadline: Friday\nBudget: 100"


def test_expired_rejected_and_missing_confirmation_do_not_write(client, app):
    original = upload(client)
    proposal = propose(client, original["id"])
    endpoint = f"/api/files/edit-proposals/{proposal['id']}/confirm"
    assert client.post(endpoint, json={"confirmed": False}).status_code == 422
    assert client.post(endpoint, json={"confirmed": True, "content": "unreviewed"}).status_code == 422
    assert client.delete(f"/api/files/edit-proposals/{proposal['id']}").status_code == 200
    assert confirm(client, proposal["id"]).status_code == 409
    app.state.file_edits.ttl = -1
    expired = propose(client, original["id"])
    assert confirm(client, expired["id"]).status_code == 409
    assert client.get(f"/api/files/{original['id']}/content").content == b"Deadline: Monday\nBudget: 100"


@pytest.mark.parametrize("alteration", ["proposal", "stage", "original"])
def test_mutating_reviewed_state_blocks_confirmation(client, app, alteration):
    original = upload(client)
    proposal = propose(client, original["id"])
    if alteration == "proposal":
        with app.state.database.session() as session:
            row = session.get(FileEditProposal, proposal["id"])
            row.content = "Unreviewed content"
            session.commit()
    elif alteration == "stage":
        app.state.file_edits._stage(proposal["id"], ".txt").write_text("Unreviewed content")
    else:
        Path(original["path"]).write_bytes(b"External modification")
    assert confirm(client, proposal["id"]).status_code == 409
    with app.state.database.session() as session:
        assert session.get(Attachment, original["id"]).sha256 == original["sha256"]
        assert list(session.scalars(select(FileRevision))) == []


def test_concurrent_confirmation_is_single_use(client, app):
    original = upload(client)
    proposal = propose(client, original["id"])

    def apply():
        try:
            app.state.file_edits.confirm(proposal["id"])
            return 200
        except HTTPException as exc:
            return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: apply(), range(2)))
    assert sorted(results) == [200, 409]
    assert len(client.get(f"/api/files/{original['id']}/revisions").json()) == 1


def test_edit_does_not_follow_an_outside_managed_file_path(client, app, tmp_path):
    original = upload(client)
    outside = tmp_path.parent / ("outside-" + original["id"] + ".txt")
    outside.write_text("Outside data")
    try:
        with app.state.database.session() as session:
            row = session.get(Attachment, original["id"])
            row.path = str(outside)
            row.sha256 = hashlib.sha256(outside.read_bytes()).hexdigest()
            session.commit()
        assert client.get(f"/api/files/{original['id']}/edit-content").status_code == 404
        assert client.post(f"/api/files/{original['id']}/edit-proposals", json={"content": "Change outside"}).status_code == 404
        assert outside.read_text() == "Outside data"
    finally:
        outside.unlink()


def test_failed_revalidation_rolls_back_library_change(client, app, monkeypatch):
    original = upload(client)
    proposal = propose(client, original["id"])

    def fail(*args, **kwargs):
        raise ValueError("Revalidation failed")

    monkeypatch.setattr("pixel_station.file_edits.NativeFileParser.parse", fail)
    assert confirm(client, proposal["id"]).status_code == 422
    with app.state.database.session() as session:
        assert session.get(Attachment, original["id"]).sha256 == original["sha256"]
        assert session.get(FileEditProposal, proposal["id"]).status == "pending"
        assert list(session.scalars(select(FileRevision))) == []
        assert "Monday" in session.scalar(select(DocumentChunk)).text


def test_proposal_and_revision_persist_after_app_restart(tmp_path):
    app = create_app(tmp_path, llm=EditLLM(), discover=False)
    if not hasattr(app.state, "file_edits"):
        app.include_router(create_file_edit_router(app))
    with TestClient(app) as client:
        original = upload(client)
        proposal = propose(client, original["id"])
    restarted = create_app(tmp_path, llm=EditLLM(), discover=False)
    if not hasattr(restarted.state, "file_edits"):
        restarted.include_router(create_file_edit_router(restarted))
    with TestClient(restarted) as client:
        assert confirm(client, proposal["id"]).status_code == 200
        assert len(client.get(f"/api/files/{original['id']}/revisions").json()) == 1
        assert client.get(f"/api/files/{original['id']}/content").content == b"Deadline: Friday\nBudget: 100"


@pytest.mark.asyncio
async def test_chat_edit_returns_proposal_with_exact_target_without_writing(app):
    from pixel_station.files import ingest
    with app.state.database.session() as session:
        original = ingest(session, app.state.data_dir, "notes.txt", b"Deadline: Monday\nBudget: 100")
    app.state.llm.file_id = original.id
    settings = app.state.settings()
    settings.roles["planner"] = "test-local"
    app.state.set_settings(settings)
    result = await propose_chat_edit(app, [original.id], "Move the deadline to Friday")
    assert result["file_edit"]["file_id"] == original.id
    assert "confirm" in result["content"]
    assert Path(original.path).read_bytes() == b"Deadline: Monday\nBudget: 100"
    with pytest.raises(HTTPException) as error:
        await propose_chat_edit(app, [], "Edit something")
    assert error.value.status_code == 422


def test_remote_origin_cannot_propose_file_edits(client):
    original = upload(client)
    response = client.post(f"/api/files/{original['id']}/edit-proposals", json={"content": "Updated"},
                           headers={"Origin": "https://example.com"})
    assert response.status_code == 403
