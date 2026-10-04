import asyncio
import json

import pytest
from sqlalchemy import select

from pixel_station.app import create_app
from pixel_station.chat import MessageInput, generate_response
from pixel_station.database import AgentRun
from pixel_station.files import ingest


class ModelFixture:
    def __init__(self, actions):
        self.actions = actions

    async def structured(self, model, messages, schema, **kwargs):
        assert schema.__name__ == "AdaptiveAction"
        return schema.model_validate(self.actions.pop(0))

    async def stream(self, model, messages, **kwargs):
        yield "Observed [source](https://fixture.example/actual) and [unobserved](https://fixture.example/invented)."


@pytest.mark.parametrize("create_artifact", [False, True])
async def test_final_answer_retains_late_read_and_artifact_after_large_search(tmp_path, create_artifact):
    fact = "The observed fixture release is version 487."
    artifact_name = "observed-release-487.md"
    expected = artifact_name if create_artifact else fact
    actions = [
        {"tool": "web_search", "args": {"query": "fixture release"}},
        {"tool": "web_fetch", "args": {"url": "https://fixture.example/actual"}},
    ]
    if create_artifact:
        actions.append({
            "tool": "file_create",
            "args": {"filename": artifact_name, "format": "md", "content": fact},
        })
    actions.append({"tool": "finish", "args": {}})

    class AnswerFixture(ModelFixture):
        async def stream(self, model, messages, **kwargs):
            # Exercise the actual synthesis context, including build_context's
            # smaller evidence allocation, rather than only the tool result.
            assert expected in json.dumps(messages)
            yield expected

    app = create_app(tmp_path, llm=AnswerFixture(actions), discover=False)
    settings = app.state.settings()
    settings.roles["primary_chat"] = "fixture:model"
    app.state.set_settings(settings)

    async def search(query):
        return [
            {"url": "https://fixture.example/actual", "title": "Actual", "snippet": "S" * 3999},
            {"url": "https://fixture.example/other", "title": "Other", "snippet": "O" * 3999},
        ]

    async def fetch(url):
        return {"url": url, "title": "Actual fixture", "text": fact}

    app.state.tool_registry.tools["web_search"].execute = search
    app.state.tool_registry.tools["web_fetch"].execute = fetch
    with app.state.database.session() as session:
        from pixel_station.chat import ConversationCreate, new_conversation

        conversation = new_conversation(ConversationCreate(), session)
    request = "/task Search web and read a page"
    if create_artifact:
        request += ", then create a Markdown file with the observed release"
    events = [
        json.loads(value)
        async for value in generate_response(
            app, conversation["id"], MessageInput(content=request)
        )
    ]
    message = events[-1]["message"]
    assert message["status"] == "complete" and message["content"] == expected
    expected_tools = ["web_search", "web_fetch"] + (["file_create"] if create_artifact else [])
    assert [trace["tool"] for trace in message["traces"] if trace.get("adaptive")] == expected_tools
    if create_artifact:
        from pathlib import Path

        created = next(trace["file"] for trace in message["traces"] if "file" in trace)
        assert created["filename"] == artifact_name
        assert Path(created["path"]).read_text(encoding="utf-8") == fact
    if app.state.background_tasks:
        await asyncio.gather(*app.state.background_tasks)
    app.state.database.engine.dispose()


async def test_chat_adaptive_stream_persists_real_tool_progress_and_validated_sources(tmp_path):
    actions = [
        {"tool": "web_search", "args": {"query": "fixture"}},
        {"tool": "web_fetch", "args": {"url": "https://fixture.example/actual"}},
        {"tool": "finish", "args": {}},
    ]
    app = create_app(tmp_path, llm=ModelFixture(actions), discover=False)
    settings = app.state.settings()
    settings.roles["primary_chat"] = "fixture:model"
    app.state.set_settings(settings)
    calls = []

    async def search(query):
        calls.append("web_search")
        return [
            {
                "url": "https://fixture.example/actual",
                "title": "Actual fixture",
                "snippet": "Observed fixture fact",
            }
        ]

    async def fetch(url):
        calls.append("web_fetch")
        return {"url": url, "title": "Actual fixture", "text": "Fetched fixture fact"}

    app.state.tool_registry.tools["web_search"].execute = search
    app.state.tool_registry.tools["web_fetch"].execute = fetch
    with app.state.database.session() as session:
        from pixel_station.chat import ConversationCreate, new_conversation

        conversation = new_conversation(ConversationCreate(), session)
    events = [
        json.loads(value)
        async for value in generate_response(
            app,
            conversation["id"],
            MessageInput(content="/task Search web then read one result and explain it"),
        )
    ]
    assert calls == ["web_search", "web_fetch"]
    assert any(
        event.get("stage") == "web_search" and "running" in event.get("detail", "")
        for event in events
    )
    assert any(
        event.get("stage") == "web_fetch" and "complete" in event.get("detail", "")
        for event in events
    )
    message = events[-1]["message"]
    assert message["status"] == "complete"
    assert "https://fixture.example/actual" in message["content"]
    assert "https://fixture.example/invented" not in message["content"]
    assert (
        next(trace for trace in message["traces"] if trace.get("tool") == "adaptive_task")[
            "result"
        ]["tool_steps"]["total"]
        == 2
    )
    with app.state.database.session() as session:
        run = session.scalar(select(AgentRun))
        assert run.route == "adaptive_task" and run.status == "complete"
        assert run.evidence["metrics"]["tool_steps"] == 2
    if app.state.background_tasks:
        await asyncio.gather(*app.state.background_tasks)
    app.state.database.engine.dispose()


async def test_attachment_task_creates_a_real_artifact_after_reading_scoped_file(tmp_path):
    app = create_app(tmp_path, llm=ModelFixture([]), discover=False)
    settings = app.state.settings()
    settings.roles["primary_chat"] = "fixture:model"
    app.state.set_settings(settings)
    with app.state.database.session() as session:
        attachment = ingest(
            session, tmp_path, "fixture-contract.txt", b"Rental date: 2026-11-16. Member: Yes."
        )
        identity = attachment.id
    app.state.llm.actions = [
        {
            "tool": "file_retrieve",
            "args": {"attachment_ids": [identity], "query": "rental date member"},
        },
        {
            "tool": "file_create",
            "args": {
                "filename": "contract-summary.md",
                "format": "md",
                "content": "Rental date: 2026-11-16. Member: Yes.",
            },
        },
        {"tool": "finish", "args": {}},
    ]
    from pixel_station.adaptive import run_adaptive

    traces = []
    result = await run_adaptive(
        app,
        "Read the attached document and create a Markdown file with the rental date and membership",
        [identity],
        asyncio.Event(),
        traces=traces,
    )
    assert result["stop_reason"] == "model_finished"
    created = next(trace["file"] for trace in traces if "file" in trace)
    assert created["filename"] == "contract-summary.md" and created["source"] == "generated"
    assert created["parse_status"] == "ready"
    from pathlib import Path

    assert (
        Path(created["path"]).read_text(encoding="utf-8") == "Rental date: 2026-11-16. Member: Yes."
    )
    app.state.database.engine.dispose()


async def test_budget_exhaustion_keeps_incomplete_task_honest_without_model_completion_claim(
    tmp_path,
):
    app = create_app(
        tmp_path,
        llm=ModelFixture([{"tool": "web_search", "args": {"query": "fixture"}}]),
        discover=False,
    )
    settings = app.state.settings()
    settings.roles["primary_chat"] = "fixture:model"
    settings.max_steps = 1
    app.state.set_settings(settings)

    async def search(query):
        return [
            {
                "url": "https://fixture.example/actual",
                "title": "Actual fixture",
                "snippet": "A search snippet",
            }
        ]

    app.state.tool_registry.tools["web_search"].execute = search
    with app.state.database.session() as session:
        from pixel_station.chat import ConversationCreate, new_conversation

        conversation = new_conversation(ConversationCreate(), session)
    events = [
        json.loads(value)
        async for value in generate_response(
            app, conversation["id"], MessageInput(content="/task Search web then read one result")
        )
    ]
    message = events[-1]["message"]
    assert (
        "1-step tool limit" in message["content"]
        and "Still needed: read a web source" in message["content"]
    )
    assert (
        "Observed" not in message["content"]
    )  # The fixture synthesis must not run/claim a fetched fact.
    result = next(
        trace["result"] for trace in message["traces"] if trace.get("tool") == "adaptive_task"
    )
    assert result["stop_reason"] == "tool_budget" and result["incomplete_actions"] == ["web_fetch"]
    if app.state.background_tasks:
        await asyncio.gather(*app.state.background_tasks)
    app.state.database.engine.dispose()


async def test_image_answer_records_actual_vision_model_in_message_and_agent_run(tmp_path):
    import io

    from PIL import Image

    class VisionFixture(ModelFixture):
        async def models(self):
            return {
                "available": True,
                "models": [{"name": "fixture:vision", "capabilities": ["completion", "vision"]}],
            }

        async def stream(self, model, messages, **kwargs):
            assert model == "fixture:vision" and messages[-1]["images"]
            yield "The image contains a fixture."

    app = create_app(tmp_path, llm=VisionFixture([]), discover=False)
    settings = app.state.settings()
    settings.roles["primary_chat"] = "fixture:primary"
    settings.roles["vision"] = "fixture:vision"
    app.state.set_settings(settings)
    stream = io.BytesIO()
    Image.new("RGB", (120, 60), "white").save(stream, format="PNG")
    with app.state.database.session() as session:
        from pixel_station.chat import ConversationCreate, new_conversation

        image = ingest(session, tmp_path, "fixture-image.png", stream.getvalue())
        identity = image.id
        conversation = new_conversation(ConversationCreate(), session)
    events = [
        json.loads(value)
        async for value in generate_response(
            app,
            conversation["id"],
            MessageInput(content="What does this image show?", attachment_ids=[identity]),
        )
    ]
    message = events[-1]["message"]
    assert message["status"] == "complete" and message["model"] == "fixture:vision"
    with app.state.database.session() as session:
        run = session.scalar(select(AgentRun))
        assert (
            run.model == "fixture:vision"
            and run.evidence["metrics"]["configuration"]["roles"]["primary_chat"]
            == "fixture:primary"
        )
    if app.state.background_tasks:
        await asyncio.gather(*app.state.background_tasks)
    app.state.database.engine.dispose()
