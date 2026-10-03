"""ComfyUI API workflows, bounded jobs, progress and a persistent local image library."""

from __future__ import annotations

import asyncio
import copy
import io
import json
import secrets
import sqlite3
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx
from PIL import Image

from .web import IntegrationError, endpoint_url


def now() -> str:
    return datetime.now(UTC).isoformat()


class ComfyImageProvider:
    def __init__(self, data_dir: Path, endpoint: Callable[[], str], default_workflow: Callable[[], str],
                 *, transport: httpx.AsyncBaseTransport | None = None, timeout: float = 600) -> None:
        self.root = (data_dir / "images").resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "library.sqlite3"
        self.endpoint, self.default_workflow = endpoint, default_workflow
        self.transport, self.timeout = transport, timeout
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.lock = asyncio.Lock()
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS workflows(id TEXT PRIMARY KEY,name TEXT NOT NULL,workflow TEXT NOT NULL,bindings TEXT NOT NULL,created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,payload TEXT NOT NULL,status TEXT NOT NULL,progress REAL NOT NULL DEFAULT 0,prompt_id TEXT,error TEXT,created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS images(id TEXT PRIMARY KEY,job_id TEXT NOT NULL,path TEXT NOT NULL,prompt TEXT NOT NULL,seed INTEGER NOT NULL,width INTEGER NOT NULL,height INTEGER NOT NULL,workflow_id TEXT NOT NULL,created_at TEXT NOT NULL);
                UPDATE jobs SET status='interrupted',error='Generation interrupted by application restart. Regenerate to try again.' WHERE status IN ('queued','running');
            """)

    def db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        return db

    async def status(self) -> dict[str, Any]:
        value = self.endpoint()
        try:
            if not value:
                raise IntegrationError("missing")
            async with httpx.AsyncClient(timeout=5, transport=self.transport, trust_env=False) as client:
                response = await client.get(endpoint_url(value) + "/system_stats")
                response.raise_for_status()
                stats = response.json()
            return {"available": True, "endpoint": value, "message": "ComfyUI connected. Import an API workflow with explicit prompt/seed bindings.", "devices": stats.get("devices", [])}
        except (httpx.HTTPError, ValueError, IntegrationError):
            return {"available": False, "endpoint": value, "message": "Start ComfyUI locally (default http://127.0.0.1:8188), then import an API workflow. See docs/INTEGRATIONS.md."}

    def workflows(self) -> list[dict[str, Any]]:
        with self.db() as db:
            rows = db.execute("SELECT id,name,bindings,created_at FROM workflows ORDER BY created_at DESC").fetchall()
        return [{**dict(row), "bindings": json.loads(row["bindings"])} for row in rows]

    def import_workflow(self, name: str, workflow: dict[str, Any], bindings: dict[str, Any]) -> dict[str, Any]:
        if not name.strip() or len(name) > 120 or not workflow or len(json.dumps(workflow)) > 2_000_000:
            raise IntegrationError("Provide a workflow name and ComfyUI API workflow JSON under 2 MB.", "invalid_workflow", 422)
        if "nodes" in workflow or any(not isinstance(node, dict) or "class_type" not in node or not isinstance(node.get("inputs"), dict) for node in workflow.values()):
            raise IntegrationError("Export ComfyUI in API format; canvas workflow JSON cannot be submitted to /prompt.", "invalid_workflow", 422)
        if "prompt" not in bindings:
            raise IntegrationError("Bind the positive prompt node and input explicitly.", "missing_binding", 422)
        for key, binding in bindings.items():
            if key not in {"prompt", "seed", "width", "height"} or not isinstance(binding, dict):
                raise IntegrationError("Supported bindings are prompt, seed, width and height.", "invalid_binding", 422)
            node = workflow.get(str(binding.get("node", "")), {})
            if binding.get("input") not in node.get("inputs", {}):
                raise IntegrationError(f"Binding {key} references an unknown node/input.", "invalid_binding", 422)
        id_ = str(uuid.uuid4())
        with self.db() as db:
            db.execute("INSERT INTO workflows VALUES(?,?,?,?,?)", (id_, name.strip(), json.dumps(workflow), json.dumps(bindings), now()))
        return next(row for row in self.workflows() if row["id"] == id_)

    def delete_workflow(self, id_: str) -> None:
        with self.db() as db:
            active = db.execute("SELECT payload FROM jobs WHERE status IN ('queued','running')").fetchall()
            if any(json.loads(row[0]).get("workflow_id") == id_ for row in active):
                raise IntegrationError("Wait for this workflow's generation to finish before deleting it.", "workflow_in_use", 409)
            if not db.execute("DELETE FROM workflows WHERE id=?", (id_,)).rowcount:
                raise IntegrationError("Workflow not found.", "not_found", 404)

    def _workflow(self, id_: str) -> tuple[dict[str, Any], dict[str, Any]]:
        with self.db() as db:
            row = db.execute("SELECT workflow,bindings FROM workflows WHERE id=?", (id_,)).fetchone()
        if not row:
            raise IntegrationError("Import a compatible ComfyUI API workflow and choose it before generating.", "workflow_missing", 422)
        return json.loads(row[0]), json.loads(row[1])

    async def start_generation(self, prompt: str, workflow_id: str | None = None, seed: int | None = None,
                               width: int = 512, height: int = 512) -> dict[str, Any]:
        endpoint = self.endpoint()
        if not endpoint:
            raise IntegrationError("Configure and start ComfyUI before generating images.", "comfyui_missing")
        endpoint = endpoint_url(endpoint)
        if not prompt.strip() or len(prompt) > 10_000 or not 256 <= width <= 2048 or not 256 <= height <= 2048 or width % 8 or height % 8:
            raise IntegrationError("Use a prompt under 10,000 characters and dimensions from 256–2048 in multiples of 8.", "invalid_generation", 422)
        workflow_id = workflow_id or self.default_workflow()
        self._workflow(workflow_id)
        seed = secrets.randbelow(2**53) if seed is None else seed
        if not 0 <= seed < 2**53:
            raise IntegrationError("Seed must be from 0 to 2^53-1.", "invalid_seed", 422)
        id_ = str(uuid.uuid4())
        payload = {"prompt": prompt, "workflow_id": workflow_id, "seed": seed, "width": width, "height": height,
                   "endpoint": endpoint}
        with self.db() as db:
            db.execute("INSERT INTO jobs(id,payload,status,created_at) VALUES(?,?,?,?)", (id_, json.dumps(payload), "queued", now()))
        self.tasks[id_] = asyncio.create_task(self._run(id_, payload))
        return self.job(id_)

    async def generate(self, prompt: str, **kwargs: Any) -> dict[str, Any]:
        job = await self.start_generation(prompt, **kwargs)
        try:
            # Keep the job alive until cancel() captures its running prompt ID.
            # Direct propagation would mark it cancelled before remote cleanup.
            await asyncio.shield(self.tasks[job["id"]])
        except asyncio.CancelledError:
            await self.cancel(job["id"])
            raise
        result = self.job(job["id"])
        if result["status"] != "complete":
            raise IntegrationError(result.get("error") or "Image generation was cancelled.", "generation_failed")
        return result

    def _update(self, id_: str, **kwargs: Any) -> None:
        allowed = {"status", "progress", "prompt_id", "error"}
        if not kwargs or not set(kwargs) <= allowed:
            return
        with self.db() as db:
            db.execute(f"UPDATE jobs SET {','.join(k+'=?' for k in kwargs)} WHERE id=?", (*kwargs.values(), id_))

    async def _progress(self, id_: str, client_id: str, endpoint: str) -> None:
        import websockets
        parsed = urlsplit(endpoint_url(endpoint))
        ws_url = urlunsplit(("wss" if parsed.scheme == "https" else "ws", parsed.netloc, parsed.path + "/ws", urlencode({"clientId": client_id}), ""))
        try:
            async with websockets.connect(ws_url, open_timeout=5, max_size=2_000_000) as ws:
                async for message in ws:
                    if not isinstance(message, str):
                        continue
                    event = json.loads(message)
                    data = event.get("data", {})
                    prompt_id = self.job(id_).get("prompt_id")
                    if data.get("prompt_id") and data["prompt_id"] != prompt_id:
                        continue
                    if event.get("type") == "progress" and data.get("max"):
                        self._update(id_, progress=min(float(data.get("value", 0)) / float(data["max"]), 0.99))
                    elif event.get("type") == "execution_start":
                        self._update(id_, status="running")
        except (OSError, ValueError, websockets.WebSocketException):
            # History polling independently confirms completion and errors if websocket support is absent.
            return

    async def _run(self, id_: str, payload: dict[str, Any]) -> None:
        progress_task = None
        try:
            async with self.lock:
                workflow, bindings = self._workflow(payload["workflow_id"])
                workflow = copy.deepcopy(workflow)
                for key, binding in bindings.items():
                    workflow[str(binding["node"])]["inputs"][binding["input"]] = payload[key]
                client_id = str(uuid.uuid4())
                base = endpoint_url(payload.get("endpoint") or self.endpoint())
                if self.transport is None:
                    progress_task = asyncio.create_task(self._progress(id_, client_id, base))
                async with httpx.AsyncClient(timeout=30, transport=self.transport, trust_env=False) as client:
                    response = await client.post(base + "/prompt", json={"prompt": workflow, "client_id": client_id})
                    response.raise_for_status()
                    submitted = response.json()
                    prompt_id = submitted.get("prompt_id")
                    if not prompt_id or submitted.get("node_errors"):
                        raise IntegrationError("ComfyUI rejected this workflow. Verify its nodes and installed models.", "workflow_rejected")
                    self._update(id_, status="running", prompt_id=prompt_id)
                    deadline = time.monotonic() + self.timeout
                    while time.monotonic() < deadline:
                        response = await client.get(base + "/history/" + prompt_id)
                        response.raise_for_status()
                        history = response.json().get(prompt_id)
                        if history:
                            status = history.get("status", {})
                            if status.get("status_str") == "error":
                                raise IntegrationError("ComfyUI execution failed. Check its console and workflow models.", "execution_failed")
                            if status.get("completed", False) or history.get("outputs"):
                                saved = 0
                                for output in history.get("outputs", {}).values():
                                    for image in output.get("images", [])[:8]:
                                        await asyncio.wait_for(self._download(client, base, id_, payload, image), timeout=60)
                                        saved += 1
                                        if saved >= 8:
                                            break
                                    if saved >= 8:
                                        break
                                if not saved:
                                    raise IntegrationError("This workflow produced no saved image. Add a SaveImage output node.", "no_images")
                                self._update(id_, status="complete", progress=1)
                                return
                        await asyncio.sleep(1)
                    await self._cancel_remote(client, base, prompt_id)
                    raise IntegrationError("Image generation exceeded 10 minutes and was cancelled.", "generation_timeout")
        except asyncio.CancelledError:
            self._update(id_, status="cancelled", error="Cancelled by user.")
            raise
        except (httpx.HTTPError, IntegrationError, ValueError, OSError, TimeoutError) as exc:
            self._update(id_, status="failed", error=str(exc) if isinstance(exc, IntegrationError) else "ComfyUI request failed. Check the local endpoint and console.")
        finally:
            if progress_task:
                progress_task.cancel()
                await asyncio.gather(progress_task, return_exceptions=True)
            self.tasks.pop(id_, None)

    async def _download(self, client: httpx.AsyncClient, base: str, job_id: str, payload: dict[str, Any], image: dict[str, Any]) -> None:
        filename, subfolder = str(image.get("filename", "")), str(image.get("subfolder", ""))
        if not filename or "/" in filename or "\\" in filename or filename in {".", ".."} or ".." in subfolder.replace("\\", "/").split("/") or subfolder.startswith(("/", "\\")) or ":" in subfolder:
            raise IntegrationError("ComfyUI returned an unsafe image filename.", "unsafe_image")
        if image.get("type", "output") not in {"output", "temp"}:
            raise IntegrationError("ComfyUI returned an unsupported image source.", "unsafe_image")
        async with client.stream("GET", base + "/view", params={"filename": filename, "subfolder": subfolder, "type": image.get("type", "output")}) as response:
            response.raise_for_status()
            raw = bytearray()
            async for chunk in response.aiter_bytes():
                raw.extend(chunk)
                if len(raw) > 40_000_000:
                    raise IntegrationError("Generated image exceeds the 40 MB limit.", "image_too_large")
        with Image.open(io.BytesIO(raw)) as parsed:
            width, height, format_ = parsed.width, parsed.height, parsed.format
            if format_ not in {"PNG", "JPEG", "WEBP"} or width * height > 32_000_000:
                raise IntegrationError("Unsupported or oversized generated image.", "invalid_image")
            parsed.verify()
        id_, extension = str(uuid.uuid4()), {"PNG": "png", "JPEG": "jpg", "WEBP": "webp"}[format_]
        path = self.root / f"{id_}.{extension}"
        path.write_bytes(raw)
        with self.db() as db:
            db.execute("INSERT INTO images VALUES(?,?,?,?,?,?,?,?,?)", (id_, job_id, path.name, payload["prompt"], payload["seed"], width, height, payload["workflow_id"], now()))

    def library(self, job_id: str | None = None) -> list[dict[str, Any]]:
        with self.db() as db:
            if job_id:
                rows = db.execute("SELECT * FROM images WHERE job_id=? ORDER BY created_at DESC", (job_id,)).fetchall()
            else:
                rows = db.execute("SELECT * FROM images ORDER BY created_at DESC LIMIT 500").fetchall()
        return [{k: v for k, v in dict(row).items() if k != "path"} | {"content_url": f"/api/images/{row['id']}/content"} for row in rows]

    def job(self, id_: str) -> dict[str, Any]:
        with self.db() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (id_,)).fetchone()
        if not row:
            raise IntegrationError("Generation not found.", "not_found", 404)
        return {**{k: v for k, v in dict(row).items() if k != "payload"}, **json.loads(row["payload"]), "images": self.library(id_)}

    async def _cancel_remote(self, client: httpx.AsyncClient, base: str, prompt_id: str) -> None:
        response = await client.post(base + "/queue", json={"delete": [prompt_id]})
        response.raise_for_status()
        response = await client.get(base + "/queue")
        response.raise_for_status()
        if any(item[1] == prompt_id for item in response.json().get("queue_running", []) if len(item) > 1):
            response = await client.post(base + "/interrupt")
            response.raise_for_status()

    async def cancel(self, id_: str) -> dict[str, Any]:
        job = self.job(id_)
        task = self.tasks.get(id_)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if job.get("prompt_id") and job["status"] in {"queued", "running"}:
            try:
                async with httpx.AsyncClient(timeout=10, transport=self.transport, trust_env=False) as client:
                    await self._cancel_remote(client, endpoint_url(job.get("endpoint") or self.endpoint()), job["prompt_id"])
            except httpx.HTTPError as exc:
                self._update(id_, status="cancelled", error="Cancelled locally; ComfyUI was unreachable. Check its queue.")
                raise IntegrationError("Cancelled locally; ComfyUI was unreachable. Check its queue.", "cancel_failed") from exc
        return self.job(id_)

    def image_path(self, id_: str) -> Path:
        with self.db() as db:
            row = db.execute("SELECT path FROM images WHERE id=?", (id_,)).fetchone()
        if not row:
            raise IntegrationError("Image not found.", "not_found", 404)
        path = (self.root / row[0]).resolve()
        if path.parent != self.root or not path.is_file():
            raise IntegrationError("Image file is unavailable.", "not_found", 404)
        return path

    def delete_image(self, id_: str) -> None:
        path = self.image_path(id_)
        path.unlink()
        with self.db() as db:
            db.execute("DELETE FROM images WHERE id=?", (id_,))

    async def close(self) -> None:
        for id_ in list(self.tasks):
            try:
                await self.cancel(id_)
            except IntegrationError:
                pass
