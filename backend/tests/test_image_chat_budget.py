import asyncio
import json

import pytest

from pixel_station.app import create_app
from pixel_station.chat import MessageInput, generate_response
from pixel_station.config import AppSettings
from pixel_station.database import Conversation
from pixel_station.providers.comfy import MEMORY_RELEASE_TIMEOUT, REMOTE_CLEANUP_TIMEOUT
from pixel_station.toolset import build_tools


class FixtureModel:
    async def stream(self, model, messages, **kwargs):
        yield "Fixture answer."


@pytest.mark.parametrize("prompt,intent,provider_timeout,expected", [
    ("Generate an image of a forest", "image_generate", 600, 630),
    ("Generate an image of a forest", "image_generate", 4.5, 34.5),
    ("Search the web for a forest", "web_search", 4.5, 180),
])
async def test_direct_chat_integration_timeout_tracks_image_tool_only(
    tmp_path, monkeypatch, prompt, intent, provider_timeout, expected
):
    app = create_app(tmp_path, llm=FixtureModel(), discover=False)
    app.state.integration_services.images.timeout = provider_timeout
    app.state.tool_registry = build_tools(app)
    settings = AppSettings(auto_memory=False)
    settings.roles["primary_chat"] = "fixture"
    app.state.set_settings(settings)
    durations = []
    original_timeout = asyncio.timeout

    def observed_timeout(seconds):
        durations.append(seconds)
        return original_timeout(seconds)

    async def context(route, content):
        assert route == intent and content == prompt
        assert durations[-1] == expected
        return {"content": "Fixture evidence.", "sources": [], "images": []}

    monkeypatch.setattr(asyncio, "timeout", observed_timeout)
    monkeypatch.setattr(app.state.integration_services, "chat_context", context)
    with app.state.database.session() as session:
        conversation = Conversation()
        session.add(conversation)
        session.commit()
        conversation_id = conversation.id
    try:
        events = [json.loads(line) async for line in generate_response(
            app, conversation_id, MessageInput(content=prompt)
        )]
        assert not any(event["type"] == "error" for event in events)
        assert events[-1]["message"]["status"] == "complete"
        if intent == "image_generate":
            tool = app.state.tool_registry.tools["image_generate"]
            assert tool.timeout == app.state.integration_services.images.timeout + REMOTE_CLEANUP_TIMEOUT + MEMORY_RELEASE_TIMEOUT
    finally:
        await asyncio.gather(*app.state.background_tasks, return_exceptions=True)
        await app.state.integration_services.close()
        app.state.database.engine.dispose()
