import pytest

from pixel_station.app import create_app


@pytest.mark.asyncio
async def test_registered_draft_uses_reviewed_attachment_service(tmp_path, monkeypatch):
    app = create_app(tmp_path, discover=False)
    calls = []

    async def snapshot_draft(payload):
        calls.append(payload)
        return {"id": "verified-draft"}

    async def direct_provider(**kwargs):
        pytest.fail("The registry must not bypass attachment snapshots")

    services = app.state.integration_services
    monkeypatch.setattr(services, "create_gmail_draft", snapshot_draft)
    monkeypatch.setattr(services.google, "create_draft", direct_provider)
    result = await app.state.tool_registry.execute("gmail_draft", {
        "to": "fixture@example.invalid", "subject": "TEST", "body": "Fictional test",
        "attachment_ids": ["managed-fixture"],
    })
    assert result == {"id": "verified-draft"}
    assert calls[0]["attachment_ids"] == ["managed-fixture"]
    assert calls[0]["to"] == "fixture@example.invalid"
    await services.close()


@pytest.mark.parametrize("tool_id,args", [
    ("gmail_send", {"to": "fixture@example.invalid", "subject": "TEST", "body": "Fictional", "attachment_ids": ["fixture"]}),
    ("calendar_create", {"calendar_id": "primary", "event": {"summary": "Fictional"}}),
    ("calendar_update", {"calendar_id": "primary", "event_id": "observed", "event": {"summary": "Fictional"}}),
    ("calendar_delete", {"calendar_id": "primary", "event_id": "observed"}),
])
@pytest.mark.asyncio
async def test_registry_permission_cannot_replace_concrete_google_review(tmp_path, monkeypatch, tool_id, args):
    app = create_app(tmp_path, discover=False)
    services = app.state.integration_services
    proposals = []

    async def propose(action, payload):
        proposals.append((action, payload))
        return {"approval": {"id": "account-bound-review", "status": "pending"}}

    async def direct_provider(*args, **kwargs):
        pytest.fail("Generic registry approval must not make a remote mutation")

    monkeypatch.setattr(services, "propose_action", propose)
    monkeypatch.setattr(services.google, "send_email", direct_provider)
    monkeypatch.setattr(services.google, "mutate_event", direct_provider)
    with pytest.raises(PermissionError):
        await app.state.tool_registry.execute(tool_id, args)
    assert proposals == []
    result = await app.state.tool_registry.execute(tool_id, args, approved=True)
    assert result["approval"]["status"] == "pending"
    assert len(proposals) == 1 and proposals[0][0] == tool_id
    assert all(proposals[0][1][key] == value for key, value in args.items())
    await services.close()
