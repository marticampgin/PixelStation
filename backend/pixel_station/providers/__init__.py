import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any, Protocol

import httpx
from pydantic import BaseModel


class LLMProvider(Protocol):
    async def models(self) -> dict: ...
    def stream(self, model: str, messages: list[dict], **kwargs) -> AsyncIterator[str]: ...
    async def structured(
        self, model: str, messages: list[dict], schema: type[BaseModel], **kwargs
    ) -> BaseModel: ...


class EmbeddingProvider(Protocol):
    async def embed(self, model: str, texts: list[str]) -> list[list[float]]: ...


class VisionProvider(Protocol):
    async def describe(self, model: str, prompt: str, images: list[str]) -> str: ...


class ImageGenerationProvider(Protocol):
    async def generate(self, prompt: str, **kwargs) -> dict: ...


class SearchProvider(Protocol):
    async def search(self, query: str) -> list[dict]: ...


class WebFetchProvider(Protocol):
    async def fetch(self, url: str) -> dict: ...


class MemoryStore(Protocol):
    def search(self, query: str, limit: int = 4) -> list[dict]: ...


class VectorStore(Protocol):
    def add(self, key: str, vector: list[float]) -> None: ...
    def delete(self, key: str) -> None: ...
    def search(self, vector: list[float], limit: int = 4) -> list[tuple[str, float]]: ...


class FileParser(Protocol):
    def parse(self, path, extension: str) -> list[dict]: ...


class FileWriter(Protocol):
    def write(self, path, content: str, format: str) -> None: ...


class EmailConnector(Protocol):
    async def search(self, query: str) -> list[dict]: ...


class CalendarConnector(Protocol):
    async def agenda(self, start: str, end: str) -> list[dict]: ...


class Game(Protocol):
    def legal_actions(self, state: dict) -> list[dict]: ...


class Scheduler(Protocol):
    async def run_due(self) -> None: ...


class OllamaError(RuntimeError):
    pass


class OllamaProvider:
    """Native local Ollama API. Reasoning fields are never retained."""

    def __init__(self, get_settings):
        self.get_settings = get_settings
        self._cache: dict[str, tuple[float, dict]] = {}

    def _validate_model(self, model: str) -> None:
        if not model or "cloud" in model.lower():
            raise OllamaError(
                "Select an installed local model in Settings. Cloud models are disabled."
            )

    async def models(self) -> dict:
        import time

        settings = self.get_settings()
        cached = self._cache.get(settings.ollama_url)
        if cached and time.monotonic() - cached[0] < 30:
            return cached[1]
        try:
            async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
                response = await client.get(f"{settings.ollama_url}/api/tags")
                response.raise_for_status()
                models = []
                for model in response.json().get("models", []):
                    name = model.get("name", "")
                    if "cloud" in name.lower():
                        continue
                    details = await client.post(
                        f"{settings.ollama_url}/api/show", json={"model": name}
                    )
                    details.raise_for_status()
                    info = details.json()
                    if info.get("remote_host") or "cloud" in info.get("capabilities", []):
                        continue
                    models.append(
                        {
                            "name": name,
                            "size": model.get("size", 0),
                            "capabilities": info.get("capabilities", ["completion"]),
                            "details": model.get("details", {}),
                        }
                    )
            result = {"available": True, "models": models}
            self._cache[settings.ollama_url] = (time.monotonic(), result)
            return result
        except (httpx.HTTPError, ValueError) as exc:
            return {
                "available": False,
                "models": [],
                "error": f"Ollama unavailable: {exc}. Start Ollama and run ollama pull hf.co/LiquidAI/LFM2.5-2.6B-GGUF:Q4_K_M",
            }

    async def _require_local_model(self, model: str) -> None:
        self._validate_model(model)
        discovery = await self.models()
        if model not in {item["name"] for item in discovery["models"]}:
            raise OllamaError(
                discovery.get("error") or f"Model is not installed locally. Run ollama pull {model}"
            )

    def _thinking_option(self, model: str) -> dict:
        settings = self.get_settings()
        cached = self._cache.get(settings.ollama_url)
        models = cached[1].get("models", []) if cached else []
        capability: dict = next((item for item in models if item["name"] == model), {})
        return (
            {"think": settings.thinking_enabled}
            if "thinking" in capability.get("capabilities", [])
            else {}
        )

    async def stream(self, model: str, messages: list[dict], **kwargs) -> AsyncIterator[str]:
        await self._require_local_model(model)
        settings = self.get_settings()
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
            "keep_alive": "0"
            if settings.profile == "lite" and model != settings.roles["primary_chat"]
            else settings.keep_alive,
            "options": {
                "num_ctx": settings.context_tokens,
                "num_predict": settings.bounded_response_tokens,
            },
            **self._thinking_option(model),
            **kwargs,
        }
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(180, connect=5), trust_env=False
        ) as client:
            async with client.stream(
                "POST", f"{settings.ollama_url}/api/chat", json=payload
            ) as response:
                if response.is_error:
                    body = (await response.aread()).decode(errors="replace")
                    raise OllamaError(
                        f"Ollama {response.status_code}: {body[:300]}. Check the installed model in Settings."
                    )
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    chunk = json.loads(line)
                    if chunk.get("error"):
                        raise OllamaError(chunk["error"])
                    if content := chunk.get("message", {}).get("content"):
                        yield content

    async def structured(
        self, model: str, messages: list[dict], schema: type[BaseModel], **kwargs
    ) -> Any:
        await self._require_local_model(model)
        settings = self.get_settings()
        validation_retries = max(0, min(int(kwargs.pop("validation_retries", 1)), 1))
        num_predict = max(
            128, min(int(kwargs.pop("num_predict", 1024)), settings.bounded_response_tokens)
        )
        async with httpx.AsyncClient(timeout=120, trust_env=False) as client:
            for attempt in range(validation_retries + 1):
                response = await client.post(
                    f"{settings.ollama_url}/api/chat",
                    json={
                        "model": model,
                        "messages": messages,
                        "stream": False,
                        "format": schema.model_json_schema(),
                        "keep_alive": "0"
                        if settings.profile == "lite" and model != settings.roles["primary_chat"]
                        else settings.keep_alive,
                        "options": {
                            "num_ctx": settings.context_tokens,
                            "temperature": 0,
                            "num_predict": num_predict,
                        },
                        **self._thinking_option(model),
                        **kwargs,
                    },
                )
                response.raise_for_status()
                try:
                    return schema.model_validate_json(response.json()["message"]["content"])
                except (ValueError, KeyError) as exc:
                    if attempt == validation_retries:
                        raise OllamaError(
                            f"Model did not follow structured output schema: {exc}"
                        ) from exc
                    messages = [
                        *messages,
                        {
                            "role": "user",
                            "content": "Return only a JSON object matching the supplied schema exactly.",
                        },
                    ]
        raise OllamaError("Structured output failed")

    async def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        await self._require_local_model(model)
        async with httpx.AsyncClient(timeout=60, trust_env=False) as client:
            response = await client.post(
                f"{self.get_settings().ollama_url}/api/embed",
                json={"model": model, "input": texts, "keep_alive": "0"},
            )
            response.raise_for_status()
            return response.json()["embeddings"]

    async def describe(self, model: str, prompt: str, images: list[str]) -> str:
        return "".join(
            [
                token
                async for token in self.stream(
                    model, [{"role": "user", "content": prompt, "images": images}]
                )
            ]
        )


class ModelQueue:
    """One inference at a time protects Lite machines from helper model contention."""

    def __init__(self):
        self.lock = asyncio.Lock()
