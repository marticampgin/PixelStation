from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from pixel_station.app import create_app
from pixel_station.google_tools import CalendarReadArguments, google_chat_action
from pixel_station.providers.web import IntegrationError


class LocalModel:
    def __init__(self):
        self.requests = []

    async def models(self):
        return {
            "available": True,
            "models": [{"name": "test:latest", "capabilities": ["completion"]}],
        }

    async def structured(self, model, messages, schema, **kwargs):
        self.requests.append(messages)
        if schema.__name__ == "GmailReadArguments":
            return schema(
                query="from:alice after:2026/09/26 before:2026/10/03",
                include_bodies=True,
                read_limit=2,
            )
        if schema.__name__ == "CalendarReadArguments":
            return schema(
                calendar_id="primary",
                time_min="2026-10-09T00:00:00+03:00",
                time_max="2026-10-10T00:00:00+03:00",
                query="dentist",
            )
        raise AssertionError(schema)


@pytest.fixture
def app(tmp_path):
    model = LocalModel()
    app = create_app(tmp_path, llm=model, discover=False)
    settings = app.state.settings()
    settings.roles["primary_chat"] = "test:latest"
    app.state.set_settings(settings)
    calls = []

    async def threads(query):
        calls.append(("threads", query))
        return {
            "threads": [
                {"id": "actual-1", "subject": "Invoice"},
                {"id": "actual-2", "subject": "Question"},
            ],
            "next_page_token": None,
        }

    async def thread(identity):
        calls.append(("thread", identity))
        return {"id": identity, "messages": [{"body": "Actual email body", "from": "Alice"}]}

    async def events(calendar, start, end, query):
        calls.append(("events", calendar, start, end, query))
        return {"items": [{"id": "actual-event", "summary": "Dentist"}]}

    app.state.integration_services.google = SimpleNamespace(
        status=lambda: {"connected": True}, threads=threads, thread=thread, events=events
    )
    app.state.test_calls = calls
    return app


async def test_gmail_honors_search_query_and_reads_actual_matched_bodies(app):
    result = await google_chat_action(app, "gmail_read", "Read Alice's emails from last week")
    assert app.state.test_calls[0] == ("threads", "from:alice after:2026/09/26 before:2026/10/03")
    assert {value[1] for value in app.state.test_calls if value[0] == "thread"} == {
        "actual-1",
        "actual-2",
    }
    assert "Actual email body" in result["content"]
    assert result["tool_steps"]["total"] == 3
    assert "Europe/Riga" in app.state.llm.requests[0][0]["content"]


async def test_calendar_honors_requested_local_day_window(app):
    result = await google_chat_action(
        app, "calendar_read", "What dentist appointments are on Friday October 9?"
    )
    assert app.state.test_calls == [
        ("events", "primary", "2026-10-09T00:00:00+03:00", "2026-10-10T00:00:00+03:00", "dentist")
    ]
    assert result["events"][0]["id"] == "actual-event"


async def test_google_setup_state_avoids_unnecessary_model_calls(app):
    app.state.integration_services.google.status = lambda: {"connected": False}
    with pytest.raises(IntegrationError, match="not connected"):
        await google_chat_action(app, "gmail_read", "Read email")
    assert app.state.llm.requests == []


async def test_gmail_read_respects_tool_budget(app):
    settings = app.state.settings()
    settings.max_steps = 1
    app.state.set_settings(settings)
    with pytest.raises(ValueError, match="two tool steps"):
        await google_chat_action(app, "gmail_read", "Read Alice email")
    assert app.state.test_calls == []


def test_calendar_read_range_schema_requires_offsets_and_bounded_range():
    with pytest.raises(ValidationError):
        CalendarReadArguments(time_min="2026-10-09T00:00:00", time_max="2026-10-10T00:00:00")
    with pytest.raises(ValidationError):
        CalendarReadArguments(
            time_min="2026-10-10T00:00:00+03:00", time_max="2026-10-09T00:00:00+03:00"
        )
