import asyncio
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pixel_station.integrations import IntegrationServices, create_integrations_router
from pixel_station.providers.google import CALENDAR, GMAIL, SERVICE_SCOPES, GoogleConnector
from pixel_station.providers.web import IntegrationError


class SecretStore:
    def __init__(self):
        self.values = {}

    def get_password(self, service, account):
        return self.values.get((service, account))

    def set_password(self, service, account, value):
        self.values[service, account] = value

    def delete_password(self, service, account):
        del self.values[service, account]


def desktop(client_id="fixture-client"):
    return {"installed": {"client_id": client_id, "client_secret": "fixture-secret"}}


def envelope(service, generation="first", account=None, *, expired=False):
    identity = account or f"{service}@example.com"
    return {
        "version": 2, "service": service, "generation": generation,
        "account": {"label": identity, "email" if service == "gmail" else "calendar_id": identity},
        "credentials": {
            "token": f"{service}-token-{generation}", "refresh_token": "fixture-refresh",
            "token_uri": "https://oauth2.googleapis.com/token", "client_id": "fixture-client",
            "client_secret": "fixture-secret", "scopes": SERVICE_SCOPES[service],
            "expiry": "2000-01-01T00:00:00Z" if expired else "2099-01-01T00:00:00Z",
        },
    }


def connect(connector, service, **kwargs):
    connector.connection(service)._save_secret(json.dumps(envelope(service, **kwargs)))


def app_services(tmp_path, *, transport=None, store=None):
    service = IntegrationServices(tmp_path, lambda key, default: default, lambda key, value: None)
    service.google = GoogleConnector(tmp_path, transport=transport, secret_store=store or SecretStore())
    return service


@pytest.fixture
def fake_oauth(monkeypatch):
    from google_auth_oauthlib.flow import Flow

    flows = []

    class Credentials:
        refresh_token = "fixture-refresh"

        def __init__(self, service):
            self.token = f"{service}-oauth-token"
            self.granted_scopes = SERVICE_SCOPES[service]
            self.service = service

        def to_json(self):
            value = envelope(self.service)["credentials"]
            value["token"] = self.token
            return json.dumps(value)

    class FakeFlow:
        def __init__(self, scopes, redirect_uri):
            self.scopes, self.redirect_uri = scopes, redirect_uri
            self.service = "gmail" if scopes == SERVICE_SCOPES["gmail"] else "calendar"
            self.credentials = Credentials(self.service)
            self.state = f"state-{self.service}-{len(flows)}"
            flows.append(self)

        def authorization_url(self, **kwargs):
            self.options = kwargs
            return f"https://accounts.google.com/o/oauth2/auth?state={self.state}", self.state

        def fetch_token(self, **kwargs):
            self.exchange = kwargs

    monkeypatch.setattr(Flow, "from_client_config", lambda config, *, scopes, redirect_uri, **kwargs: FakeFlow(scopes, redirect_uri))
    return flows


def test_service_oauth_routes_keep_scopes_states_tokens_and_identities_separate(tmp_path, fake_oauth):
    calls = []

    def serve(request):
        calls.append((request.url.path, request.headers["Authorization"]))
        if request.url.path.endswith("/profile"):
            return httpx.Response(200, json={"emailAddress": "mail-account@example.com"})
        assert request.url.path.endswith("/calendarList/primary")
        return httpx.Response(200, json={"id": "other-calendar@example.com", "primary": True})

    service = app_services(tmp_path, transport=httpx.MockTransport(serve))
    app = FastAPI()
    app.include_router(create_integrations_router(service))
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        assert client.post("/api/google/credentials", json={"credentials": desktop()}).status_code == 200
        gmail = client.get("/api/google/gmail/authorize").json()
        gmail_state = parse_qs(urlsplit(gmail["authorization_url"]).query)["state"][0]
        calendar = client.get("/api/google/calendar/authorize").json()
        calendar_state = parse_qs(urlsplit(calendar["authorization_url"]).query)["state"][0]
        assert fake_oauth[0].scopes == SERVICE_SCOPES["gmail"]
        assert fake_oauth[1].scopes == SERVICE_SCOPES["calendar"]
        assert all(flow.options["prompt"] == "select_account consent" for flow in fake_oauth)
        assert all(flow.options["include_granted_scopes"] == "false" for flow in fake_oauth)
        assert fake_oauth[0].redirect_uri.endswith("/api/google/gmail/callback")
        assert fake_oauth[1].redirect_uri.endswith("/api/google/calendar/callback")
        assert client.get("/api/google/calendar/callback", params={"state": gmail_state, "code": "fixture"}).status_code == 422
        assert gmail_state in service.google.gmail.pending
        assert client.get("/api/google/gmail/callback", params={"state": gmail_state, "code": "fixture"}).status_code == 200
        status = client.get("/api/google/gmail/status").json()
        assert status["connected"] and status["account"] == {"label": "mail-account@example.com", "email": "mail-account@example.com"}
        assert client.get("/api/google/calendar/status").json()["connected"] is False
        assert calls == [("/gmail/v1/users/me/profile", "Bearer gmail-oauth-token")]
        assert calendar_state in service.google.calendar.pending
        assert client.get("/api/google/calendar/callback", params={"state": calendar_state, "code": "fixture"}).status_code == 200
        assert client.get("/api/google/calendar/status").json()["account"]["calendar_id"] == "other-calendar@example.com"
        assert client.post("/api/google/gmail/disconnect").json()["connected"] is False
        assert client.get("/api/google/calendar/status").json()["connected"] is True
        assert client.get("/api/google/gmail/callback", params={"state": gmail_state, "code": "fixture"}).status_code == 422
    reopened = GoogleConnector(tmp_path, secret_store=service.google.gmail.secret_store)
    assert not reopened.service_status("gmail")["connected"]
    assert reopened.service_status("calendar")["account"]["calendar_id"] == "other-calendar@example.com"


def test_shared_client_import_preserves_unchanged_connections_and_invalidates_changed_client(tmp_path, fake_oauth):
    store = SecretStore()
    google = GoogleConnector(tmp_path, secret_store=store)
    google.import_credentials(desktop())
    connect(google, "gmail")
    connect(google, "calendar")
    google.gmail.authorize("http://127.0.0.1:8000/api/google/gmail/callback")
    google.calendar.authorize("http://127.0.0.1:8000/api/google/calendar/callback")
    bindings = {service: google.connection(service).binding() for service in SERVICE_SCOPES}
    google.import_credentials(desktop())
    assert all(google.connection(service).binding() == bindings[service] for service in SERVICE_SCOPES)
    assert google.gmail.pending and google.calendar.pending
    google.import_credentials(desktop("different-client"))
    assert not google.gmail.pending and not google.calendar.pending
    assert all(not google.service_status(service)["connected"] for service in SERVICE_SCOPES)
    for service in SERVICE_SCOPES:
        with pytest.raises(IntegrationError) as error:
            google.connection(service).assert_binding(bindings[service])
        assert error.value.code == "approval_connection_changed"


@pytest.mark.asyncio
async def test_legacy_combined_token_is_flagged_without_reuse_or_new_api_calls(tmp_path):
    store = SecretStore()
    google = GoogleConnector(tmp_path, secret_store=store)
    store.set_password("PixelStation.Google", google.gmail.legacy_account, json.dumps(envelope("gmail")["credentials"]))
    for service in SERVICE_SCOPES:
        status = google.service_status(service)
        assert status["migration_required"] and not status["connected"] and status["account"] is None
        with pytest.raises(IntegrationError) as error:
            google.connection(service).binding()
        assert error.value.code == "google_not_connected"
    assert google.gmail._secret() is None and google.calendar._secret() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["reconnect", "disconnect", "legacy_unbound"])
async def test_approval_cannot_send_from_changed_or_unbound_account(tmp_path, change):
    calls = []
    service = app_services(tmp_path, transport=httpx.MockTransport(lambda request: calls.append(request) or httpx.Response(200, json={"id": "sent"})))
    connect(service.google, "gmail")
    proposal = await service.propose_action("gmail_send", {"to": "recipient@example.com", "subject": "Review", "body": "Exact body"})
    assert proposal["approval"]["payload"]["connection_binding"]["account"]["email"] == "gmail@example.com"
    if change == "reconnect":
        connect(service.google, "gmail", generation="second", account="different@example.com")
    elif change == "disconnect":
        service.google.gmail.disconnect()
    else:
        proposal = {"approval": service.approvals.propose("gmail_send", {"to": "recipient@example.com", "subject": "Old", "body": "Old unbound proposal"})}
    with pytest.raises(IntegrationError) as error:
        await service.confirm(proposal["approval"]["id"])
    assert error.value.code == "approval_connection_changed" and calls == []
    with pytest.raises(IntegrationError):
        await service.confirm(proposal["approval"]["id"])


@pytest.mark.asyncio
async def test_refresh_and_other_service_disconnect_preserve_valid_gmail_approval(tmp_path, monkeypatch):
    from google.oauth2.credentials import Credentials

    calls, refresh_scopes = [], []

    def refresh(credentials, request):
        refresh_scopes.append(credentials.scopes)
        credentials.token = "refreshed-gmail-token"
        credentials.expiry = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)

    monkeypatch.setattr(Credentials, "refresh", refresh)
    service = app_services(tmp_path, transport=httpx.MockTransport(lambda request: calls.append(request) or httpx.Response(200, json={"id": "sent"})))
    connect(service.google, "gmail", expired=True)
    connect(service.google, "calendar")
    proposal = await service.propose_action("gmail_send", {"to": "recipient@example.com", "subject": "Review", "body": "Exact body"})
    binding = service.google.gmail.binding()
    service.google.calendar.disconnect()
    reopened = GoogleConnector(tmp_path, transport=service.google.gmail.transport, secret_store=service.google.gmail.secret_store)
    service.google = reopened
    result = await service.confirm(proposal["approval"]["id"])
    assert result["result"]["id"] == "sent"
    assert calls[0].headers["Authorization"] == "Bearer refreshed-gmail-token"
    assert refresh_scopes == [SERVICE_SCOPES["gmail"]]
    assert service.google.gmail.binding() == binding


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["draft", "calendar"])
async def test_creation_followup_cannot_verify_with_a_different_account(tmp_path, operation):
    service_name = "gmail" if operation == "draft" else "calendar"
    requests = []
    google = None

    def serve(request):
        requests.append(request)
        assert request.method == "POST"
        connect(google, service_name, generation="switched", account="different@example.com")
        return httpx.Response(200, json={"id": "accepted-id"})

    google = GoogleConnector(tmp_path, secret_store=SecretStore(), transport=httpx.MockTransport(serve))
    connect(google, service_name)
    with pytest.raises(IntegrationError) as error:
        if operation == "draft":
            await google.create_draft(to="recipient@example.com", subject="Draft", body="Review")
        else:
            await google.mutate_event("calendar_create", "primary", event={"summary": "Review", "start": {"date": "2026-10-04"}, "end": {"date": "2026-10-05"}})
    assert error.value.code == "accepted_unverified"
    assert error.value.details["returned_id"] == "accepted-id"
    assert len(requests) == 1, "Verification must not query the replacement account"


@pytest.mark.asyncio
async def test_thread_search_does_not_fetch_old_ids_with_new_account(tmp_path):
    requests = []
    google = None

    def serve(request):
        requests.append(request)
        connect(google, "gmail", generation="switched", account="different@example.com")
        return httpx.Response(200, json={"threads": [{"id": "old-account-thread"}]})

    google = GoogleConnector(tmp_path, secret_store=SecretStore(), transport=httpx.MockTransport(serve))
    connect(google, "gmail")
    with pytest.raises(IntegrationError) as error:
        await google.threads("from:alice")
    assert error.value.code == "approval_connection_changed" and len(requests) == 1


@pytest.mark.asyncio
async def test_delayed_token_loader_rechecks_binding_before_http_submission(tmp_path):
    calls = []
    google = None

    async def load():
        await asyncio.sleep(0)
        google.gmail.disconnect()
        return "replacement-token"

    google = GoogleConnector(tmp_path, secret_store=SecretStore(), credential_loader={"gmail": load},
                             transport=httpx.MockTransport(lambda request: calls.append(request) or httpx.Response(200, json={"id": "wrong"})))
    binding = google.gmail.binding()
    with pytest.raises(IntegrationError) as error:
        await google.send_email(to="recipient@example.com", subject="Review", body="Body", connection_binding=binding)
    assert error.value.code == "approval_connection_changed" and calls == []


@pytest.mark.asyncio
async def test_service_connection_rejects_other_service_endpoint(tmp_path):
    google = GoogleConnector(tmp_path, credential_loader=lambda: "fixture-token")
    with pytest.raises(IntegrationError) as error:
        await google.gmail.request("GET", CALENDAR + "/users/me/calendarList")
    assert error.value.code == "google_service_mismatch"
    with pytest.raises(IntegrationError):
        await google.calendar.request("GET", GMAIL + "/profile")
