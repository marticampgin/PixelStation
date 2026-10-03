import asyncio
import json
import time

import httpx
import pytest
from pydantic import BaseModel

from pixel_station.config import AppSettings
from pixel_station.observability import provider_observations
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
            return httpx.Response(
                200,
                json={
                    "models": [
                        {"name": "local:latest", "size": 100, "digest": "1" * 64},
                        {"name": "remote:latest"},
                        {"name": "hosted-cloud:latest"},
                    ]
                },
            )
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.test"})
        if request.url.path == "/api/show":
            return httpx.Response(
                200,
                json={
                    "capabilities": ["completion", "thinking", "embedding"],
                    **(
                        {"remote_host": "cloud.example"}
                        if payload["model"] == "remote:latest"
                        else {}
                    ),
                },
            )
        if request.url.path == "/api/chat" and payload["stream"]:
            return httpx.Response(
                200,
                content=b'{"message":{"thinking":"hidden reasoning","content":"Hello "}}\n{"message":{"content":"world"},"done":true}\n',
            )
        if request.url.path == "/api/chat":
            output_attempts += 1
            value = '{"value":"invalid"}' if output_attempts == 1 else '{"value":3}'
            return httpx.Response(
                200, json={"message": {"content": value, "thinking": "discard this"}}
            )
        if request.url.path == "/api/embed":
            return httpx.Response(200, json={"embeddings": [[1.0, 0.0]]})
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
    assert models["models"][0]["digest"] == "1" * 64
    response = "".join(
        [
            token
            async for token in provider.stream(
                "local:latest", [{"role": "user", "content": "Hello"}]
            )
        ]
    )
    assert response == "Hello world"
    payload = next(payload for path, payload in requests if path == "/api/chat")
    assert payload["think"] is False
    assert payload["options"] == {"num_ctx": 4096, "num_predict": 512}


async def test_structured_schema_retry_is_bounded(ollama):
    provider, requests = ollama
    result = await provider.structured(
        "local:latest", [{"role": "user", "content": "Get value"}], Output
    )
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
    assert await provider.embed("local:latest", ["private text"]) == [[1.0, 0.0]]
    assert requests[-1] == (
        "/api/embed",
        {"model": "local:latest", "input": ["private text"], "keep_alive": "0"},
    )


async def test_native_probe_records_fresh_model_digest_and_runtime_from_local_api(ollama, tmp_path):
    from pixel_station.app import create_app
    from pixel_station.evaluations import known_native_identity, native_cases

    provider, requests = ollama
    app = create_app(tmp_path, llm=provider, discover=False)
    settings = provider.get_settings()
    cases = await native_cases(app, settings)
    assert len(cases) == 2
    for case in cases:
        assert case["runtime"]["digest"] == "1" * 64
        assert case["runtime"]["ollama"] == "0.test"
        assert known_native_identity(case["runtime"])
    assert any(path == "/api/version" for path, _ in requests)
    assert any(path == "/api/tags" for path, _ in requests)


@pytest.fixture
def content_ollama(monkeypatch):
    original_client = httpx.AsyncClient

    def create(*, chunks=None, structured_content="", wire_stream=None, terminal=None):
        requests = []

        def handle(request):
            payload = json.loads(request.content)
            requests.append(payload)
            if payload["stream"]:
                if wire_stream:
                    return httpx.Response(200, stream=wire_stream)
                return httpx.Response(
                    200,
                    content="\n".join(
                        json.dumps(
                            {
                                "message": {
                                    "content": content,
                                    "thinking": "private separate reasoning",
                                }
                            }
                        )
                        for content in chunks
                    )
                    + "\n"
                    + (json.dumps({"done": True, **terminal}) + "\n" if terminal else ""),
                )
            return httpx.Response(
                200,
                json={
                    "message": {
                        "content": structured_content,
                        "thinking": "private separate reasoning",
                    }
                },
            )

        transport = httpx.MockTransport(handle)
        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            lambda **kwargs: original_client(transport=transport, timeout=kwargs.get("timeout")),
        )
        settings = AppSettings()
        provider = OllamaProvider(lambda: settings)
        provider._cache[settings.ollama_url] = (
            time.monotonic(),
            {
                "available": True,
                "models": [{"name": "local:latest", "capabilities": ["completion", "thinking"]}],
            },
        )
        return provider, requests

    return create


async def test_provider_records_actual_terminal_metadata_without_content(content_ollama):
    provider, _ = content_ollama(
        chunks=["<think>private thought</think>Public"],
        terminal={
            "prompt_eval_count": 20,
            "eval_count": 12,
            "load_duration": 10_000_000,
            "total_duration": 610_000_000,
            "eval_duration": 600_000_000,
            "done_reason": "stop",
        },
    )
    observations = []
    scope = provider_observations.set(observations)
    try:
        answer = "".join([part async for part in provider.stream("local:latest", [])])
    finally:
        provider_observations.reset(scope)
    assert answer == "Public"
    assert len(observations) == 1
    assert observations[0]["prompt_eval_count"] == 20
    assert observations[0]["eval_count"] == 12
    assert observations[0]["tokens_per_second"] == 20
    assert observations[0]["result_status"] == "complete"
    assert observations[0]["done_reason"] == "stop"
    assert "private" not in json.dumps(observations)


@pytest.mark.parametrize("tag", ["think", "thinking", "reasoning", "analysis"])
async def test_inline_reasoning_tags_split_across_chunks_never_reach_output(content_ollama, tag):
    text = f"<{tag}>private chain of thought</{tag}>Public answer."
    provider, _ = content_ollama(chunks=list(text))
    result = "".join([part async for part in provider.stream("local:latest", [])])
    assert result == "Public answer."


@pytest.mark.parametrize(
    "content,expected",
    [
        ("Visible before <think>private unfinished reasoning", "Visible before "),
        ("<reasoning>private unfinished reasoning", ""),
        ("<thi", ""),
        ("<think>outer <analysis>inner</analysis> still private</think>Public", "Public"),
        ("<THINK>private</THINK>Public", "Public"),
        (
            "2 < 3; <em>ordinary HTML</em> is preserved.",
            "2 < 3; <em>ordinary HTML</em> is preserved.",
        ),
    ],
)
async def test_unterminated_channels_drop_and_ordinary_text_survives(
    content_ollama, content, expected
):
    provider, _ = content_ollama(chunks=list(content))
    assert "".join([part async for part in provider.stream("local:latest", [])]) == expected


@pytest.mark.parametrize(
    "example",
    [
        "The literal tag is `<think>example</think>`.",
        "Example:\n```xml\n<think>example</think>\n```\nDescription.",
        "~~~xml\n<reasoning>example</reasoning>\n~~~\nDescription.",
        "Use ``<analysis>example</analysis>`` in sample markup.",
    ],
)
async def test_reasoning_filter_preserves_markdown_code_examples(content_ollama, example):
    provider, _ = content_ollama(chunks=list(example))
    assert "".join([part async for part in provider.stream("local:latest", [])]) == example


async def test_provider_still_streams_public_text_before_response_completes(content_ollama):
    release = asyncio.Event()

    class LiveStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"message":{"content":"Public first sentence. "}}\n'
            await release.wait()
            yield b'{"message":{"content":"<thi"}}\n'
            yield b'{"message":{"content":"nk>private</think>Public second sentence."}}\n'

    provider, _ = content_ollama(wire_stream=LiveStream())
    stream = provider.stream("local:latest", [])
    assert await asyncio.wait_for(anext(stream), timeout=1) == "Public first sentence. "
    release.set()
    assert "".join([part async for part in stream]) == "Public second sentence."


async def test_structured_json_filters_external_reasoning_before_schema_validation(content_ollama):
    provider, requests = content_ollama(
        structured_content='<think>private reasoning</think>{"value":3}<reasoning>private suffix</reasoning>'
    )
    assert (await provider.structured("local:latest", [], Output)).value == 3
    assert len(requests) == 1


async def test_structured_literal_json_strings_are_not_reasoning_channels(content_ollama):
    class LiteralOutput(BaseModel):
        content: str

    example = 'Use <think>example</think> as literal markup, including "quotes".'
    provider, _ = content_ollama(
        structured_content="<reasoning>private</reasoning>" + json.dumps({"content": example})
    )
    assert (await provider.structured("local:latest", [], LiteralOutput)).content == example


async def test_unterminated_structured_reasoning_cannot_appear_in_validation_error(content_ollama):
    provider, requests = content_ollama(structured_content="<think>private chain of thought")
    with pytest.raises(OllamaError) as error:
        await provider.structured("local:latest", [], Output)
    assert "private chain of thought" not in str(error.value)
    assert len(requests) == 2
