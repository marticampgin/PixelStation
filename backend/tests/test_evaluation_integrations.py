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
