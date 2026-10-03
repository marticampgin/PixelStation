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
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx
from PIL import Image

from .web import IntegrationError, endpoint_url

REMOTE_CLEANUP_TIMEOUT = 15
MEMORY_RELEASE_TIMEOUT = 15


def now() -> str:
    return datetime.now(UTC).isoformat()


def _valid_dimension(value: Any) -> bool:
    return type(value) is int and 256 <= value <= 2048 and value % 8 == 0


def _workflow_dimensions(workflow: dict[str, Any], bindings: dict[str, Any]) -> dict[str, int]:
    defaults = {}
    for key in ("width", "height"):
        binding = bindings.get(key, {})
        inputs = workflow.get(str(binding.get("node", "")), {}).get("inputs", {})
        value = inputs.get(binding.get("input"))
        if _valid_dimension(value):
            defaults[key] = value
    return defaults


class ComfyImageProvider:
    def __init__(self, data_dir: Path, endpoint: Callable[[], str], default_workflow: Callable[[], str],
                 *, transport: httpx.AsyncBaseTransport | None = None, timeout: float = 600,
                 inference_lock: asyncio.Lock | None = None,
                 before_generation: Callable[[], Awaitable[dict]] | None = None) -> None:
        self.root = (data_dir / "images").resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "library.sqlite3"
        self.endpoint, self.default_workflow = endpoint, default_workflow
        self.transport, self.timeout = transport, timeout
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.lock = asyncio.Lock()
        self.inference_lock, self.before_generation = inference_lock, before_generation
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS workflows(id TEXT PRIMARY KEY,name TEXT NOT NULL,workflow TEXT NOT NULL,bindings TEXT NOT NULL,created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,payload TEXT NOT NULL,status TEXT NOT NULL,progress REAL NOT NULL DEFAULT 0,prompt_id TEXT,error TEXT,created_at TEXT NOT NULL,remote_cleanup_required INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS images(id TEXT PRIMARY KEY,job_id TEXT NOT NULL,path TEXT NOT NULL,prompt TEXT NOT NULL,seed INTEGER NOT NULL,width INTEGER NOT NULL,height INTEGER NOT NULL,workflow_id TEXT NOT NULL,created_at TEXT NOT NULL);
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
            if "remote_cleanup_required" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN remote_cleanup_required INTEGER NOT NULL DEFAULT 0")
                db.execute("UPDATE jobs SET remote_cleanup_required=1 WHERE prompt_id IS NOT NULL AND status IN ('queued','running','failed','interrupted','cancelled')")
            db.execute("UPDATE jobs SET remote_cleanup_required=1 WHERE prompt_id IS NOT NULL AND status IN ('queued','running')")
            db.execute("UPDATE jobs SET status='interrupted',error='Generation interrupted by application restart. Regenerate to try again.' WHERE status IN ('queued','running')")

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
            rows = db.execute("SELECT id,name,workflow,bindings,created_at FROM workflows ORDER BY created_at DESC").fetchall()
        result = []
        for row in rows:
            workflow, bindings = json.loads(row["workflow"]), json.loads(row["bindings"])
            defaults = _workflow_dimensions(workflow, bindings)
            record = {key: value for key, value in dict(row).items() if key != "workflow"}
            record["bindings"] = bindings
            if defaults:
                record["defaults"] = defaults
            result.append(record)
        return result

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
                               width: int | None = None, height: int | None = None) -> dict[str, Any]:
        endpoint = self.endpoint()
        if not endpoint:
            raise IntegrationError("Configure and start ComfyUI before generating images.", "comfyui_missing")
        endpoint = endpoint_url(endpoint)
        if not prompt.strip() or len(prompt) > 10_000 or any(
            value is not None and not _valid_dimension(value) for value in (width, height)
        ):
            raise IntegrationError("Use a prompt under 10,000 characters and dimensions from 256–2048 in multiples of 8.", "invalid_generation", 422)
        workflow_id = workflow_id or self.default_workflow()
        workflow, bindings = self._workflow(workflow_id)
        defaults = _workflow_dimensions(workflow, bindings)
        width = defaults.get("width", 512) if width is None else width
        height = defaults.get("height", 512) if height is None else height
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
        allowed = {"status", "progress", "prompt_id", "error", "remote_cleanup_required"}
        if not kwargs or not set(kwargs) <= allowed:
            return
        with self.db() as db:
            db.execute(f"UPDATE jobs SET {','.join(k+'=?' for k in kwargs)} WHERE id=?", (*kwargs.values(), id_))

    def _handoff(self, id_: str, stage: str, facts: dict[str, Any]) -> None:
        with self.db() as db:
            row = db.execute("SELECT payload FROM jobs WHERE id=?", (id_,)).fetchone()
            payload = json.loads(row[0])
            payload.setdefault("residency_handoff", {})[stage] = facts
            db.execute("UPDATE jobs SET payload=? WHERE id=?", (json.dumps(payload), id_))

    def _warning(self, id_: str, message: str) -> None:
        with self.db() as db:
            row = db.execute("SELECT payload FROM jobs WHERE id=?", (id_,)).fetchone()
            payload = json.loads(row[0])
            payload["handoff_warning"] = message
            db.execute("UPDATE jobs SET payload=? WHERE id=?", (json.dumps(payload), id_))

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
        prompt_id = None
        own_acquired = shared_acquired = False
        submitted = submission_uncertain = confirmed_inactive = False
        terminal_status, terminal_error = "failed", None
        base = endpoint_url(payload.get("endpoint") or self.endpoint())
        try:
            # The budget includes both queue waits, residency handoff and execution.
            async with asyncio.timeout(self.timeout):
                await self.lock.acquire()
                own_acquired = True
                if self.inference_lock is not None:
                    await self.inference_lock.acquire()
                    shared_acquired = True
                if self.before_generation:
                    try:
                        released = await self.before_generation()
                    except (httpx.HTTPError, ValueError, OSError, RuntimeError, TimeoutError) as exc:
                        self._handoff(id_, "ollama", {"confirmed_absent": False})
                        raise IntegrationError(
                            f"Ollama residency release could not be confirmed: {str(exc)[:400]}. Close other inference clients or check Ollama, then retry.",
                            "residency_release_failed",
                        ) from exc
                    if not isinstance(released, dict) or released.get("confirmed_absent") is not True:
                        self._handoff(id_, "ollama", {"confirmed_absent": False})
                        raise IntegrationError("Ollama model absence was not confirmed. Check resident models before retrying Image Studio.", "residency_release_failed")
                    self._handoff(id_, "ollama", released)
                elif shared_acquired:
                    self._handoff(id_, "ollama", {"status": "release_callback_unavailable"})
                workflow, bindings = self._workflow(payload["workflow_id"])
                workflow = copy.deepcopy(workflow)
                for key, binding in bindings.items():
                    workflow[str(binding["node"])]["inputs"][binding["input"]] = payload[key]
                client_id = str(uuid.uuid4())
                if self.transport is None:
                    progress_task = asyncio.create_task(self._progress(id_, client_id, base))
                async with httpx.AsyncClient(timeout=30, transport=self.transport, trust_env=False) as client:
                    submitted = submission_uncertain = True
                    response = await client.post(base + "/prompt", json={"prompt": workflow, "client_id": client_id})
                    response.raise_for_status()
                    receipt = response.json()
                    prompt_id = receipt.get("prompt_id") if isinstance(receipt, dict) else None
                    if not isinstance(prompt_id, str) or not prompt_id or receipt.get("node_errors"):
                        submission_uncertain = False
                        confirmed_inactive = True
                        prompt_id = None
                        raise IntegrationError("ComfyUI rejected this workflow. Verify its nodes and installed models.", "workflow_rejected")
                    submission_uncertain = False
                    self._update(id_, status="running", prompt_id=prompt_id, remote_cleanup_required=True)
                    while True:
                        response = await client.get(base + "/history/" + prompt_id)
                        response.raise_for_status()
                        body = response.json()
                        if not isinstance(body, dict):
                            raise IntegrationError("ComfyUI returned invalid history data.", "invalid_history")
                        history = body.get(prompt_id)
                        if history:
                            if not isinstance(history, dict) or not isinstance(history.get("status", {}), dict):
                                raise IntegrationError("ComfyUI returned invalid job state.", "invalid_history")
                            status = history.get("status", {})
                            if status.get("status_str") == "error":
                                raise IntegrationError("ComfyUI execution failed. Check its console and workflow models.", "execution_failed")
                            if status.get("completed", False):
                                confirmed_inactive = True
                                self._update(id_, remote_cleanup_required=False)
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
                                terminal_status = "complete"
                                self._update(id_, progress=1, remote_cleanup_required=False)
                                return
                        await asyncio.sleep(1)
        except asyncio.CancelledError:
            terminal_status, terminal_error = "cancelled", "Cancelled by user."
            raise
        except (httpx.HTTPError, IntegrationError, ValueError, OSError, RuntimeError, TimeoutError) as exc:
            message = (
                "Image generation exceeded its time budget, including inference queue waiting."
                if isinstance(exc, TimeoutError)
                else str(exc) if isinstance(exc, IntegrationError)
                else "ComfyUI request failed. Check the local endpoint and console."
            )
            terminal_error = message
        finally:
            try:
                if prompt_id and terminal_status != "complete":
                    confirmed_inactive = await self._cleanup(id_, base, prompt_id)
                elif submission_uncertain:
                    self._update(id_, remote_cleanup_required=True)
                    self._warning(id_, "ComfyUI submission outcome is unknown. Inspect its queue before resuming local inference; no global interrupt or memory release was attempted.")
                if shared_acquired and submitted and confirmed_inactive:
                    await self._free_models(id_, base)
            finally:
                if progress_task:
                    progress_task.cancel()
                try:
                    if progress_task:
                        await asyncio.gather(progress_task, return_exceptions=True)
                    error = self.job(id_).get("error") or terminal_error
                    self._update(id_, status=terminal_status, error=error)
                finally:
                    if shared_acquired and self.inference_lock is not None:
                        self.inference_lock.release()
                    if own_acquired:
                        self.lock.release()
                    self.tasks.pop(id_, None)

    async def _cleanup(self, id_: str, base: str, prompt_id: str) -> bool:
        try:
            async with asyncio.timeout(REMOTE_CLEANUP_TIMEOUT), httpx.AsyncClient(
                timeout=10, transport=self.transport, trust_env=False
            ) as client:
                await self._cancel_remote(client, base, prompt_id)
            self._update(id_, remote_cleanup_required=False)
            return True
        except (httpx.HTTPError, IntegrationError, ValueError, TimeoutError):
            message = "Remote cancellation could not be confirmed. ComfyUI may still be running this prompt; use Cancel again when its endpoint returns. Memory release was skipped."
            self._update(id_, remote_cleanup_required=True, error=message)
            self._warning(id_, message)
            return False

    async def _free_models(self, id_: str, base: str) -> None:
        """Request model release only after own prompt is known inactive.

        /free is an acknowledgement, not proof of a specific VRAM reduction.
        Device counters before/after are recorded as reported, never estimated.
        """
        facts: dict[str, Any] = {"release_requested": False}

        async def stats(client: httpx.AsyncClient) -> list[dict]:
            response = await client.get(base + "/system_stats")
            response.raise_for_status()
            body = response.json()
            devices = body.get("devices") if isinstance(body, dict) else None
            if not isinstance(devices, list):
                raise ValueError("ComfyUI memory counters unavailable")
            return [
                {key: value for key, value in item.items()
                 if key in {"name", "type", "index", "vram_total", "vram_free", "torch_vram_total", "torch_vram_free"}
                 and isinstance(value, (str, int, float)) and not isinstance(value, bool)}
                for item in devices if isinstance(item, dict)
            ]

        try:
            async with asyncio.timeout(MEMORY_RELEASE_TIMEOUT), httpx.AsyncClient(
                timeout=5, transport=self.transport, trust_env=False
            ) as client:
                response = await client.get(base + "/queue")
                response.raise_for_status()
                queue = response.json()
                if not isinstance(queue, dict) or any(
                    not isinstance(queue.get(key), list) for key in ("queue_running", "queue_pending")
                ):
                    raise IntegrationError("Cannot verify ComfyUI is idle before releasing its models.", "invalid_queue")
                if queue["queue_running"] or queue["queue_pending"]:
                    facts["status"] = "other_jobs_active"
                    self._warning(id_, "ComfyUI has other active or queued jobs. Memory release was skipped to preserve them; finish those jobs before loading chat models.")
                    return
                try:
                    facts["before"] = await stats(client)
                except (httpx.HTTPError, ValueError):
                    facts["before"] = None
                response = await client.post(base + "/free", json={"unload_models": True, "free_memory": True})
                response.raise_for_status()
                facts.update(release_requested=True, status="server_acknowledged")
                try:
                    facts["after"] = await stats(client)
                    before = {
                        row.get("index", offset): row.get("torch_vram_total")
                        for offset, row in enumerate(facts["before"] or [])
                        if isinstance(row.get("torch_vram_total"), (int, float))
                        and row["torch_vram_total"] > 0
                    }

                    def reduced():
                        return any(
                            isinstance(row.get("torch_vram_total"), (int, float))
                            and row["torch_vram_total"] < before.get(row.get("index", offset), 0)
                            for offset, row in enumerate(facts["after"])
                        )

                    deadline = time.monotonic() + 3
                    while before and not reduced() and time.monotonic() < deadline:
                        await asyncio.sleep(0.2)
                        facts["after"] = await stats(client)
                    facts["hardware_release_observed"] = reduced()
                    facts["observation_scope"] = "ComfyUI-reported torch residency reduction; no assertion that all GPU memory is free."
                except (httpx.HTTPError, ValueError):
                    facts["after"] = None
                    self._warning(id_, "ComfyUI acknowledged model release but fresh VRAM counters are unavailable. Check GPU memory before loading a large chat model.")
        except asyncio.CancelledError:
            facts["status"] = "release_interrupted"
            self._warning(id_, "ComfyUI memory-release observation was interrupted. The saved image is preserved; check GPU memory before loading a large chat model.")
            raise
        except (httpx.HTTPError, IntegrationError, ValueError, TimeoutError):
            facts["status"] = "observation_unconfirmed" if facts["release_requested"] else "release_unconfirmed"
            self._warning(id_, "ComfyUI memory release or its observation could not be confirmed. The image result is preserved; use ComfyUI's unload/free controls or stop it before loading large chat models.")
        finally:
            self._handoff(id_, "comfyui", facts)

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
        return {**{k: v for k, v in dict(row).items() if k != "payload"}, **json.loads(row["payload"]),
                "remote_cleanup_required": bool(row["remote_cleanup_required"]), "images": self.library(id_)}

    async def _cancel_remote(self, client: httpx.AsyncClient, base: str, prompt_id: str) -> None:
        response = await client.post(base + "/queue", json={"delete": [prompt_id]})
        response.raise_for_status()
        response = await client.get(base + "/queue")
        response.raise_for_status()
        queue = response.json()
        if not isinstance(queue, dict) or not isinstance(queue.get("queue_running"), list):
            raise IntegrationError("ComfyUI returned an invalid queue; remote cancellation could not be confirmed.", "invalid_queue")
        if any(item[1] == prompt_id for item in queue["queue_running"] if isinstance(item, list) and len(item) > 1):
            response = await client.post(base + "/interrupt", json={"prompt_id": prompt_id})
            response.raise_for_status()
        if self.inference_lock is not None:
            # An interrupt acknowledgement does not prove that execution stopped.
            # Verify only our submitted ID; never interrupt a different prompt.
            deadline = time.monotonic() + 10
            while True:
                response = await client.get(base + "/queue")
                response.raise_for_status()
                queue = response.json()
                if not isinstance(queue, dict) or any(
                    not isinstance(queue.get(key), list)
                    for key in ("queue_running", "queue_pending")
                ):
                    raise IntegrationError("Remote queue state cannot be confirmed.", "invalid_queue")
                if not any(
                    len(item) > 1 and item[1] == prompt_id
                    for key in ("queue_running", "queue_pending")
                    for item in queue[key] if isinstance(item, list)
                ):
                    return
                if time.monotonic() >= deadline:
                    raise IntegrationError("Submitted prompt still appears in ComfyUI's queue.", "cancel_unconfirmed")
                await asyncio.sleep(0.1)

    async def cancel(self, id_: str) -> dict[str, Any]:
        task = self.tasks.get(id_)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        job = self.job(id_)
        if job.get("prompt_id") and (job["status"] in {"queued", "running"} or job.get("remote_cleanup_required")):
            own_acquired = shared_acquired = False
            try:
                async with asyncio.timeout(30):
                    await self.lock.acquire()
                    own_acquired = True
                    if self.inference_lock is not None:
                        await self.inference_lock.acquire()
                        shared_acquired = True
                    base = endpoint_url(job.get("endpoint") or self.endpoint())
                    if not await self._cleanup(id_, base, job["prompt_id"]):
                        raise IntegrationError("Remote cleanup remains unconfirmed.", "cancel_failed")
                    self._update(id_, status="cancelled", remote_cleanup_required=False, error="Submitted prompt was removed or is no longer executing.")
                    if shared_acquired:
                        await self._free_models(id_, base)
            except (httpx.HTTPError, IntegrationError, ValueError, TimeoutError) as exc:
                self._update(id_, status="cancelled", remote_cleanup_required=True, error="Stopped local polling; remote cancellation could not be confirmed. ComfyUI may still be running this prompt. Use Cancel again when its endpoint returns.")
                raise IntegrationError("Stopped local polling; remote cancellation could not be confirmed. ComfyUI may still be running this prompt. Use Cancel again when its endpoint returns.", "cancel_failed") from exc
            finally:
                if shared_acquired and self.inference_lock is not None:
                    self.inference_lock.release()
                if own_acquired:
                    self.lock.release()
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
