import base64
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pixel_station.database import Database
from pixel_station.files import router as files_router
from pixel_station.integrations import IntegrationServices, create_integrations_router
from pixel_station.providers.google import MAX_ATTACHMENT_BYTES, GoogleConnector
from pixel_station.providers.web import IntegrationError


def encoded(value):
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def message(content=b"Rental service package", *, separate=True, filename="rental.txt"):
    body = {"size": len(content), "attachmentId": "proven-attachment"} if separate else {"size": len(content), "data": encoded(content)}
    return {"id": "message1", "payload": {"parts": [{"partId": "0", "mimeType": "text/plain", "body": {"data": encoded(b"Letter")}},
                                                   {"partId": "1", "filename": filename, "mimeType": "text/plain", "body": body}]}}


@pytest.mark.asyncio
@pytest.mark.parametrize("separate", [True, False])
async def test_attachment_reads_verified_part_bytes_with_one_connection(tmp_path, separate):
    content = b"Rental service package"
    payload = message(content, separate=separate)
    calls = []

    def serve(request):
        calls.append(request)
        if request.url.path.endswith("/message1"):
            assert request.url.params["format"] == "full"
            return httpx.Response(200, json=payload)
        assert request.url.path.endswith("/message1/attachments/proven-attachment")
        return httpx.Response(200, json={"size": len(content), "data": encoded(content)})

    google = GoogleConnector(tmp_path, credential_loader=lambda: "fixture", transport=httpx.MockTransport(serve))
    parsed = google._message(payload)["attachments"]
    assert parsed[0]["part_id"] == "1"
    assert parsed[0]["attachment_id"] == ("proven-attachment" if separate else None)
    result = await google.attachment("message1", "1")
    assert result["filename"] == "rental.txt" and result["content"] == content
    assert result["connection_binding"] == google.gmail.binding()
    assert len(calls) == (2 if separate else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation,code", [
    ("missing", "attachment_not_found"), ("duplicate", "attachment_not_found"),
    ("oversize", "attachment_too_large"), ("negative", "attachment_too_large"),
    ("wrong_message", "invalid_attachment"), ("size_mismatch", "invalid_attachment"),
    ("invalid_base64", "invalid_attachment"), ("traversal", "invalid_attachment"),
])
async def test_bad_attachment_metadata_or_content_never_imported(tmp_path, mutation, code):
    payload = message(separate=False)
    part = payload["payload"]["parts"][1]
    part_id = "1"
    if mutation == "missing":
        part_id = "2"
    elif mutation == "duplicate":
        payload["payload"]["parts"].append(part.copy())
    elif mutation == "oversize":
        part["body"]["size"] = MAX_ATTACHMENT_BYTES + 1
    elif mutation == "negative":
        part["body"]["size"] = -1
    elif mutation == "wrong_message":
        payload["id"] = "other"
    elif mutation == "size_mismatch":
        part["body"]["size"] += 1
    elif mutation == "invalid_base64":
        part["body"]["data"] = "**invalid**"
    else:
        part_id = "../1"
    google = GoogleConnector(tmp_path, credential_loader=lambda: "fixture", transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)))
    with pytest.raises(IntegrationError) as error:
        await google.attachment("message1", part_id)
    assert error.value.code == code
    assert not (tmp_path / "files").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("switch_at", ["message", "attachment"])
async def test_attachment_rejects_account_switch_before_followup_or_return(tmp_path, switch_at):
    google = None
    calls = []

    def serve(request):
        calls.append(request)
        is_message = request.url.path.endswith("/message1")
        if is_message == (switch_at == "message"):
            google.gmail.injected_generation = "different-connection"
        return httpx.Response(200, json=message() if is_message else {"size": 22, "data": encoded(b"Rental service package")})

    google = GoogleConnector(tmp_path, credential_loader=lambda: "fixture", transport=httpx.MockTransport(serve))
    with pytest.raises(IntegrationError) as error:
        await google.attachment("message1", "1")
    assert error.value.code == "approval_connection_changed"
    assert len(calls) == (1 if switch_at == "message" else 2)


def test_download_and_import_use_provider_filename_and_existing_managed_ingestion(tmp_path):
    payload = message(separate=False, filename="../rental.txt")
    service = IntegrationServices(tmp_path, lambda key, default: default, lambda key, value: None)
    service.google = GoogleConnector(tmp_path, credential_loader=lambda: "fixture", transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)))
    app = FastAPI()
    app.state.database = Database(tmp_path)
    app.state.database.migrate()
    app.state.data_dir = tmp_path.resolve()
    app.include_router(create_integrations_router(service))
    app.include_router(files_router)
    route = "/api/google/gmail/messages/message1/attachments/1"
    with TestClient(app) as client:
        download = client.get(route + "/content")
        assert download.status_code == 200 and download.content == b"Rental service package"
        assert download.headers["content-disposition"] == "attachment; filename*=UTF-8''rental.txt"
        assert download.headers["content-type"] == "application/octet-stream"
        imported = client.post(route + "/import").json()
        assert imported["filename"] == "rental.txt" and imported["source"] == "gmail"
        assert imported["parse_status"] == "ready"
        assert Path(imported["path"]).is_relative_to(tmp_path)
        record = client.get("/api/files/" + imported["id"]).json()
        assert record["chunks"][0]["text"] == "Rental service package"
        assert client.post(route + "/import").json()["id"] == imported["id"]
        assert len(client.get("/api/files").json()) == 1
