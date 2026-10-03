"""Optional integration services and HTTP routes. User data stays under data_dir."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from .providers.comfy import ComfyImageProvider
from .providers.google import GoogleConnector
from .providers.web import IntegrationError, WebProvider


class IntegrationRoute(APIRoute):
    def get_route_handler(self) -> Callable[..., Any]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Any:
            try:
                return await original(request)
            except IntegrationError as exc:
                raise HTTPException(status_code=exc.status, detail={"message": str(exc), "code": exc.code}) from exc
        return handler


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchInput(Input):
    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=8, ge=1, le=20)


class FetchInput(Input):
    url: str = Field(min_length=8, max_length=4000)


class ResearchInput(Input):
    queries: list[str] = Field(min_length=1, max_length=4)
    limit: int = Field(default=4, ge=1, le=6)


class WorkflowInput(Input):
    name: str = Field(min_length=1, max_length=120)
    workflow: dict[str, Any]
    bindings: dict[str, Any]


class GenerationInput(Input):
    prompt: str = Field(min_length=1, max_length=10_000)
    workflow_id: str | None = None
    seed: int | None = Field(default=None, ge=0, lt=2**53)
    width: int = Field(default=512, ge=256, le=2048)
    height: int = Field(default=512, ge=256, le=2048)


class CredentialsInput(Input):
    credentials: dict[str, Any]


class EmailInput(Input):
    to: str = Field(min_length=3, max_length=2000)
    subject: str = Field(min_length=1, max_length=1000)
    body: str = Field(max_length=200_000)
    thread_id: str | None = Field(default=None, max_length=200)
    in_reply_to: str | None = Field(default=None, max_length=1000)


class EventInput(Input):
    calendar_id: str = Field(default="primary", min_length=1, max_length=500)
    event: dict[str, Any]


class ConfirmInput(Input):
    confirmed: Literal[True]


class ApprovalStore:
    """An immutable, expiring proposal; atomic consume prevents replay and races."""

    ACTIONS = {"gmail_send", "calendar_create", "calendar_update", "calendar_delete"}

    def __init__(self, path: Path, ttl: int = 600) -> None:
        self.path, self.ttl = path, ttl
        with self.db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS approvals(
                id TEXT PRIMARY KEY, action TEXT NOT NULL, payload TEXT NOT NULL, digest TEXT NOT NULL,
                expires_at REAL NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, result TEXT)
            """)

    def db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def propose(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        if action not in self.ACTIONS:
            raise IntegrationError("This action cannot be approved through the integration API.", "invalid_action", 422)
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        id_, deadline = str(uuid.uuid4()), time.time() + self.ttl
        with self.db() as db:
            db.execute("INSERT INTO approvals VALUES(?,?,?,?,?,?,?,NULL)",
                       (id_, action, canonical, hashlib.sha256(canonical.encode()).hexdigest(), deadline, "pending", datetime.now(UTC).isoformat()))
        return {"id": id_, "action": action, "payload": payload, "expires_at": datetime.fromtimestamp(deadline, UTC).isoformat()}

    def pending(self) -> list[dict[str, Any]]:
        with self.db() as db:
            rows = db.execute("SELECT * FROM approvals WHERE status='pending' AND expires_at>? ORDER BY created_at DESC", (time.time(),)).fetchall()
        return [{"id": row["id"], "action": row["action"], "payload": json.loads(row["payload"]),
                 "expires_at": datetime.fromtimestamp(row["expires_at"], UTC).isoformat()} for row in rows]

    def consume(self, id_: str) -> tuple[str, dict[str, Any]]:
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM approvals WHERE id=?", (id_,)).fetchone()
            if not row or row["status"] != "pending" or row["expires_at"] <= time.time():
                raise IntegrationError("Approval expired, was rejected, or has already been used. Review a new proposal.", "approval_unavailable", 409)
            if hashlib.sha256(row["payload"].encode()).hexdigest() != row["digest"]:
                raise IntegrationError("The proposal was altered. Review a new proposal.", "approval_changed", 409)
            db.execute("UPDATE approvals SET status='consumed' WHERE id=?", (id_,))
        return row["action"], json.loads(row["payload"])

    def reject(self, id_: str) -> None:
        with self.db() as db:
            if not db.execute("UPDATE approvals SET status='rejected' WHERE id=? AND status='pending'", (id_,)).rowcount:
                raise IntegrationError("Pending approval not found.", "not_found", 404)

    def result(self, id_: str, value: dict[str, Any], success: bool = True) -> None:
        with self.db() as db:
            db.execute("UPDATE approvals SET status=?,result=? WHERE id=?", ("complete" if success else "failed", json.dumps(value), id_))


class IntegrationServices:
    def __init__(self, data_dir: Path, get_setting: Callable[[str, Any], Any], set_setting: Callable[[str, Any], None]) -> None:
        data_dir = Path(data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        self.get_setting, self.set_setting = get_setting, set_setting
        self.web = WebProvider(lambda: str(get_setting("searxng_url", "http://127.0.0.1:8888") or ""))
        self.images = ComfyImageProvider(data_dir, lambda: str(get_setting("comfyui_url", "http://127.0.0.1:8188") or ""),
                                        lambda: str(get_setting("comfyui_default_workflow", "") or ""))
        self.google = GoogleConnector(data_dir)
        self.approvals = ApprovalStore(data_dir / "integration_approvals.sqlite3")

    async def propose_action(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        if action == "gmail_send":
            payload = EmailInput.model_validate(payload).model_dump()
            self.google.email_payload(**payload)
        elif action in {"calendar_create", "calendar_update", "calendar_delete"}:
            if action != "calendar_delete":
                self.google.validate_event(payload.get("event", {}))
            if action != "calendar_create":
                original = await self.google.event(payload.get("calendar_id", "primary"), payload["event_id"])
                payload = {**payload, "etag": original.get("etag"), "original": {key: original.get(key) for key in ["summary", "start", "end", "location"]}}
        else:
            raise IntegrationError("Unsupported consequential action.", "invalid_action", 422)
        return {"approval": self.approvals.propose(action, payload)}

    async def confirm(self, id_: str) -> dict[str, Any]:
        action, payload = self.approvals.consume(id_)
        try:
            if action == "gmail_send":
                result = await self.google.send_email(**payload)
            else:
                result = await self.google.mutate_event(action, **payload)
            self.approvals.result(id_, result)
            return {"result": result}
        except IntegrationError as exc:
            self.approvals.result(id_, {"error": str(exc)}, success=False)
            raise

    async def chat_context(self, route: str, prompt: str) -> dict[str, Any]:
        """Tool integration hook: model synthesis receives evidence, never invented success."""
        if route in {"web", "web_search", "web_research", "search"}:
            evidence = await self.web.research([prompt], limit=4)
            return {"content": evidence["context"], "sources": evidence["sources"]}
        if route in {"image", "image_generate"}:
            result = await self.images.generate(prompt)
            return {"content": "Generated image saved to the local Image Studio library.", "image": result["images"][0] if result["images"] else None,
                    "images": result["images"]}
        if route in {"gmail", "gmail_search", "gmail_read"}:
            result = await self.google.threads("")
            return {"content": json.dumps(result), "threads": result["threads"]}
        if route in {"calendar", "calendar_read"}:
            start = datetime.now(UTC)
            result = await self.google.events("primary", start.isoformat(), (start + timedelta(days=7)).isoformat())
            return {"content": json.dumps(result), "events": result.get("items", [])}
        raise IntegrationError("This integration intent needs typed tool arguments. Open its workspace or construct a validated tool request.", "arguments_required", 422)

    async def close(self) -> None:
        await self.images.close()


def create_integrations_router(services: IntegrationServices) -> APIRouter:
    router = APIRouter(route_class=IntegrationRoute)

    @router.get("/api/web/status")
    async def web_status() -> dict[str, Any]:
        return await services.web.status()

    @router.post("/api/web/search")
    async def search(body: SearchInput) -> dict[str, Any]:
        return {"results": await services.web.search(body.query, body.limit)}

    @router.post("/api/web/fetch")
    async def fetch(body: FetchInput) -> dict[str, Any]:
        return await services.web.fetch(body.url)

    @router.post("/api/web/research")
    async def research(body: ResearchInput) -> dict[str, Any]:
        return await services.web.research(body.queries, body.limit)

    @router.get("/api/images/status")
    async def image_status() -> dict[str, Any]:
        return await services.images.status()

    @router.get("/api/images/workflows")
    async def workflows() -> dict[str, Any]:
        return {"workflows": services.images.workflows(), "default_workflow": services.get_setting("comfyui_default_workflow", "")}

    @router.post("/api/images/workflows")
    async def import_workflow(body: WorkflowInput) -> dict[str, Any]:
        result = services.images.import_workflow(body.name, body.workflow, body.bindings)
        if not services.get_setting("comfyui_default_workflow", ""):
            services.set_setting("comfyui_default_workflow", result["id"])
        return result

    @router.delete("/api/images/workflows/{id_}")
    async def delete_workflow(id_: str) -> dict[str, Any]:
        services.images.delete_workflow(id_)
        if services.get_setting("comfyui_default_workflow", "") == id_:
            remaining = services.images.workflows()
            services.set_setting("comfyui_default_workflow", remaining[0]["id"] if remaining else "")
        return {"deleted": True}

    @router.post("/api/images/generate")
    async def generate(body: GenerationInput) -> dict[str, Any]:
        return await services.images.start_generation(**body.model_dump())

    @router.get("/api/images/jobs/{id_}")
    async def job(id_: str) -> dict[str, Any]:
        return services.images.job(id_)

    @router.delete("/api/images/jobs/{id_}")
    async def cancel_job(id_: str) -> dict[str, Any]:
        return await services.images.cancel(id_)

    @router.get("/api/images/library")
    async def library() -> dict[str, Any]:
        return {"images": services.images.library()}

    @router.get("/api/images/{id_}/content")
    async def content(id_: str) -> FileResponse:
        return FileResponse(services.images.image_path(id_))

    @router.delete("/api/images/{id_}")
    async def delete_image(id_: str) -> dict[str, Any]:
        services.images.delete_image(id_)
        return {"deleted": True}

    @router.get("/api/google/status")
    async def google_status() -> dict[str, Any]:
        return services.google.status()

    @router.post("/api/google/credentials")
    async def credentials(body: CredentialsInput) -> dict[str, Any]:
        return services.google.import_credentials(body.credentials)

    @router.get("/api/google/authorize")
    async def authorize(request: Request) -> dict[str, Any]:
        return services.google.authorize(str(request.url_for("google_callback")))

    @router.get("/api/google/callback", name="google_callback")
    async def callback(state: str = "", code: str = "", error: str = "") -> HTMLResponse:
        if error:
            services.google.pending.pop(state, None)
            return HTMLResponse("<meta charset='utf-8'><title>Pixel Station</title><h1>Google access was not granted</h1><p>Return to Pixel Station Settings to reconnect.</p>", status_code=400)
        await services.google.complete_authorization(state, code)
        return HTMLResponse("<meta charset='utf-8'><title>Pixel Station</title><h1>Google connected</h1><p>You can close this tab and return to Pixel Station.</p>")

    @router.post("/api/google/disconnect")
    async def disconnect() -> dict[str, Any]:
        services.google.disconnect()
        return services.google.status()

    @router.get("/api/google/gmail/threads")
    async def threads(q: str = "", page_token: str = "") -> dict[str, Any]:
        return await services.google.threads(q, page_token)

    @router.get("/api/google/gmail/threads/{id_}")
    async def thread(id_: str) -> dict[str, Any]:
        return await services.google.thread(id_)

    @router.post("/api/google/gmail/drafts")
    async def draft(body: EmailInput) -> dict[str, Any]:
        return await services.google.create_draft(**body.model_dump())

    @router.post("/api/google/gmail/send")
    async def send(body: EmailInput) -> dict[str, Any]:
        return await services.propose_action("gmail_send", body.model_dump())

    @router.get("/api/google/calendar/calendars")
    async def calendars() -> dict[str, Any]:
        return await services.google.calendars()

    @router.get("/api/google/calendar/events")
    async def events(time_min: str, time_max: str, calendar_id: str = "primary", q: str = "") -> dict[str, Any]:
        return await services.google.events(calendar_id, time_min, time_max, q)

    @router.post("/api/google/calendar/events")
    async def create_event(body: EventInput) -> dict[str, Any]:
        return await services.propose_action("calendar_create", body.model_dump())

    @router.patch("/api/google/calendar/events/{id_}")
    async def update_event(id_: str, body: EventInput) -> dict[str, Any]:
        return await services.propose_action("calendar_update", {**body.model_dump(), "event_id": id_})

    @router.delete("/api/google/calendar/events/{id_}")
    async def delete_event(id_: str, calendar_id: str = "primary") -> dict[str, Any]:
        return await services.propose_action("calendar_delete", {"calendar_id": calendar_id, "event_id": id_})

    @router.get("/api/integrations/approvals")
    async def approvals() -> dict[str, Any]:
        return {"approvals": services.approvals.pending()}

    @router.post("/api/integrations/approvals/{id_}/confirm")
    async def confirm(id_: str, body: ConfirmInput) -> dict[str, Any]:
        return await services.confirm(id_)

    @router.delete("/api/integrations/approvals/{id_}")
    async def reject(id_: str) -> dict[str, Any]:
        services.approvals.reject(id_)
        return {"rejected": True}

    return router
