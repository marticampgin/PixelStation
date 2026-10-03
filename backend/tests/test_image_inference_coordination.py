import asyncio
import io
import json
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from pixel_station.app import create_app
from pixel_station.config import AppSettings
from pixel_station.indexing import index_pending
from pixel_station.memory import compact_due
from pixel_station.providers import OllamaError, OllamaProvider
from pixel_station.providers.comfy import ComfyImageProvider
from pixel_station.providers.web import IntegrationError


def graph():
    return {"1": {"class_type": "Test", "inputs": {"text": "old"}}}, {
        "prompt": {"node": "1", "input": "text"}
    }


def png():
    output = io.BytesIO()
    Image.new("RGB", (16, 16), "purple").save(output, "PNG")
    return output.getvalue()


def provider(tmp_path, serve, lock, callback=None, *, timeout=600):
    images = ComfyImageProvider(
        tmp_path,
        lambda: "http://127.0.0.1:8188",
        lambda: "",
        transport=httpx.MockTransport(serve),
        inference_lock=lock,
        before_generation=callback,
        timeout=timeout,
    )
    workflow, bindings = graph()
    imported = images.import_workflow("Coordination fixture", workflow, bindings)
    return images, imported["id"]


class ComfyServer:
    def __init__(self, lock):
        self.lock = lock
        self.calls = []
        self.history_started = asyncio.Event()
        self.allow_history = asyncio.Event()
        self.allow_history.set()
        self.complete = True
        self.other_job = False
        self.cleanup_unreachable = False
        self.free_unavailable = False
        self.after_free_unavailable = False
        self.free_started = asyncio.Event()
        self.allow_free = asyncio.Event()
        self.allow_free.set()
        self.own_running = True
        self.freed = False
        self.after_free_reads = 0
        self.release_after_reads = 1

    async def serve(self, request):
        assert self.lock.locked(), "Native service call escaped the shared lock"
        path = request.url.path
        self.calls.append((request.method, path))
        if path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "own-prompt"})
        if path == "/history/own-prompt":
            self.history_started.set()
            await self.allow_history.wait()
            if not self.complete:
                return httpx.Response(200, json={})
            self.own_running = False
            return httpx.Response(
                200,
                json={
                    "own-prompt": {
                        "status": {"completed": True},
                        "outputs": {"out": {"images": [{"filename": "fixture.png"}]}},
                    }
                },
            )
        if path == "/view":
            return httpx.Response(200, content=png())
        if path == "/queue":
            if self.cleanup_unreachable:
                return httpx.Response(503)
            if request.method == "POST":
                assert json.loads(request.content) == {"delete": ["own-prompt"]}
                return httpx.Response(200, json={})
            running = [[0, "own-prompt"]] if self.own_running else []
            if self.other_job:
                running = [[1, "someone-elses-prompt"]]
            return httpx.Response(200, json={"queue_running": running, "queue_pending": []})
        if path == "/interrupt":
            assert json.loads(request.content) == {"prompt_id": "own-prompt"}
            assert self.own_running and not self.other_job
            self.own_running = False
            return httpx.Response(200, json={})
        if path == "/system_stats":
            if self.freed and self.after_free_unavailable:
                return httpx.Response(503)
            if self.freed:
                self.after_free_reads += 1
            observed = self.freed and self.after_free_reads >= self.release_after_reads
            return httpx.Response(
                200,
                json={
                    "devices": [
                        {
                            "index": 0,
                            "name": "fixture GPU",
                            "vram_free": 4000 if self.freed else 1000,
                            "torch_vram_total": 0 if observed else 3000,
                        }
                    ]
                },
            )
        if path == "/free":
            self.free_started.set()
            await self.allow_free.wait()
            assert json.loads(request.content) == {"unload_models": True, "free_memory": True}
            if self.free_unavailable:
                return httpx.Response(503)
            self.freed = True
            return httpx.Response(200, json={})
        pytest.fail(f"Unexpected service request: {request.method} {path}")


@pytest.mark.asyncio
async def test_fresh_ollama_residency_unloads_only_observed_models_without_generating():
    settings = AppSettings()
    ollama = OllamaProvider(lambda: settings)
    loaded = [
        {"name": "actual-chat", "size_vram": 1234, "digest": "digest-a"},
        {"model": "actual-embed", "size_vram": 456},
    ]
    calls = []

    def serve(request):
        calls.append((request.method, request.url.path))
        if request.url.path == "/api/ps":
            return httpx.Response(200, json={"models": list(loaded)})
        assert request.url.path == "/api/generate"
        body = json.loads(request.content)
        assert set(body) == {"model", "stream", "keep_alive"}
        assert body["keep_alive"] == 0 and body["stream"] is False
        loaded[:] = [
            row for row in loaded if (row.get("name") or row.get("model")) != body["model"]
        ]
        return httpx.Response(200, json={"done": True})

    ollama._cache[settings.ollama_url] = (0, {"models": [{"name": "installed-but-not-resident"}]})
    result = await ollama.unload_loaded(transport=httpx.MockTransport(serve))
    assert result["confirmed_absent"] and result["after"] == []
    assert result["before"][0]["size_vram"] == 1234
    assert result["unloaded_models"] == ["actual-chat", "actual-embed"]
    assert calls == [
        ("GET", "/api/ps"),
        ("POST", "/api/generate"),
        ("POST", "/api/generate"),
        ("GET", "/api/ps"),
    ]


@pytest.mark.asyncio
async def test_unload_acknowledgement_without_absence_does_not_confirm_release():
    def serve(request):
        return httpx.Response(200, json={"models": [{"name": "still-resident"}]})

    with pytest.raises(OllamaError, match="still has resident"):
        await OllamaProvider(AppSettings).unload_loaded(transport=httpx.MockTransport(serve))


@pytest.mark.asyncio
async def test_app_injects_existing_lock_and_optional_fake_provider_callback(tmp_path):
    fake = SimpleNamespace()
    app = create_app(tmp_path, llm=fake, discover=False)
    try:
        assert app.state.integration_services.images.inference_lock is app.state.model_queue.lock
        assert app.state.integration_services.images.before_generation is None
    finally:
        await app.state.integration_services.close()
        app.state.database.engine.dispose()


@pytest.mark.asyncio
async def test_shared_lock_waits_for_ollama_and_blocks_maintenance_until_image_handoff(tmp_path):
    lock = asyncio.Lock()
    server = ComfyServer(lock)
    server.allow_history.clear()
    events = []

    async def unload():
        assert lock.locked()
        events.append("ollama-unloaded")
        return {"confirmed_absent": True, "before": [{"name": "actual-chat"}], "after": []}

    images, workflow_id = provider(tmp_path, server.serve, lock, unload)
    await lock.acquire()
    job = await images.start_generation("Fixture prompt", workflow_id=workflow_id)
    image_task = images.tasks[job["id"]]
    try:
        await asyncio.sleep(0)
        assert not server.calls and not events
        lock.release()
        await asyncio.wait_for(server.history_started.wait(), 1)
        settings = AppSettings(roles={"embedding": "configured-embed"})
        idle_app = SimpleNamespace(
            state=SimpleNamespace(
                settings=lambda: settings,
                active_generations={},
                model_queue=SimpleNamespace(lock=lock),
            )
        )
        await index_pending(idle_app)
        await compact_due(idle_app)  # Neither may access a DB or start a model while locked.

        async def foreground_inference():
            async with lock:
                assert server.freed
                events.append("foreground-inference")

        foreground = asyncio.create_task(foreground_inference())
        await asyncio.sleep(0)
        assert events == ["ollama-unloaded"]
        server.allow_history.set()
        await asyncio.wait_for(image_task, 1)
        await asyncio.wait_for(foreground, 1)
        result = images.job(job["id"])
        assert result["status"] == "complete" and result["images"]
        facts = result["residency_handoff"]["comfyui"]
        assert facts["status"] == "server_acknowledged"
        assert facts["hardware_release_observed"]
        assert facts["before"][0]["torch_vram_total"] == 3000
        assert facts["after"][0]["torch_vram_total"] == 0
        assert events == ["ollama-unloaded", "foreground-inference"]
    finally:
        server.allow_history.set()
        await images.close()
        if lock.locked():
            lock.release()


@pytest.mark.asyncio
@pytest.mark.parametrize("waiting_for", ["provider", "inference"])
async def test_image_time_budget_includes_both_lock_waits(tmp_path, waiting_for):
    lock = asyncio.Lock()
    server = ComfyServer(lock)
    callback_calls = []

    async def unload():
        callback_calls.append(True)
        return {"confirmed_absent": True}

    images, workflow_id = provider(tmp_path, server.serve, lock, unload, timeout=0.02)
    blocked = images.lock if waiting_for == "provider" else lock
    await blocked.acquire()
    try:
        job = await images.start_generation("Fixture", workflow_id=workflow_id)
        await asyncio.wait_for(images.tasks[job["id"]], 0.3)
        failed = images.job(job["id"])
        assert failed["status"] == "failed" and "time budget" in failed["error"]
        assert not callback_calls and not server.calls
        assert blocked.locked(), "A timed-out waiter released someone else's lock"
    finally:
        blocked.release()
        await images.close()


@pytest.mark.asyncio
async def test_unload_failure_is_persisted_before_any_comfy_submission(tmp_path):
    lock = asyncio.Lock()
    server = ComfyServer(lock)

    async def unload():
        raise OllamaError("Resident model still observed")

    images, workflow_id = provider(tmp_path, server.serve, lock, unload)
    job = await images.start_generation("Fixture", workflow_id=workflow_id)
    await asyncio.wait_for(images.tasks[job["id"]], 1)
    failed = images.job(job["id"])
    assert failed["status"] == "failed" and "Resident model" in failed["error"]
    assert not failed["residency_handoff"]["ollama"]["confirmed_absent"]
    assert not server.calls and not lock.locked() and not images.lock.locked()


@pytest.mark.asyncio
async def test_unconfirmed_callback_result_cannot_submit_an_image(tmp_path):
    lock = asyncio.Lock()
    server = ComfyServer(lock)

    async def unload():
        return {"confirmed_absent": False, "after": [{"name": "resident"}]}

    images, workflow_id = provider(tmp_path, server.serve, lock, unload)
    job = await images.start_generation("Fixture", workflow_id=workflow_id)
    await asyncio.wait_for(images.tasks[job["id"]], 1)
    assert images.job(job["id"])["status"] == "failed"
    assert not server.calls and not lock.locked()


@pytest.mark.asyncio
async def test_terminal_success_waits_for_handoff_warning_and_preserves_completed_image(tmp_path):
    lock = asyncio.Lock()
    server = ComfyServer(lock)
    server.free_unavailable = True
    server.allow_free.clear()
    images, workflow_id = provider(tmp_path, server.serve, lock)
    job = await images.start_generation("Fixture", workflow_id=workflow_id)
    task = images.tasks[job["id"]]
    try:
        await asyncio.wait_for(server.free_started.wait(), 1)
        pending = images.job(job["id"])
        assert pending["status"] == "running" and pending["images"]
        assert lock.locked()
        server.allow_free.set()
        await asyncio.wait_for(task, 1)
        complete = images.job(job["id"])
        assert complete["status"] == "complete" and complete["images"]
        assert "could not be confirmed" in complete["handoff_warning"]
        assert complete["residency_handoff"]["comfyui"]["status"] == "release_unconfirmed"
        assert not lock.locked()
    finally:
        server.allow_free.set()
        await images.close()


@pytest.mark.asyncio
async def test_running_cancel_confirms_own_prompt_absent_then_frees_under_shared_lock(tmp_path):
    lock = asyncio.Lock()
    server = ComfyServer(lock)
    server.complete = False
    images, workflow_id = provider(tmp_path, server.serve, lock)
    job = await images.start_generation("Fixture", workflow_id=workflow_id)
    await asyncio.wait_for(server.history_started.wait(), 1)
    result = await images.cancel(job["id"])
    assert result["status"] == "cancelled" and not result["remote_cleanup_required"]
    assert server.calls.index(("POST", "/interrupt")) < server.calls.index(("POST", "/free"))
    assert result["residency_handoff"]["comfyui"]["hardware_release_observed"]
    assert not lock.locked() and not images.lock.locked()


@pytest.mark.asyncio
async def test_cleanup_uncertainty_skips_free_and_retains_retryable_warning(tmp_path):
    lock = asyncio.Lock()
    server = ComfyServer(lock)
    server.complete = False
    server.cleanup_unreachable = True
    images, workflow_id = provider(tmp_path, server.serve, lock)
    job = await images.start_generation("Fixture", workflow_id=workflow_id)
    await asyncio.wait_for(server.history_started.wait(), 1)
    with pytest.raises(IntegrationError, match="could not be confirmed"):
        await images.cancel(job["id"])
    result = images.job(job["id"])
    assert result["status"] == "cancelled" and result["remote_cleanup_required"]
    assert "may still be running" in result["error"]
    assert ("POST", "/free") not in server.calls
    assert not lock.locked() and not images.lock.locked()


@pytest.mark.asyncio
async def test_other_external_prompt_is_never_interrupted_or_freed(tmp_path):
    lock = asyncio.Lock()
    server = ComfyServer(lock)
    server.complete = False
    server.other_job = True
    images, workflow_id = provider(tmp_path, server.serve, lock)
    job = await images.start_generation("Fixture", workflow_id=workflow_id)
    await asyncio.wait_for(server.history_started.wait(), 1)
    result = await images.cancel(job["id"])
    assert not result["remote_cleanup_required"]
    assert "other active" in result["handoff_warning"]
    assert ("POST", "/interrupt") not in server.calls and ("POST", "/free") not in server.calls
    assert not lock.locked()


@pytest.mark.asyncio
async def test_fresh_counter_failure_does_not_mask_completed_image(tmp_path):
    lock = asyncio.Lock()
    server = ComfyServer(lock)
    server.after_free_unavailable = True
    images, workflow_id = provider(tmp_path, server.serve, lock)
    result = await images.generate("Fixture", workflow_id=workflow_id)
    assert result["status"] == "complete" and result["images"]
    assert result["residency_handoff"]["comfyui"]["release_requested"]
    assert result["residency_handoff"]["comfyui"]["after"] is None
    assert "counters are unavailable" in result["handoff_warning"]


@pytest.mark.asyncio
async def test_delayed_free_acknowledgement_is_polled_until_torch_reduction_observed(tmp_path):
    lock = asyncio.Lock()
    server = ComfyServer(lock)
    server.release_after_reads = 3
    images, workflow_id = provider(tmp_path, server.serve, lock)
    result = await images.generate("Fixture", workflow_id=workflow_id)
    facts = result["residency_handoff"]["comfyui"]
    assert result["status"] == "complete" and facts["hardware_release_observed"]
    assert server.after_free_reads == 3
    assert facts["status"] == "server_acknowledged"


@pytest.mark.asyncio
async def test_unchanged_torch_pool_retains_measurements_without_warning(tmp_path):
    lock = asyncio.Lock()
    server = ComfyServer(lock)
    server.release_after_reads = 10**9
    images, workflow_id = provider(tmp_path, server.serve, lock)
    result = await images.generate("Fixture", workflow_id=workflow_id)
    facts = result["residency_handoff"]["comfyui"]
    assert result["status"] == "complete" and result["images"]
    assert facts["status"] == "server_acknowledged" and facts["release_requested"]
    assert not facts["hardware_release_observed"]
    assert facts["after"][0]["torch_vram_total"] == 3000
    assert not result.get("handoff_warning")
    assert not lock.locked()
