"""Official Gmail/Calendar APIs with desktop OAuth and OS-keyring token storage."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time
from datetime import datetime
from email.message import EmailMessage
from email.utils import getaddresses
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from .web import IntegrationError, ReadableHTML

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/gmail.compose",
          "https://www.googleapis.com/auth/calendar.events", "https://www.googleapis.com/auth/calendar.calendarlist.readonly"]
GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
CALENDAR = "https://www.googleapis.com/calendar/v3"


class GoogleConnector:
    def __init__(self, data_dir: Path, *, transport: httpx.AsyncBaseTransport | None = None,
                 credential_loader: Any = None, secret_store: Any = None) -> None:
        self.root = data_dir / "connectors" / "google"
        self.root.mkdir(parents=True, exist_ok=True)
        self.credentials_path = self.root / "credentials.json"
        self.account = hashlib.sha256(str(self.root.resolve()).encode()).hexdigest()[:24]
        self.transport, self.credential_loader = transport, credential_loader
        self.secret_store = secret_store
        self.pending: dict[str, tuple[float, Any]] = {}
        self.refresh_lock = asyncio.Lock()

    def _keyring(self) -> Any:
        if self.secret_store:
            return self.secret_store
        try:
            import keyring
            if keyring.get_keyring().priority <= 0:
                raise RuntimeError("No OS keyring")
            return keyring
        except (ImportError, RuntimeError) as exc:
            raise IntegrationError("A working OS credential keyring is required for Google tokens; install keyring and enable Windows Credential Manager.", "keyring_unavailable") from exc

    def _secret(self) -> str | None:
        try:
            return self._keyring().get_password("PixelStation.Google", self.account)
        except IntegrationError:
            raise
        except Exception as exc:
            raise IntegrationError("Windows Credential Manager is unavailable; Google connection cannot securely load tokens.", "keyring_unavailable") from exc

    def _save_secret(self, value: str) -> None:
        try:
            self._keyring().set_password("PixelStation.Google", self.account, value)
        except Exception as exc:
            raise IntegrationError("Google token could not be saved in the OS keyring. Connection was not persisted.", "keyring_unavailable") from exc

    def status(self) -> dict[str, Any]:
        configured = self.credentials_path.is_file()
        try:
            connected = bool(self.credential_loader or self._secret())
            message = "Google account connected." if connected else "Import Desktop OAuth credentials, then complete Google authorization in your browser."
        except IntegrationError as exc:
            connected, message = False, str(exc)
        return {"configured": configured, "connected": connected, "message": message, "scopes": SCOPES}

    def import_credentials(self, credentials: dict[str, Any]) -> dict[str, Any]:
        installed = credentials.get("installed")
        if not isinstance(installed, dict) or not installed.get("client_id") or not installed.get("client_secret"):
            raise IntegrationError("Import credentials downloaded for a Google OAuth Desktop App client, with an installed section.", "invalid_credentials", 422)
        # Never allow imported credentials to redirect token exchanges to an arbitrary host.
        for key, expected in {"auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token"}.items():
            uri = installed.get(key, expected)
            parsed = urlsplit(uri)
            if parsed.scheme != "https" or parsed.hostname not in {"accounts.google.com", "oauth2.googleapis.com"} or parsed.username:
                raise IntegrationError("OAuth authorization/token endpoints must be official Google HTTPS endpoints.", "invalid_credentials", 422)
        clean = {"installed": {**installed, "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token"}}
        if len(json.dumps(clean)) > 20_000:
            raise IntegrationError("OAuth credential file is unexpectedly large.", "invalid_credentials", 422)
        # Replacing a client invalidates this installation's previous connection.
        if self.credentials_path.exists():
            self.disconnect()
        temp = self.root / "credentials.tmp"
        temp.write_text(json.dumps(clean), encoding="utf-8")
        temp.replace(self.credentials_path)
        self.pending.clear()
        return self.status()

    def authorize(self, callback_url: str) -> dict[str, Any]:
        if not self.credentials_path.is_file():
            raise IntegrationError("Import your Google Desktop OAuth credentials first.", "google_credentials_missing", 422)
        self._keyring()
        parsed = urlsplit(callback_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.username:
            raise IntegrationError("Desktop OAuth callback must use a local loopback HTTP URL.", "invalid_redirect", 422)
        try:
            from google_auth_oauthlib.flow import Flow
            flow = Flow.from_client_config(json.loads(self.credentials_path.read_text(encoding="utf-8")), scopes=SCOPES,
                                           redirect_uri=callback_url, autogenerate_code_verifier=True)
            authorization_url, state = flow.authorization_url(access_type="offline", prompt="consent", include_granted_scopes="true")
        except ImportError as exc:
            raise IntegrationError("Install google-auth-oauthlib to connect Google.", "google_dependencies_missing") from exc
        self.pending = {key: entry for key, entry in self.pending.items() if entry[0] > time.monotonic()}
        self.pending[state] = (time.monotonic() + 600, flow)
        return {"authorization_url": authorization_url, "expires_in": 600}

    async def complete_authorization(self, state: str, code: str) -> dict[str, Any]:
        entry = self.pending.pop(state, None)
        if not entry or entry[0] < time.monotonic() or not code:
            raise IntegrationError("OAuth state is invalid, expired or already used. Start Connect Google again.", "invalid_oauth_state", 422)
        flow = entry[1]
        try:
            await asyncio.wait_for(asyncio.to_thread(flow.fetch_token, code=code, timeout=30), 35)
            credentials = flow.credentials
            if not credentials.refresh_token:
                raise IntegrationError("Google did not issue a refresh token. Reconnect and grant offline access.", "refresh_token_missing")
            self._save_secret(credentials.to_json())
        except IntegrationError:
            raise
        except Exception as exc:
            raise IntegrationError("Google authorization failed. Check consent, API access and OAuth client configuration.", "oauth_failed") from exc
        return self.status()

    def disconnect(self) -> None:
        self.pending.clear()
        try:
            store = self._keyring()
            if store.get_password("PixelStation.Google", self.account):
                store.delete_password("PixelStation.Google", self.account)
        except IntegrationError:
            raise
        except Exception as exc:
            raise IntegrationError("Could not remove Google credentials from the OS keyring.", "keyring_unavailable") from exc

    async def _token(self) -> str:
        if self.credential_loader:
            value = self.credential_loader()
            return await value if hasattr(value, "__await__") else value
        async with self.refresh_lock:
            raw = self._secret()
            if not raw:
                raise IntegrationError("Connect Google in Settings to access Gmail or Calendar.", "google_not_connected", 401)
            try:
                from google.auth.transport.requests import Request
                from google.oauth2.credentials import Credentials
                credentials = Credentials.from_authorized_user_info(json.loads(raw), scopes=SCOPES)
                if not credentials.valid:
                    await asyncio.wait_for(asyncio.to_thread(credentials.refresh, Request()), 35)
                    self._save_secret(credentials.to_json())
                return credentials.token
            except IntegrationError:
                raise
            except Exception as exc:
                raise IntegrationError("Google authorization expired or was revoked. Reconnect in Settings.", "google_reconnect", 401) from exc

    async def request(self, method: str, url: str, *, params: dict[str, Any] | None = None,
                      body: dict[str, Any] | None = None, etag: str | None = None) -> dict[str, Any]:
        token = await self._token()
        headers = {"Authorization": "Bearer " + token}
        if etag:
            headers["If-Match"] = etag
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(25, connect=5), transport=self.transport, trust_env=False) as client:
                async with client.stream(method, url, params=params, json=body, headers=headers) as response:
                    if response.status_code == 412:
                        raise IntegrationError("The event changed since your proposal. Reload it and approve a new proposal.", "event_changed", 409)
                    if response.status_code in {401, 403}:
                        raise IntegrationError("Google access denied. Reconnect or check enabled APIs, scopes and Workspace policy.", "google_access_denied", 403)
                    if response.status_code == 404:
                        raise IntegrationError("The Google item no longer exists.", "not_found", 404)
                    response.raise_for_status()
                    if response.status_code == 204:
                        return {"deleted": True}
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 10_000_000:
                            raise IntegrationError("Google response exceeded 10 MB. Narrow the search.", "response_too_large", 422)
                    return json.loads(raw)
        except (httpx.HTTPError, ValueError) as exc:
            raise IntegrationError("Google API request failed. Check connectivity and account permissions.", "google_api_failed") from exc

    async def threads(self, query: str = "", page_token: str = "") -> dict[str, Any]:
        params = {"maxResults": 20, "q": query[:2000]}
        if page_token:
            params["pageToken"] = page_token
        result = await self.request("GET", GMAIL + "/threads", params=params)
        ids = [item["id"] for item in result.get("threads", [])]
        semaphore = asyncio.Semaphore(4)

        async def load(id_: str) -> dict[str, Any]:
            async with semaphore:
                thread = await self.thread(id_)
                last = thread["messages"][-1] if thread["messages"] else {}
                return {"id": id_, "subject": last.get("subject", ""), "from": last.get("from", ""), "date": last.get("date", ""), "snippet": thread.get("snippet", "")}

        return {"threads": await asyncio.gather(*(load(id_) for id_ in ids)), "next_page_token": result.get("nextPageToken")}

    @staticmethod
    def _message(message: dict[str, Any]) -> dict[str, Any]:
        payload = message.get("payload", {})
        headers = {str(h.get("name", "")).lower(): str(h.get("value", "")) for h in payload.get("headers", [])}
        plain, html, attachments = [], [], []
        pending = [payload]
        count = 0
        while pending and count < 256:
            part = pending.pop()
            count += 1
            pending.extend(part.get("parts", []))
            body = part.get("body", {})
            if part.get("filename"):
                attachments.append({"filename": part["filename"], "mime_type": part.get("mimeType"), "size": body.get("size", 0)})
                continue
            if body.get("data") and part.get("mimeType") in {"text/plain", "text/html"}:
                encoded = body["data"][:400_000]
                try:
                    decoded = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode("utf-8", errors="replace")
                except (ValueError, TypeError):
                    continue
                (plain if part["mimeType"] == "text/plain" else html).append(decoded)
        if plain:
            text = "\n".join(plain)
        else:
            parser = ReadableHTML()
            parser.feed("\n".join(html))
            _, text = parser.result()
        return {"id": message.get("id"), "subject": headers.get("subject", ""), "from": headers.get("from", ""),
                "to": headers.get("to", ""), "date": headers.get("date", ""), "body": text[:50_000],
                "message_id": headers.get("message-id", ""), "attachments": attachments}

    async def thread(self, id_: str) -> dict[str, Any]:
        result = await self.request("GET", GMAIL + "/threads/" + quote(id_, safe=""), params={"format": "full"})
        return {"id": result.get("id", id_), "snippet": result.get("messages", [{}])[-1].get("snippet", "") if result.get("messages") else "",
                "messages": [self._message(message) for message in result.get("messages", [])]}

    @staticmethod
    def email_payload(to: str, subject: str, body: str, thread_id: str | None = None, in_reply_to: str | None = None) -> dict[str, Any]:
        if any("\r" in value or "\n" in value for value in [to, subject, in_reply_to or ""]) or not subject.strip() or len(body) > 200_000:
            raise IntegrationError("Provide a recipient, subject and body; email headers cannot contain newlines.", "invalid_email", 422)
        recipients = getaddresses([to])
        if not recipients or any("@" not in address or not address.split("@", 1)[0] or "." not in address.rsplit("@", 1)[-1] for _, address in recipients):
            raise IntegrationError("Enter valid recipient email addresses.", "invalid_email", 422)
        msg = EmailMessage()
        msg["To"], msg["Subject"] = to, subject
        if in_reply_to:
            msg["In-Reply-To"], msg["References"] = in_reply_to, in_reply_to
        msg.set_content(body)
        payload: dict[str, Any] = {"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")}
        if thread_id:
            payload["threadId"] = thread_id
        return payload

    async def create_draft(self, **email: Any) -> dict[str, Any]:
        result = await self.request("POST", GMAIL + "/drafts", body={"message": self.email_payload(**email)})
        if not result.get("id"):
            raise IntegrationError("Gmail did not return a draft ID.", "draft_validation_failed")
        # Reopen the actual artifact to validate creation.
        verified = await self.request("GET", GMAIL + "/drafts/" + quote(result["id"], safe=""), params={"format": "minimal"})
        if verified.get("id") != result["id"]:
            raise IntegrationError("Gmail draft could not be verified.", "draft_validation_failed")
        return result

    async def send_email(self, **email: Any) -> dict[str, Any]:
        result = await self.request("POST", GMAIL + "/messages/send", body=self.email_payload(**email))
        if not result.get("id"):
            raise IntegrationError("Gmail did not return a sent-message ID.", "send_validation_failed")
        return result

    async def calendars(self) -> dict[str, Any]:
        return await self.request("GET", CALENDAR + "/users/me/calendarList", params={"maxResults": 100})

    async def events(self, calendar_id: str, time_min: str, time_max: str, query: str = "") -> dict[str, Any]:
        for value in [time_min, time_max]:
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    raise ValueError("timezone missing")
            except ValueError as exc:
                raise IntegrationError("Calendar range requires RFC3339 timestamps with timezone offsets.", "invalid_range", 422) from exc
        if time_min >= time_max:
            if datetime.fromisoformat(time_min.replace("Z", "+00:00")) >= datetime.fromisoformat(time_max.replace("Z", "+00:00")):
                raise IntegrationError("Calendar range end must follow its start.", "invalid_range", 422)
        return await self.request("GET", CALENDAR + "/calendars/" + quote(calendar_id, safe="") + "/events",
                                  params={"timeMin": time_min, "timeMax": time_max, "singleEvents": "true", "orderBy": "startTime", "maxResults": 100, "q": query[:2000]})

    async def event(self, calendar_id: str, event_id: str) -> dict[str, Any]:
        return await self.request("GET", CALENDAR + "/calendars/" + quote(calendar_id, safe="") + "/events/" + quote(event_id, safe=""))

    @staticmethod
    def validate_event(event: dict[str, Any]) -> dict[str, Any]:
        allowed = {"summary", "description", "location", "start", "end"}
        if not set(event) <= allowed or not str(event.get("summary", "")).strip():
            raise IntegrationError("Event needs a title; allowed fields are summary, description, location, start and end.", "invalid_event", 422)
        values = []
        kinds = []
        for key in ["start", "end"]:
            value = event.get(key, {})
            if not isinstance(value, dict) or not set(value) <= {"date", "dateTime", "timeZone"} or ("date" in value) == ("dateTime" in value):
                raise IntegrationError("Event start/end need either date or dateTime, with optional timeZone.", "invalid_event", 422)
            kind = "dateTime" if "dateTime" in value else "date"
            try:
                parsed = datetime.fromisoformat(value[kind].replace("Z", "+00:00"))
                if kind == "dateTime" and parsed.tzinfo is None:
                    raise ValueError("timezone missing")
                if kind == "date" and len(value[kind]) != 10:
                    raise ValueError("invalid date")
            except (ValueError, TypeError) as exc:
                raise IntegrationError("Use ISO dates or RFC3339 dateTime values with offsets.", "invalid_event", 422) from exc
            values.append(parsed)
            kinds.append(kind)
        if kinds[0] != kinds[1] or values[1] <= values[0] or len(json.dumps(event)) > 30_000:
            raise IntegrationError("Event end must follow start using the same date format; event must be under 30 KB.", "invalid_event", 422)
        return event

    async def mutate_event(self, action: str, calendar_id: str, event: dict[str, Any] | None = None,
                           event_id: str | None = None, etag: str | None = None, **_: Any) -> dict[str, Any]:
        url = CALENDAR + "/calendars/" + quote(calendar_id, safe="") + "/events"
        if event_id:
            url += "/" + quote(event_id, safe="")
        method = {"calendar_create": "POST", "calendar_update": "PATCH", "calendar_delete": "DELETE"}[action]
        result = await self.request(method, url, body=self.validate_event(event) if event else None, etag=etag)
        if action != "calendar_delete":
            if not result.get("id"):
                raise IntegrationError("Google did not return an event ID.", "event_validation_failed")
            verified = await self.event(calendar_id, result["id"])
            if verified.get("id") != result["id"]:
                raise IntegrationError("Created event could not be verified.", "event_validation_failed")
        return result
