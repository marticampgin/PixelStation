from __future__ import annotations

import asyncio
import base64
import io
import json
import sqlite3
import time
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from pixel_station.integrations import (
    ApprovalStore,
    IntegrationServices,
    create_integrations_router,
)
from pixel_station.providers.comfy import ComfyImageProvider
from pixel_station.providers.google import GoogleConnector
from pixel_station.providers.web import IntegrationError, WebProvider


def event() -> dict:
    return {"summary": "Dentist", "start": {"dateTime": "2026-10-09T14:00:00+03:00"},
            "end": {"dateTime": "2026-10-09T15:00:00+03:00"}}


def services(tmp_path: Path) -> IntegrationServices:
    settings = {}
    return IntegrationServices(tmp_path, lambda key, default: settings.get(key, default), settings.__setitem__)


def test_image_workflow_default_is_validated_and_persisted(tmp_path: Path) -> None:
    service = services(tmp_path)
    app = FastAPI()
    app.include_router(create_integrations_router(service))
    workflow = {"1": {"class_type": "CLIPTextEncode", "inputs": {"text": "original"}}}
    bindings = {"prompt": {"node": "1", "input": "text"}}
    with TestClient(app) as client:
        first = client.post("/api/images/workflows", json={"name": "First", "workflow": workflow, "bindings": bindings}).json()
        second = client.post("/api/images/workflows", json={"name": "Second", "workflow": workflow, "bindings": bindings}).json()
        assert client.get("/api/images/workflows").json()["default_workflow"] == first["id"]
        assert client.put("/api/images/workflows/default", json={"workflow_id": "missing"}).status_code == 404
        response = client.put("/api/images/workflows/default", json={"workflow_id": second["id"]})
        assert response.status_code == 200
        assert response.json()["default_workflow_id"] == second["id"]
        assert service.get_setting("comfyui_default_workflow", "") == second["id"]
        assert client.delete(f"/api/images/workflows/{second['id']}").status_code == 200
        assert client.get("/api/images/workflows").json()["default_workflow"] == first["id"]


def test_approval_exact_payload_and_single_use(tmp_path: Path) -> None:
    store = ApprovalStore(tmp_path / "approvals.db")
    payload = {"to": "client@example.com", "subject": "Approved", "body": "Exact content"}
    proposal = store.propose("gmail_send", payload)
    payload["body"] = "Changed after proposal"
    action, actual = store.consume(proposal["id"])
    assert action == "gmail_send"
    assert actual["body"] == "Exact content"
    with pytest.raises(IntegrationError, match="already been used"):
        store.consume(proposal["id"])


def test_expired_rejected_and_tampered_approvals_are_blocked(tmp_path: Path) -> None:
    store = ApprovalStore(tmp_path / "approvals.db", ttl=-1)
    expired = store.propose("calendar_create", {"event": event()})
    with pytest.raises(IntegrationError):
        store.consume(expired["id"])
    store.ttl = 600
    rejected = store.propose("gmail_send", {})
    store.reject(rejected["id"])
    with pytest.raises(IntegrationError):
        store.consume(rejected["id"])
    tampered = store.propose("gmail_send", {"body": "reviewed"})
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE approvals SET payload=? WHERE id=?", ('{"body":"unreviewed"}', tampered["id"]))
    with pytest.raises(IntegrationError, match="altered"):
        store.consume(tampered["id"])


@pytest.mark.asyncio
async def test_search_json_dedup_and_query_boundary() -> None:
    def serve(request: httpx.Request) -> httpx.Response:
        assert request.url.params["format"] == "json"
        assert request.url.params["q"] == "actual query"
        return httpx.Response(200, json={"results": [
            {"url": "https://example.com/a?utm_source=test", "title": "First"},
            {"url": "https://example.com/a", "title": "Duplicate"},
            {"url": "file:///private", "title": "Bad"},
            {"url": "https://example.org/b", "title": "Other"}]})
    provider = WebProvider(lambda: "http://127.0.0.1:8888", transport=httpx.MockTransport(serve))
    results = await provider.search("actual query")
    assert [r["title"] for r in results] == ["First", "Other"]


@pytest.mark.asyncio
async def test_missing_service_and_disabled_json_are_actionable() -> None:
    with pytest.raises(IntegrationError, match="Configure SearXNG"):
        await WebProvider(lambda: "").search("test")
    provider = WebProvider(lambda: "http://127.0.0.1:8888", transport=httpx.MockTransport(lambda _: httpx.Response(403)))
    with pytest.raises(IntegrationError, match="JSON search is disabled"):
        await provider.search("test")


@pytest.mark.asyncio
@pytest.mark.parametrize("url,ips", [
    ("file:///etc/passwd", ["93.184.216.34"]), ("http://localhost/private", ["127.0.0.1"]),
    ("http://example.com", ["93.184.216.34", "192.168.0.1"]),
    ("http://example.com:8000", ["93.184.216.34"]),
    ("https://user:password@example.com", ["93.184.216.34"]),
    ("http://169.254.169.254/latest", ["169.254.169.254"]),
])
async def test_public_fetch_blocks_ssrf(url: str, ips: list[str]) -> None:
    provider = WebProvider(lambda: "", resolver=lambda *_: ips)
    with pytest.raises(IntegrationError, match="public HTTP"):
        await provider.fetch(url)


@pytest.mark.asyncio
async def test_redirect_revalidation_and_pinned_address() -> None:
    def serve(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "93.184.216.34"
        assert request.headers["Host"] == "example.com"
        assert request.extensions["sni_hostname"] == "example.com"
        return httpx.Response(302, headers={"location": "http://localhost/secret"})
    provider = WebProvider(lambda: "", transport=httpx.MockTransport(serve),
                           resolver=lambda host, _: ["127.0.0.1"] if host == "localhost" else ["93.184.216.34"])
    with pytest.raises(IntegrationError, match="public HTTP"):
        await provider.fetch("https://example.com")


@pytest.mark.asyncio
async def test_web_content_extraction_and_download_limits() -> None:
    provider = WebProvider(lambda: "", resolver=lambda *_: ["93.184.216.34"], transport=httpx.MockTransport(
        lambda _: httpx.Response(200, headers={"content-type": "text/html"}, text="<title>Source</title><script>private script</script><article><h1>Actual source</h1><p>Evidence text.</p></article>")))
    page = await provider.fetch("https://example.com")
    assert page["title"] == "Source"
    assert "Evidence text." in page["text"] and "private script" not in page["text"]
    oversized = WebProvider(lambda: "", resolver=lambda *_: ["93.184.216.34"], transport=httpx.MockTransport(
        lambda _: httpx.Response(200, headers={"content-type": "text/plain", "content-length": "3000000"}, text="too large")))
    with pytest.raises(IntegrationError, match="2 MB"):
        await oversized.fetch("https://example.com")


@pytest.mark.asyncio
async def test_research_preserves_actual_sources_and_fetch_failure() -> None:
    def serve(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search":
            return httpx.Response(200, json={"results": [{"url": "https://example.com", "title": "Original", "content": "Search snippet"}]})
        return httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"pdf")
    provider = WebProvider(lambda: "http://127.0.0.1:8888", resolver=lambda *_: ["93.184.216.34"], transport=httpx.MockTransport(serve))
    result = await provider.research(["one", "two"])
    assert len(result["sources"]) == 1
    assert result["sources"][0]["url"] == "https://example.com/"
    assert result["sources"][0]["fetch_error"]
    assert "Search snippet" in result["context"]


@pytest.mark.asyncio
@pytest.mark.parametrize("budget", [1, 2, 3, 4, 6, 12])
async def test_research_tool_budget_counts_searches_and_fetches(budget: int) -> None:
    calls = {"search": 0, "fetch": 0}

    def serve(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search":
            calls["search"] += 1
            return httpx.Response(200, json={"results": [{"url": f"https://example.com/source{i}", "title": f"Source {i}"} for i in range(4)]})
        calls["fetch"] += 1
        return httpx.Response(200, headers={"content-type": "text/plain"}, text="Actual evidence")

    provider = WebProvider(lambda: "http://127.0.0.1:8888", transport=httpx.MockTransport(serve), resolver=lambda *_: ["93.184.216.34"])
    result = await provider.research(["one", "two", "three", "four"], limit=6, max_steps=budget)
    assert calls["search"] + calls["fetch"] <= budget
    assert result["tool_steps"]["total"] == calls["search"] + calls["fetch"]
    assert result["tool_steps"]["search"] == calls["search"]
    assert result["tool_steps"]["fetch"] == calls["fetch"]
    if budget == 1:
        assert calls == {"search": 1, "fetch": 0}
    if budget >= 3:
        assert 2 <= calls["search"] <= 4 and calls["fetch"] >= 1


def workflow() -> tuple[dict, dict]:
    return {"6": {"class_type": "CLIPTextEncode", "inputs": {"text": "old"}},
            "3": {"class_type": "KSampler", "inputs": {"seed": 0}}}, {"prompt": {"node": "6", "input": "text"}, "seed": {"node": "3", "input": "seed"}}


@pytest.mark.asyncio
async def test_comfy_executes_actual_workflow_and_persists_image(tmp_path: Path) -> None:
    img = io.BytesIO()
    Image.new("RGB", (32, 24), "purple").save(img, "PNG")
    def serve(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/prompt":
            body = json.loads(request.content)
            assert body["prompt"]["6"]["inputs"]["text"] == "Exact user prompt"
            assert body["prompt"]["3"]["inputs"]["seed"] == 42
            return httpx.Response(200, json={"prompt_id": "real-prompt"})
        if request.url.path == "/history/real-prompt":
            return httpx.Response(200, json={"real-prompt": {"status": {"completed": True}, "outputs": {"9": {"images": [{"filename": "output.png", "subfolder": "", "type": "output"}]}}}})
        if request.url.path == "/view":
            return httpx.Response(200, content=img.getvalue())
        raise AssertionError(str(request.url))
    provider = ComfyImageProvider(tmp_path, lambda: "http://127.0.0.1:8188", lambda: "", transport=httpx.MockTransport(serve))
    graph, bindings = workflow()
    imported = provider.import_workflow("Test workflow", graph, bindings)
    result = await provider.generate("Exact user prompt", workflow_id=imported["id"], seed=42)
    assert result["status"] == "complete" and result["progress"] == 1
    saved = result["images"][0]
    assert (saved["width"], saved["height"]) == (32, 24)
    assert provider.image_path(saved["id"]).read_bytes() == img.getvalue()
    reopened = ComfyImageProvider(tmp_path, lambda: "", lambda: "")
    assert reopened.library()[0]["id"] == saved["id"]


@pytest.mark.asyncio
async def test_cancelling_chat_image_await_stops_its_remote_comfy_prompt(tmp_path: Path) -> None:
    poll_started = asyncio.Event()
    calls = []

    async def serve(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "own-prompt"})
        if request.url.path == "/history/own-prompt":
            poll_started.set()
            return httpx.Response(200, json={})
        if request.url.path == "/queue" and request.method == "GET":
            return httpx.Response(200, json={"queue_running": [[0, "own-prompt", {}, {}]]})
        return httpx.Response(200, json={})

    provider = ComfyImageProvider(tmp_path, lambda: "http://127.0.0.1:8188", lambda: "", transport=httpx.MockTransport(serve))
    graph, bindings = workflow()
    imported = provider.import_workflow("Test workflow", graph, bindings)
    task = asyncio.create_task(provider.generate("Actual prompt", workflow_id=imported["id"]))
    await asyncio.wait_for(poll_started.wait(), timeout=1)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert ("POST", "/queue") in calls
    assert ("POST", "/interrupt") in calls
    with provider.db() as db:
        row = db.execute("SELECT status FROM jobs").fetchone()
    assert row["status"] == "cancelled"


def test_workflow_binding_validation_and_restart_recovery(tmp_path: Path) -> None:
    provider = ComfyImageProvider(tmp_path, lambda: "", lambda: "")
    with pytest.raises(IntegrationError, match="API format"):
        provider.import_workflow("Canvas", {"nodes": []}, {})
    graph, _ = workflow()
    with pytest.raises(IntegrationError, match="unknown node"):
        provider.import_workflow("Invalid", graph, {"prompt": {"node": "missing", "input": "text"}})
    with provider.db() as db:
        db.execute("INSERT INTO jobs(id,payload,status,created_at) VALUES('interrupted','{}','running','date')")
    reopened = ComfyImageProvider(tmp_path, lambda: "", lambda: "")
    assert reopened.job("interrupted")["status"] == "interrupted"


@pytest.mark.asyncio
async def test_google_send_only_after_confirmation_and_no_replay(tmp_path: Path) -> None:
    app_services = services(tmp_path)
    calls = []
    def serve(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer test-token"
        decoded = base64.urlsafe_b64decode(json.loads(request.content)["raw"]).decode()
        assert "To: client@example.com" in decoded and "Reviewed content" in decoded
        return httpx.Response(200, json={"id": "sent-id"})
    app_services.google = GoogleConnector(tmp_path, transport=httpx.MockTransport(serve), credential_loader=lambda: "test-token")
    proposal = await app_services.propose_action("gmail_send", {"to": "client@example.com", "subject": "Subject", "body": "Reviewed content"})
    assert not calls
    result = await app_services.confirm(proposal["approval"]["id"])
    assert result["result"]["id"] == "sent-id" and len(calls) == 1
    with pytest.raises(IntegrationError):
        await app_services.confirm(proposal["approval"]["id"])
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_calendar_update_captures_original_and_etag(tmp_path: Path) -> None:
    app_services = services(tmp_path)
    calls = []
    def serve(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        if request.method == "PATCH":
            assert request.headers["If-Match"] == '"original-etag"'
            return httpx.Response(412)
        return httpx.Response(200, json={"id": "event-id", "etag": '"original-etag"', "summary": "Original", "start": {}, "end": {}})
    app_services.google = GoogleConnector(tmp_path, transport=httpx.MockTransport(serve), credential_loader=lambda: "test-token")
    proposal = await app_services.propose_action("calendar_update", {"calendar_id": "primary", "event_id": "event-id", "event": event()})
    assert calls == ["GET"]
    assert proposal["approval"]["payload"]["original"]["summary"] == "Original"
    with pytest.raises(IntegrationError, match="changed since"):
        await app_services.confirm(proposal["approval"]["id"])
    assert calls == ["GET", "PATCH"]


@pytest.mark.asyncio
async def test_google_draft_verifies_real_id_and_decodes_thread(tmp_path: Path) -> None:
    def serve(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/drafts"):
            return httpx.Response(200, json={"id": "draft-id", "message": {"id": "message-id"}})
        if request.url.path.endswith("/drafts/draft-id"):
            return httpx.Response(200, json={"id": "draft-id"})
        assert request.url.path.endswith("/threads/thread-id")
        return httpx.Response(200, json={"id": "thread-id", "messages": [{"id": "message-id", "payload": {"headers": [{"name": "Subject", "value": "Client mail"}],
             "mimeType": "text/plain", "body": {"data": base64.urlsafe_b64encode(b"Private client body").decode()}}}]})
    connector = GoogleConnector(tmp_path, transport=httpx.MockTransport(serve), credential_loader=lambda: "test-token")
    draft = await connector.create_draft(to="client@example.com", subject="Reply", body="Draft")
    assert draft["id"] == "draft-id"
    thread = await connector.thread("thread-id")
    assert thread["messages"][0]["body"] == "Private client body"


def test_google_rejects_header_injection_unsafe_credentials_and_invalid_dates(tmp_path: Path) -> None:
    connector = GoogleConnector(tmp_path)
    with pytest.raises(IntegrationError):
        connector.email_payload("client@example.com\nBcc: stranger@example.org", "Subject", "body")
    with pytest.raises(IntegrationError, match="official Google"):
        connector.import_credentials({"installed": {"client_id": "id", "client_secret": "secret", "token_uri": "http://localhost/private"}})
    invalid = event()
    invalid["end"] = invalid["start"]
    with pytest.raises(IntegrationError, match="end must follow"):
        connector.validate_event(invalid)


@pytest.mark.asyncio
async def test_google_oauth_state_expires_and_is_single_use(tmp_path: Path) -> None:
    connector = GoogleConnector(tmp_path)
    connector.pending["expired"] = (time.monotonic() - 1, object())
    with pytest.raises(IntegrationError, match="expired"):
        await connector.complete_authorization("expired", "code")
    assert "expired" not in connector.pending
    with pytest.raises(IntegrationError):
        await connector.complete_authorization("missing", "code")


def test_http_confirmation_requires_true_and_reject_does_not_send(tmp_path: Path) -> None:
    app_services = services(tmp_path)
    app = FastAPI()
    app.include_router(create_integrations_router(app_services))
    with TestClient(app) as client:
        proposed = client.post("/api/google/gmail/send", json={"to": "client@example.com", "subject": "Review", "body": "body"})
        id_ = proposed.json()["approval"]["id"]
        assert client.post(f"/api/integrations/approvals/{id_}/confirm", json={"confirmed": False}).status_code == 422
        assert client.delete(f"/api/integrations/approvals/{id_}").status_code == 200
        assert client.post(f"/api/integrations/approvals/{id_}/confirm", json={"confirmed": True}).status_code == 409
        assert not app_services.approvals.pending()
