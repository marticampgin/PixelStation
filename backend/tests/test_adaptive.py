import asyncio
import json
from types import SimpleNamespace

import pytest

from pixel_station import adaptive
from pixel_station.adaptive import AdaptiveError, run_adaptive
from pixel_station.config import AppSettings
from pixel_station.orchestration import Tool, ToolRegistry, route_prompt


def make_app(decisions, tools, *, steps=6):
    settings = AppSettings(max_steps=steps)
    settings.roles["primary_chat"] = "fixture:local"
    settings.roles["planner"] = "fixture:planner"
    requests = []
    executions = []
    registry = ToolRegistry()
    for identifier, function, properties, permission in tools:

        async def execute(operation=function, tool_id=identifier, **args):
            executions.append((tool_id, args))
            output = operation(**args)
            return await output if hasattr(output, "__await__") else output

        registry.register(
            Tool(
                identifier,
                identifier,
                "Fixture",
                "fixture",
                {
                    "type": "object",
                    "properties": properties,
                    "required": list(properties),
                    "additionalProperties": False,
                },
                execute,
                permission=permission,
            )
        )

    class Model:
        async def structured(self, model, messages, schema, **kwargs):
            requests.append({"model": model, "messages": messages, "kwargs": kwargs})
            output = decisions(messages) if callable(decisions) else decisions.pop(0)
            if hasattr(output, "__await__"):
                output = await output
            return schema.model_validate(output)

    app = SimpleNamespace(
        state=SimpleNamespace(
            settings=lambda: settings,
            llm=Model(),
            model_queue=SimpleNamespace(lock=asyncio.Lock()),
            tool_registry=registry,
        )
    )
    return app, requests, executions


def web_tools(url="https://fixture.example/observed"):
    return [
        (
            "web_search",
            lambda query: [
                {"url": url, "title": "Observed source", "snippet": "Actual fixture observation"}
            ],
            {"query": {"type": "string"}},
            "read_only",
        ),
        (
            "web_fetch",
            lambda url: {"url": url, "title": "Observed source", "text": "Fetched fixture page"},
            {"url": {"type": "string"}},
            "read_only",
        ),
    ]


@pytest.mark.parametrize(
    "observed_url", ["https://fixture.example/a", "https://fixture.example/b?version=2"]
)
async def test_next_choice_reads_actual_observation_not_a_prebuilt_plan(observed_url):
    def choose(messages):
        raw = messages[-1]["content"].split("Observed results:\n", 1)[1]
        observations = json.loads(raw)
        if not observations:
            return {"tool": "web_search", "args": {"query": "find official source"}}
        latest = observations[0]
        if latest["tool"] == "web_search":
            found = json.loads(latest["result"])[0]["url"]
            return {"tool": "web_fetch", "args": {"url": found}}
        return {"tool": "finish", "args": {}}

    app, requests, calls = make_app(choose, web_tools(observed_url))
    traces = []
    result = await run_adaptive(
        app,
        "/task Find official documentation, then read one page",
        [],
        asyncio.Event(),
        traces=traces,
    )
    assert calls == [
        ("web_search", {"query": "find official source"}),
        ("web_fetch", {"url": observed_url}),
    ]
    assert result["stop_reason"] == "model_finished" and result["tool_steps"]["total"] == 2
    assert result["sources"][0]["url"] == observed_url and result["sources"][0]["fetched"]
    assert all(request["kwargs"]["validation_retries"] == 0 for request in requests)
    assert all(request["kwargs"]["num_predict"] == 768 for request in requests)
    assert all("reasoning" not in trace for trace in traces)


async def test_task_budget_stops_without_requesting_more_decisions_or_claiming_finish():
    app, requests, calls = make_app(
        [{"tool": "web_search", "args": {"query": "source"}}], web_tools(), steps=1
    )
    result = await run_adaptive(app, "Search the web and read a source", [], asyncio.Event())
    assert len(calls) == 1 and len(requests) == 1
    assert result["stop_reason"] == "tool_budget" and result["incomplete_actions"] == ["web_fetch"]
    assert json.loads(result["content"])["stop_reason"] == "tool_budget"


async def test_identical_query_loop_is_rejected_after_one_execution():
    actions = [{"tool": "web_search", "args": {"query": query}} for query in [" source ", "SOURCE"]]
    app, requests, calls = make_app(actions, web_tools())
    traces = []
    with pytest.raises(AdaptiveError) as error:
        await run_adaptive(app, "Search the web", [], asyncio.Event(), traces=traces)
    assert error.value.code == "adaptive_repeated_action"
    assert len(calls) == 1 and len(requests) == 2
    assert traces[-1]["validation"] == "adaptive_action_error"
    assert traces[-1]["error_code"] == "adaptive_repeated_action"


@pytest.mark.parametrize(
    "action",
    [
        {"tool": "web_fetch", "args": {"url": "https://fixture.example/invented"}},
        {
            "tool": "file_create",
            "args": {"filename": "result.md", "content": "invented", "format": "md"},
        },
        {"tool": "gmail_read", "args": {"thread_id": "foreign-account-thread"}},
    ],
)
async def test_irrelevant_tools_and_invented_urls_receive_only_one_repair(action):
    app, requests, calls = make_app([action, action], web_tools())
    traces = []
    with pytest.raises(AdaptiveError):
        await run_adaptive(app, "Search the web", [], asyncio.Event(), traces=traces)
    assert not calls and len(requests) == 2
    assert [trace["retry"] for trace in traces] == [0, 1]


async def test_external_permission_is_checked_even_for_an_allowed_tool():
    tools = [
        (
            "web_search",
            lambda query: pytest.fail("Permission must reject execution"),
            {"query": {"type": "string"}},
            "external_or_destructive",
        )
    ]
    action = {"tool": "web_search", "args": {"query": "source"}}
    app, _, calls = make_app([action, action], tools)
    with pytest.raises(AdaptiveError, match="external or destructive"):
        await run_adaptive(app, "Search the web", [], asyncio.Event())
    assert not calls


async def test_early_finish_repairs_once_and_then_reads_requested_evidence():
    actions = [
        {"tool": "finish", "args": {}},
        {"tool": "web_search", "args": {"query": "source"}},
        {"tool": "finish", "args": {}},
    ]
    app, _, calls = make_app(actions, web_tools())
    result = await run_adaptive(app, "Search the web", [], asyncio.Event())
    assert len(calls) == 1 and result["repairs"] == 1 and result["stop_reason"] == "model_finished"


async def test_empty_search_never_authorizes_an_invented_fetch():
    tools = web_tools()
    tools[0] = ("web_search", lambda query: [], {"query": {"type": "string"}}, "read_only")
    foreign = {"tool": "web_fetch", "args": {"url": "https://fixture.example/guessed"}}
    app, _, calls = make_app(
        [{"tool": "web_search", "args": {"query": "source"}}, foreign, foreign], tools
    )
    with pytest.raises(AdaptiveError, match="earlier web_search"):
        await run_adaptive(app, "Search the web and read a page", [], asyncio.Event())
    assert [identifier for identifier, _ in calls] == ["web_search"]


async def test_gmail_read_accepts_only_observed_thread_ids():
    tools = [
        (
            "gmail_search",
            lambda query: {"threads": [{"id": "observed-thread", "subject": "Fixture"}]},
            {"query": {"type": "string"}},
            "read_only",
        ),
        (
            "gmail_read",
            lambda thread_id: {"id": thread_id, "messages": []},
            {"thread_id": {"type": "string"}},
            "read_only",
        ),
    ]
    actions = [
        {"tool": "gmail_search", "args": {"query": "subject:Fixture"}},
        {"tool": "gmail_read", "args": {"thread_id": "foreign"}},
        {"tool": "gmail_read", "args": {"thread_id": "observed-thread"}},
        {"tool": "finish", "args": {}},
    ]
    app, _, calls = make_app(actions, tools)
    result = await run_adaptive(app, "Find then read a Gmail email", [], asyncio.Event())
    assert calls[-1] == ("gmail_read", {"thread_id": "observed-thread"})
    assert len(calls) == 2 and result["repairs"] == 1


async def test_gmail_sequence_passes_same_account_binding_to_both_reads():
    tools = [
        (
            "gmail_search",
            lambda query: pytest.fail("Use bound connector"),
            {"query": {"type": "string"}},
            "read_only",
        ),
        (
            "gmail_read",
            lambda thread_id: pytest.fail("Use bound connector"),
            {"thread_id": {"type": "string"}},
            "read_only",
        ),
    ]
    actions = [
        {"tool": "gmail_search", "args": {"query": "fixture"}},
        {"tool": "gmail_read", "args": {"thread_id": "observed"}},
        {"tool": "finish", "args": {}},
    ]
    app, _, _ = make_app(actions, tools)
    bindings = []
    expected = {"service": "gmail", "generation": "fixture-generation"}

    class Connection:
        def binding(self):
            return expected

        def assert_binding(self, binding):
            assert binding is expected

        async def threads(self, query, *, connection_binding):
            bindings.append(connection_binding)
            return {"threads": [{"id": "observed"}]}

        async def thread(self, identity, *, connection_binding):
            bindings.append(connection_binding)
            return {"id": identity, "messages": []}

    app.state.integration_services = SimpleNamespace(google=SimpleNamespace(gmail=Connection()))
    await run_adaptive(app, "Find and read Gmail email", [], asyncio.Event())
    assert len(bindings) == 2 and all(binding is expected for binding in bindings)


async def test_active_file_scope_and_read_before_create_precondition():
    tools = [
        (
            "file_retrieve",
            lambda attachment_ids, query: [
                {"text": "Rental date: 2026-11-16", "file_id": attachment_ids[0]}
            ],
            {
                "attachment_ids": {"type": "array", "items": {"type": "string"}},
                "query": {"type": "string"},
            },
            "read_only",
        ),
        (
            "file_create",
            lambda filename, format, content: {
                "id": "created",
                "filename": filename,
                "source": "generated",
            },
            {
                "filename": {"type": "string"},
                "format": {"type": "string"},
                "content": {"type": "string"},
            },
            "local_reversible",
        ),
    ]
    create = {
        "tool": "file_create",
        "args": {"filename": "rental.md", "format": "md", "content": "Rental date: 2026-11-16"},
    }
    actions = [
        create,
        {"tool": "file_retrieve", "args": {"query": "rental date"}},
        create,
        {"tool": "finish", "args": {}},
    ]
    app, _, calls = make_app(actions, tools)
    traces = []
    result = await run_adaptive(
        app,
        "Read the attached document and create a Markdown file",
        ["active-doc"],
        asyncio.Event(),
        traces=traces,
    )
    assert calls[0] == ("file_retrieve", {"query": "rental date", "attachment_ids": ["active-doc"]})
    assert calls[1][0] == "file_create" and result["repairs"] == 1
    assert next(trace for trace in traces if "file" in trace)["file"]["id"] == "created"


async def test_foreign_attachment_scope_is_rejected_without_document_access():
    tools = [
        (
            "file_retrieve",
            lambda **args: pytest.fail("Must not access foreign file"),
            {"attachment_ids": {"type": "array"}, "query": {"type": "string"}},
            "read_only",
        )
    ]
    action = {
        "tool": "file_retrieve",
        "args": {"query": "signature", "attachment_ids": ["foreign-doc"]},
    }
    app, _, calls = make_app([action, action], tools)
    with pytest.raises(AdaptiveError, match="active attachment"):
        await run_adaptive(app, "Read the attached document", ["active-doc"], asyncio.Event())
    assert not calls


async def test_cancellation_interrupts_a_model_call_and_releases_inference_lock():
    started, canceled = asyncio.Event(), asyncio.Event()

    async def blocked(messages):
        started.set()
        try:
            await asyncio.sleep(30)
        finally:
            canceled.set()

    app, _, _ = make_app(blocked, web_tools())
    event = asyncio.Event()
    task = asyncio.create_task(run_adaptive(app, "Search the web", [], event))
    await asyncio.wait_for(started.wait(), 1)
    event.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert canceled.is_set() and not app.state.model_queue.lock.locked()


async def test_cancellation_interrupts_an_active_tool_and_releases_worker():
    started, canceled = asyncio.Event(), asyncio.Event()

    async def search(query):
        started.set()
        try:
            await asyncio.sleep(30)
        finally:
            canceled.set()

    app, _, _ = make_app(
        [{"tool": "web_search", "args": {"query": "source"}}],
        [("web_search", search, {"query": {"type": "string"}}, "read_only")],
    )
    event = asyncio.Event()
    task = asyncio.create_task(run_adaptive(app, "Search the web", [], event))
    await asyncio.wait_for(started.wait(), 1)
    event.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert canceled.is_set() and not app.state.model_queue.lock.locked()


async def test_whole_workflow_deadline_includes_model_queue_and_releases_lock(monkeypatch):
    monkeypatch.setattr(adaptive, "ADAPTIVE_TIMEOUT", 0.02)

    async def blocked(messages):
        await asyncio.sleep(30)

    app, _, _ = make_app(blocked, web_tools())
    with pytest.raises(AdaptiveError) as error:
        await run_adaptive(app, "Search the web", [], asyncio.Event())
    assert error.value.code == "adaptive_timeout" and "time limit" in str(error.value)
    assert not app.state.model_queue.lock.locked()


async def test_structured_reasoning_is_rejected_without_persisting_private_text():
    action = {
        "tool": "web_search",
        "args": {"query": "source"},
        "reasoning": "private chain of thought",
    }
    app, _, calls = make_app([action, action], web_tools())
    traces = []
    with pytest.raises(ValueError) as error:
        await run_adaptive(app, "Search the web", [], asyncio.Event(), traces=traces)
    assert (
        not calls
        and "private chain" not in json.dumps(traces)
        and "private chain" not in str(error.value)
    )


@pytest.mark.parametrize(
    "command, expected",
    [
        ("/task send email to fixture@example.invalid", "gmail_send"),
        ("/agent draft email to fixture@example.invalid", "gmail_draft"),
        ("/task edit the attached document", "file_edit"),
        ("/agent create calendar meeting", "calendar_create"),
        ("/task /agent find documentation and read one page", "adaptive_task"),
        ("/research inspect web sources", "web_research"),
    ],
)
def test_explicit_tasks_preserve_dedicated_approval_and_research_paths(command, expected):
    assert route_prompt(command).intent == expected


async def test_failure_records_actual_tool_type_and_stage_without_gathered_evidence():
    from pixel_station.providers.web import IntegrationError

    def failed(query):
        raise IntegrationError("Search engines unavailable", "search_engines_unavailable", 503)

    app, _, calls = make_app(
        [{"tool": "web_search", "args": {"query": "source"}}],
        [("web_search", failed, {"query": {"type": "string"}}, "read_only")],
    )
    traces = []
    with pytest.raises(IntegrationError):
        await run_adaptive(app, "Search the web", [], asyncio.Event(), traces=traces)
    assert len(calls) == 1
    assert traces[-1]["tool"] == "web_search" and traces[-1]["stage"] == "adaptive_tool"
    assert traces[-1]["error_code"] == "search_engines_unavailable"


async def test_metadata_and_huge_observations_are_bounded():
    tools = [
        (
            "web_search",
            lambda query: [
                {
                    "url": f"https://fixture.example/{index}",
                    "title": "title" * 1000,
                    "snippet": "data" * 10000,
                }
                for index in range(100)
            ],
            {"query": {"type": "string"}},
            "read_only",
        )
    ]
    app, requests, _ = make_app(
        [{"tool": "web_search", "args": {"query": "source"}}, {"tool": "finish", "args": {}}], tools
    )
    traces = []
    result = await run_adaptive(app, "Search the web", [], asyncio.Event(), traces=traces)
    assert len(result["sources"]) == 8 and all(
        len(source["title"]) <= 1000 for source in result["sources"]
    )
    assert len(result["content"]) <= adaptive.MAX_EVIDENCE_CHARS
    assert traces[0]["result"]["truncated"]
    assert (
        sum(len(item["content"]) for item in requests[-1]["messages"])
        <= adaptive.input_budget(app.state.settings()) * 4
    )


async def test_request_that_cannot_fit_is_rejected_before_inference():
    app, requests, _ = make_app([], web_tools())
    with pytest.raises(AdaptiveError, match="context budget"):
        await run_adaptive(app, "Search web " + "data " * 9000, [], asyncio.Event())
    assert not requests


def test_action_context_keeps_latest_task_and_completed_observations_in_one_user_message():
    settings = AppSettings(context_tokens=2048)
    observations = [
        {"tool": "web_search", "step_id": "a1", "result": "An observed URL"},
        {"tool": "web_fetch", "step_id": "a2", "result": "A fetched fact"},
    ]
    task = "Find documentation, read one page, then give two facts"
    messages = adaptive._messages(
        settings,
        task,
        ["web_search", "web_fetch"],
        [],
        observations,
        "Use finish if sufficient evidence is collected",
    )
    assert [message["role"] for message in messages] == ["system", "user"]
    assert (
        task in messages[-1]["content"]
        and "Completed tools: web_search, web_fetch" in messages[-1]["content"]
    )
    assert "A fetched fact" in messages[-1]["content"] and "Use finish" in messages[-1]["content"]
    assert (
        sum(len(message["content"]) for message in messages) <= adaptive.input_budget(settings) * 4
    )
