import copy
import io
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image

from pixel_station.integrations import IntegrationServices, create_integrations_router
from pixel_station.providers.comfy import (
    MEMORY_RELEASE_TIMEOUT,
    REMOTE_CLEANUP_TIMEOUT,
    ComfyImageProvider,
)
from pixel_station.providers.web import IntegrationError
from pixel_station.toolset import build_tools


def dimension_workflow(width=512, height=512):
    return {
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}},
        "5": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
        },
    }, {
        "prompt": {"node": "6", "input": "text"},
        "width": {"node": "5", "input": "width"},
        "height": {"node": "5", "input": "height"},
    }


def test_workflow_dimensions_follow_each_bound_graph_without_changing_storage(tmp_path):
    provider = ComfyImageProvider(tmp_path, lambda: "", lambda: "")
    presets = [("Turbo", 512, 512), ("SDXL", 1024, 1024), ("Portrait", 768, 1152)]
    saved = {}
    for name, width, height in presets:
        graph, bindings = dimension_workflow(width, height)
        original = copy.deepcopy(graph)
        imported = provider.import_workflow(name, graph, bindings)
        assert imported["defaults"] == {"width": width, "height": height}
        assert graph == original
        saved[imported["id"]] = (original, bindings)

    reopened = ComfyImageProvider(tmp_path, lambda: "", lambda: "")
    listed = {row["name"]: row for row in reopened.workflows()}
    for name, width, height in presets:
        assert listed[name]["defaults"] == {"width": width, "height": height}
        assert "workflow" not in listed[name]
    for id_, (graph, bindings) in saved.items():
        assert reopened._workflow(id_) == (graph, bindings)


@pytest.mark.parametrize("invalid", [True, False, 512.0, "1024", 248, 2056, 513, ["5", 0]])
def test_invalid_dimension_defaults_are_ignored_independently(tmp_path, invalid):
    provider = ComfyImageProvider(tmp_path, lambda: "", lambda: "")
    graph, bindings = dimension_workflow(invalid, 1024)
    imported = provider.import_workflow("Invalid width", graph, bindings)
    assert imported["defaults"] == {"height": 1024}
    graph, bindings = dimension_workflow(512, invalid)
    imported = provider.import_workflow("Invalid height", graph, bindings)
    assert imported["defaults"] == {"width": 512}


def test_unbound_dimensions_are_not_inferred_and_import_validation_is_preserved(tmp_path):
    provider = ComfyImageProvider(tmp_path, lambda: "", lambda: "")
    graph, bindings = dimension_workflow(1024, 1024)
    prompt_only = {"prompt": bindings["prompt"]}
    imported = provider.import_workflow("Unbound dimensions", graph, prompt_only)
    assert "defaults" not in imported
    assert provider._workflow(imported["id"]) == (graph, prompt_only)

    with pytest.raises(IntegrationError, match="unknown node/input"):
        provider.import_workflow(
            "Missing input", graph, {**bindings, "width": {"node": "5", "input": "unknown"}}
        )
    with pytest.raises(IntegrationError, match="Bind the positive prompt"):
        provider.import_workflow("Missing prompt", graph, {})


def generation_provider(tmp_path, width=1024, height=1024, *, bind_dimensions=True):
    submitted = []
    output = io.BytesIO()
    Image.new("RGB", (16, 16)).save(output, "PNG")

    def serve(request):
        if request.url.path == "/prompt":
            submitted.append(json.loads(request.content)["prompt"])
            return httpx.Response(200, json={"prompt_id": "fixture"})
        if request.url.path == "/history/fixture":
            return httpx.Response(200, json={"fixture": {
                "status": {"completed": True},
                "outputs": {"out": {"images": [{"filename": "fixture.png"}]}},
            }})
        if request.url.path == "/view":
            return httpx.Response(200, content=output.getvalue())
        pytest.fail(f"Unexpected provider request: {request.url.path}")

    provider = ComfyImageProvider(
        tmp_path, lambda: "http://127.0.0.1:8188", lambda: "",
        transport=httpx.MockTransport(serve),
    )
    graph, bindings = dimension_workflow(width, height)
    if not bind_dimensions:
        bindings = {"prompt": bindings["prompt"]}
    imported = provider.import_workflow("Default workflow", graph, bindings)
    provider.default_workflow = lambda: imported["id"]
    return provider, submitted


@pytest.mark.parametrize("sizes,requested,expected", [
    ((1024, 1024), {}, (1024, 1024)),
    ((512, 512), {}, (512, 512)),
    ((768, 1152), {"width": 640}, (640, 1152)),
    ((768, 1152), {"height": 520}, (768, 520)),
    ((1024, 1024), {"width": 648, "height": 520}, (648, 520)),
    ((1024, 1024), {"width": None, "height": None}, (1024, 1024)),
])
async def test_generation_api_resolves_only_omitted_axes_from_selected_workflow(
    tmp_path, sizes, requested, expected
):
    provider, submitted = generation_provider(tmp_path, *sizes)
    app = FastAPI()
    app.include_router(create_integrations_router(SimpleNamespace(images=provider)))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/images/generate", json={"prompt": "Exact prompt", **requested})
    assert response.status_code == 200, response.text
    job = response.json()
    await provider.tasks[job["id"]]
    saved_job = provider.job(job["id"])
    assert saved_job["status"] == "complete"
    assert (saved_job["width"], saved_job["height"]) == expected
    assert (submitted[0]["5"]["inputs"]["width"], submitted[0]["5"]["inputs"]["height"]) == expected
    assert submitted[0]["6"]["inputs"]["text"] == "Exact prompt"
    assert provider._workflow(job["workflow_id"])[0]["5"]["inputs"] == {
        "width": sizes[0], "height": sizes[1], "batch_size": 1,
    }


async def test_explicit_workflow_uses_its_own_defaults_instead_of_global_default(tmp_path):
    provider, submitted = generation_provider(tmp_path)
    graph, bindings = dimension_workflow(512, 512)
    alternative = provider.import_workflow("DreamShaper", graph, bindings)
    result = await provider.generate("Prompt", workflow_id=alternative["id"])
    assert (result["width"], result["height"]) == (512, 512)
    assert submitted[0]["5"]["inputs"]["width"] == 512


@pytest.mark.parametrize("invalid", [True, 512.0, "1024", 248, 2056, 513, ["5", 0]])
async def test_generation_uses_fallback_only_for_invalid_bound_default(tmp_path, invalid):
    provider, submitted = generation_provider(tmp_path, invalid, 1024)
    result = await provider.generate("Prompt")
    assert (result["width"], result["height"]) == (512, 1024)
    assert submitted[0]["5"]["inputs"]["width"] == 512
    assert submitted[0]["5"]["inputs"]["height"] == 1024


async def test_generation_leaves_unbound_graph_dimensions_untouched(tmp_path):
    provider, submitted = generation_provider(tmp_path, bind_dimensions=False)
    result = await provider.generate("Prompt")
    assert (result["width"], result["height"]) == (512, 512)
    assert submitted[0]["5"]["inputs"] == {"width": 1024, "height": 1024, "batch_size": 1}
    assert (result["images"][0]["width"], result["images"][0]["height"]) == (16, 16)


@pytest.mark.parametrize("axis", ["width", "height"])
@pytest.mark.parametrize("invalid", [True, 512.0, "1024", 248, 2056, 513])
async def test_explicit_invalid_dimensions_are_rejected_before_submission(tmp_path, axis, invalid):
    provider, submitted = generation_provider(tmp_path)
    with pytest.raises(IntegrationError, match="dimensions"):
        await provider.start_generation("Prompt", **{axis: invalid})
    assert submitted == []
    assert provider.tasks == {}


@pytest.mark.parametrize("timeout", [600, 4.5])
def test_image_tool_budget_includes_provider_generation_and_bounded_cleanup(tmp_path, timeout):
    services = IntegrationServices(tmp_path, lambda key, default: default, lambda key, value: None)
    services.images.timeout = timeout
    registry = build_tools(SimpleNamespace(state=SimpleNamespace(integration_services=services)))
    assert registry.tools["image_generate"].timeout == timeout + REMOTE_CLEANUP_TIMEOUT + MEMORY_RELEASE_TIMEOUT
    assert registry.tools["web_search"].timeout == 45
