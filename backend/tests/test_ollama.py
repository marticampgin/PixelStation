import json

import httpx
import pytest
from pydantic import BaseModel

from pixel_station.config import AppSettings
from pixel_station.providers import OllamaError, OllamaProvider


class Output(BaseModel):
    value: int


@pytest.fixture
def ollama(monkeypatch):
    requests = []
    output_attempts = 0
    def handle(request):
        nonlocal output_attempts
        payload = json.loads(request.content) if request.content else {}
        requests.append((request.url.path, payload))
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "local:latest", "size": 100}, {"name": "remote:latest"}, {"name": "hosted-cloud:latest"}]})
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion", "thinking", "embedding"], **({"remote_host": "cloud.example"} if payload["model"] == "remote:latest" else {})})
        if request.url.path == "/api/chat" and payload["stream"]:
            return httpx.Response(200, content=b'{"message":{"thinking":"hidden reasoning","content":"Hello "}}\n{"message":{"content":"world"},"done":true}\n')
        if request.url.path == "/api/chat":
            output_attempts += 1
            value = '{"value":"invalid"}' if output_attempts == 1 else '{"value":3}'
            return httpx.Response(200, json={"message": {"content": value, "thinking": "discard this"}})
        if request.url.path == "/api/embed":
            return httpx.Response(200, json={"embeddings": [[1., 0.]]})
        raise AssertionError(request.url)
    transport = httpx.MockTransport(handle)
    original_client = httpx.AsyncClient
    def client(**kwargs):
        assert kwargs.get("trust_env") is False
        return original_client(transport=transport, timeout=kwargs.get("timeout"))
    monkeypatch.setattr(httpx, "AsyncClient", client)
    settings = AppSettings(context_tokens=4096, bounded_response_tokens=512)
    settings.roles["primary_chat"] = "local:latest"
    return OllamaProvider(lambda: settings), requests


async def test_local_discovery_and_stream_discard_reasoning(ollama):
    provider, requests = ollama
    models = await provider.models()
    assert [model["name"] for model in models["models"]] == ["local:latest"]
    response = "".join([token async for token in provider.stream("local:latest", [{"role": "user", "content": "Hello"}])])
    assert response == "Hello world"
    payload = next(payload for path, payload in requests if path == "/api/chat")
    assert payload["think"] is False
    assert payload["options"] == {"num_ctx": 4096, "num_predict": 512}


async def test_structured_schema_retry_is_bounded(ollama):
    provider, requests = ollama
    result = await provider.structured("local:latest", [{"role": "user", "content": "Get value"}], Output)
    assert result.value == 3
    calls = [payload for path, payload in requests if path == "/api/chat"]
    assert len(calls) == 2
    assert calls[0]["format"] == Output.model_json_schema()
    assert calls[0]["options"]["num_predict"] == 512


async def test_outer_workflow_can_own_schema_retry(ollama):
    provider, requests = ollama
    with pytest.raises(OllamaError):
        await provider.structured("local:latest", [], Output, validation_retries=0, num_predict=150)
    calls = [payload for path, payload in requests if path == "/api/chat"]
    assert len(calls) == 1
    assert calls[0]["options"]["num_predict"] == 150
    assert "validation_retries" not in calls[0]


@pytest.mark.parametrize("model", ["hosted-cloud:latest", "remote:latest", "missing:latest"])
async def test_cloud_remote_and_missing_models_never_receive_prompts(ollama, model):
    provider, requests = ollama
    with pytest.raises(OllamaError):
        await provider.structured(model, [{"role": "user", "content": "private prompt"}], Output)
    assert not any(path == "/api/chat" for path, _ in requests)


async def test_native_embedding_api(ollama):
    provider, requests = ollama
    assert await provider.embed("local:latest", ["private text"]) == [[1., 0.]]
    assert requests[-1] == ("/api/embed", {"model": "local:latest", "input": ["private text"], "keep_alive": "0"})
