import base64
import json
from email import policy
from email.parser import BytesParser

import httpx
import pytest

from pixel_station.app import create_app
from pixel_station.files import ingest
from pixel_station.google_tools import google_chat_action
from pixel_station.integrations import EmailInput
from pixel_station.providers.google import GoogleConnector


@pytest.mark.asyncio
@pytest.mark.parametrize("with_attachment", [False, True])
async def test_chat_draft_uses_real_provider_payload_and_verified_managed_snapshot(tmp_path, with_attachment):
    attachment_ids = []

    class DraftModel:
        async def structured(self, model, messages, schema, **kwargs):
            assert schema is EmailInput
            return schema(to="recipient@example.com", subject="Explicit TEST draft",
                          body="Please review this test wording.", attachment_ids=attachment_ids)

    calls = []

    def serve(request):
        calls.append(request)
        if request.method == "POST":
            assert request.url.path.endswith("/drafts")
            payload = json.loads(request.content)["message"]
            parsed = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(payload["raw"]))
            assert parsed["To"] == "recipient@example.com"
            assert parsed["Subject"] == "Explicit TEST draft"
            attached = list(parsed.iter_attachments())
            assert len(attached) == int(with_attachment)
            if with_attachment:
                assert attached[0].get_filename() == "TEST managed rental.txt"
                assert attached[0].get_payload(decode=True) == original
            return httpx.Response(200, json={"id": "verified-test-draft"})
        assert request.method == "GET" and request.url.path.endswith("/drafts/verified-test-draft")
        return httpx.Response(200, json={"id": "verified-test-draft"})

    app = create_app(tmp_path, llm=DraftModel(), discover=False)
    settings = app.state.settings()
    settings.roles["primary_chat"] = "test-local"
    app.state.set_settings(settings)
    app.state.integration_services.google = GoogleConnector(
        tmp_path, credential_loader=lambda: "fixture", transport=httpx.MockTransport(serve),
    )
    original = b"TEST rental dates and package only."
    if with_attachment:
        with app.state.database.session() as session:
            file = ingest(session, tmp_path, "TEST managed rental.txt", original)
            attachment_ids.append(file.id)
    result = await google_chat_action(app, "gmail_draft", "Create the explicit TEST draft.")
    assert result["draft"]["id"] == "verified-test-draft"
    assert "Created Gmail draft verified-test-draft" in result["content"]
    assert [request.method for request in calls] == ["POST", "GET"]
    snapshots = list((tmp_path / "connectors" / "google" / "attachments").glob("*.bin"))
    assert len(snapshots) == int(with_attachment)
    if snapshots:
        assert snapshots[0].read_bytes() == original
        assert result["draft"]["attachment_count"] == 1
    app.state.database.engine.dispose()
