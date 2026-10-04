import base64
import hashlib
import json
from email import policy
from email.parser import BytesParser
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from pixel_station.database import Attachment, Database
from pixel_station.files import ingest
from pixel_station.integrations import IntegrationServices
from pixel_station.providers.google import GoogleConnector
from pixel_station.providers.web import IntegrationError


def setup(tmp_path, serve):
    database = Database(tmp_path)
    database.migrate()
    service = IntegrationServices(tmp_path, lambda key, default: default, lambda key, value: None)
    service.google = GoogleConnector(tmp_path, credential_loader=lambda: "fixture", transport=httpx.MockTransport(serve))
    content = b"Rental date: 2026-10-04. Package: one day."
    with database.session() as session:
        file = ingest(session, tmp_path, "Leieavtale æøå.txt", content)
        id_, path = file.id, Path(file.path)
    email = {"to": "recipient@example.com", "subject": "Agreement test", "body": "Reviewed test attachment.",
             "thread_id": "thread1", "in_reply_to": "<message@example.com>", "attachment_ids": [id_]}
    return service, database, email, path, content


def mime(request, *, draft=False):
    payload = json.loads(request.content)
    if draft:
        payload = payload["message"]
    assert payload["threadId"] == "thread1"
    return BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(payload["raw"]))


@pytest.mark.asyncio
async def test_send_reviews_and_sends_immutable_snapshot_after_original_changes(tmp_path):
    calls = []

    def serve(request):
        calls.append(request)
        return httpx.Response(200, json={"id": "sent-message", "threadId": "thread1"})

    service, _, email, path, content = setup(tmp_path, serve)
    proposal = (await service.propose_action("gmail_send", email))["approval"]
    reviewed = proposal["payload"]["attachments"][0]
    assert reviewed["filename"] == "Leieavtale æøå.txt"
    assert reviewed["sha256"] == hashlib.sha256(content).hexdigest() and reviewed["size"] == len(content)
    assert "content" not in reviewed and "path" not in reviewed and "attachment_ids" not in proposal["payload"]
    path.write_bytes(b"Original altered after approval")
    result = await service.confirm(proposal["id"])
    assert result["result"]["attachment_count"] == 1
    parsed = mime(calls[0])
    attachment = list(parsed.iter_attachments())[0]
    assert attachment.get_filename() == reviewed["filename"] and attachment.get_payload(decode=True) == content
    assert parsed["To"] == email["to"] and parsed["In-Reply-To"] == email["in_reply_to"]
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_saved_draft_contains_identical_verified_mime_attachment_and_reopens_it(tmp_path):
    calls = []

    def serve(request):
        calls.append(request)
        return httpx.Response(200, json={"id": "saved-draft"})

    service, _, email, _, content = setup(tmp_path, serve)
    result = await service.create_gmail_draft(email)
    assert result["id"] == "saved-draft" and result["attachment_count"] == 1
    attachment = list(mime(calls[0], draft=True).iter_attachments())[0]
    assert attachment.get_payload(decode=True) == content
    assert calls[0].method == "POST" and calls[1].method == "GET"
    assert calls[1].url.path.endswith("/drafts/saved-draft")


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["bytes", "missing", "path_escape"])
async def test_confirm_rejects_changed_missing_or_escaped_snapshot_before_google_write(tmp_path, change):
    calls = []
    service, _, email, _, _ = setup(tmp_path, lambda request: calls.append(request) or httpx.Response(200, json={"id": "bad"}))
    proposal = (await service.propose_action("gmail_send", email))["approval"]
    snapshot = tmp_path / "connectors" / "google" / "attachments" / (proposal["payload"]["attachments"][0]["id"] + ".bin")
    if change == "bytes":
        snapshot.write_bytes(b"Changed reviewed content")
    elif change == "missing":
        snapshot.unlink()
    else:
        # Even a valid-looking metadata ID cannot redirect a snapshot beyond its directory.
        payload = proposal["payload"]
        payload["attachments"][0]["id"] = "../../outside"
        proposal = service.approvals.propose("gmail_send", payload)
    with pytest.raises(IntegrationError) as error:
        await service.confirm(proposal["id"])
    assert error.value.code == "attachment_changed" and not calls


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["original_bytes", "outside_library", "unknown", "duplicate", "caller_metadata"])
async def test_proposal_refuses_unverified_managed_files(tmp_path, change):
    calls = []
    service, database, email, path, _ = setup(tmp_path, lambda request: calls.append(request) or httpx.Response(200, json={"id": "bad"}))
    if change == "original_bytes":
        path.write_bytes(b"Changed before review")
    elif change == "outside_library":
        outside = tmp_path.parent / (tmp_path.name + "-outside.txt")
        outside.write_bytes(path.read_bytes())
        with database.session() as session:
            session.get(Attachment, email["attachment_ids"][0]).path = str(outside)
            session.commit()
    elif change == "unknown":
        email["attachment_ids"] = ["0" * 32]
    elif change == "duplicate":
        email["attachment_ids"] *= 2
    else:
        email["attachments"] = [{"filename": "forged.txt", "content": "client data"}]
    with pytest.raises((IntegrationError, ValidationError)):
        await service.propose_action("gmail_send", email)
    assert not calls and service.approvals.pending() == []
    assert list((tmp_path / "connectors" / "google" / "attachments").glob("*.bin")) == []
