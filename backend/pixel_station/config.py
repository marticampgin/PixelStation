import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

ROLES = (
    "primary_chat",
    "planner",
    "router",
    "summarizer",
    "memory_extractor",
    "critic",
    "vision",
    "embedding",
)


class AppSettings(BaseModel):
    ollama_url: str = "http://127.0.0.1:11434"
    roles: dict[str, str] = Field(default_factory=lambda: dict.fromkeys(ROLES, ""))
    context_tokens: int = Field(default=8192, ge=2048, le=65536)
    bounded_response_tokens: int = Field(default=2048, ge=128, le=8192)
    thinking_enabled: bool = False
    auto_memory: bool = True
    retrieval_count: int = Field(default=4, ge=1, le=8)
    searxng_url: str = "http://127.0.0.1:8888"
    comfyui_url: str = "http://127.0.0.1:8188"
    critic_enabled: bool = False
    max_steps: int = Field(default=6, ge=1, le=12)
    keep_alive: str = "5m"
    summary_turns: int = Field(default=6, ge=2, le=30)
    profile: Literal["lite", "balanced", "strong"] = "lite"
    harness_enabled: bool = True
    harness_interval_hours: int = Field(default=24, ge=1, le=168)
    time_zone: str = "Europe/Riga"

    @field_validator("time_zone")
    @classmethod
    def valid_time_zone(cls, value: str) -> str:
        from zoneinfo import ZoneInfo

        ZoneInfo(value)
        return value

    @field_validator("ollama_url", "searxng_url", "comfyui_url")
    @classmethod
    def local_service_url(cls, value: str) -> str:
        from urllib.parse import urlsplit

        url = urlsplit(value)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
            raise ValueError("Use an HTTP(S) service URL without credentials")
        return value.rstrip("/")

    @field_validator("ollama_url")
    @classmethod
    def loopback_inference(cls, value: str) -> str:
        from urllib.parse import urlsplit

        if urlsplit(value).hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Inference must use a local loopback Ollama endpoint")
        return value

    @field_validator("roles")
    @classmethod
    def valid_roles(cls, value: dict[str, str]) -> dict[str, str]:
        if set(value) - set(ROLES):
            raise ValueError("Unknown model role")
        if any("cloud" in model.lower() for model in value.values()):
            raise ValueError("Cloud inference models are disabled")
        return {role: value.get(role, "") for role in ROLES}


def data_directory() -> Path:
    return Path(
        os.environ.get("PIXEL_STATION_DATA", Path(__file__).resolve().parents[2] / "data")
    ).resolve()
