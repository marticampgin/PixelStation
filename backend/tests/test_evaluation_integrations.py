from pixel_station.evaluations import deterministic_cases


def case(identifier):
    return next(row for row in deterministic_cases() if row["id"] == identifier)


def test_gmail_gate_detects_dropped_attachment_bytes(monkeypatch):
    from pixel_station.providers.google import GoogleConnection

    original = GoogleConnection.email_payload

    def drop_attachments(*args, **kwargs):
        kwargs.pop("attachments", None)
        return original(*args, **kwargs)

    monkeypatch.setattr(GoogleConnection, "email_payload", staticmethod(drop_attachments))
    result = case("gmail_mime")
    assert result["status"] == "FAIL"
    assert result["measurements"]["attachment_bytes_preserved"] is False
    assert result["measurements"]["external_requests"] == 0


def test_citation_gate_detects_unobserved_links_surviving(monkeypatch):
    from pixel_station import chat

    monkeypatch.setattr(chat, "validated_citations", lambda content, sources: (content, []))
    result = case("web_citations")
    assert result["status"] == "FAIL"
    assert result["scope"] == "isolated_fixture"


def test_observation_gate_detects_tool_fetch_ignoring_actual_search_output(monkeypatch):
    from pixel_station.orchestration import ToolRegistry

    original = ToolRegistry.execute

    async def invented_target(self, identifier, args, **kwargs):
        if identifier == "web_fetch":
            args = {**args, "url": "https://fixture.example/invented-target"}
        return await original(self, identifier, args, **kwargs)

    monkeypatch.setattr(ToolRegistry, "execute", invented_target)
    result = case("observed_workflow")
    assert result["status"] == "FAIL"
    assert result["measurements"]["fetch_used_observed_url"] is False


def test_exact_date_gate_detects_range_end_or_test_qualifier_loss(monkeypatch):
    from pixel_station import chat

    monkeypatch.setattr(
        chat,
        "exact_document_dates",
        lambda *args, **kwargs: "Signature date: 02.10.2099\nRental date: 16.10.2099",
    )
    result = case("document_dates")
    assert result["status"] == "FAIL"
    assert result["measurements"]["range_and_qualifiers_preserved"] is False


def test_adaptive_stop_gate_detects_false_success_status(monkeypatch):
    from pixel_station import chat

    original = chat.generate_response

    async def falsely_complete(*args, **kwargs):
        import json

        async for event in original(*args, **kwargs):
            row = json.loads(event)
            if row["type"] == "done":
                row["message"]["status"] = "complete"
            yield json.dumps(row)

    monkeypatch.setattr(chat, "generate_response", falsely_complete)
    result = case("adaptive_stop")
    assert result["status"] == "FAIL"
    assert result["measurements"]["confirmed_success_not_reported"] is False


def test_calendar_gate_detects_invalid_mutation_ranges_being_accepted(monkeypatch):
    from pixel_station.providers.google import GoogleConnection

    monkeypatch.setattr(GoogleConnection, "validate_event", staticmethod(lambda event: event))
    result = case("calendar_bounds")
    assert result["status"] == "FAIL"
    assert result["measurements"]["invalid_ranges_rejected"] == 3
