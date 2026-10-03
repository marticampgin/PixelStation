"""Bounded SearXNG search and public-web fetch. No paid search fallback."""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
import time
from collections.abc import Callable
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx


class IntegrationError(Exception):
    def __init__(self, message: str, code: str = "integration_error", status: int = 503,
                 details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code, self.status = code, status
        self.details = details or {}


def endpoint_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
        raise IntegrationError("Use an HTTP(S) service endpoint without credentials.", "invalid_endpoint", 422)
    if parsed.query or parsed.fragment:
        raise IntegrationError("The service endpoint must not contain a query or fragment.", "invalid_endpoint", 422)
    return value.rstrip("/")


def canonical_url(value: str) -> str:
    p = urlsplit(value)
    query = [(k, v) for k, v in parse_qsl(p.query) if not k.lower().startswith("utm_") and k not in {"fbclid", "gclid"}]
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path or "/", urlencode(query), ""))


class ReadableHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.title_depth = 0
        self.parts: list[str] = []
        self.title: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg", "nav", "footer"}:
            self.skip += 1
        if tag == "title":
            self.title_depth += 1
        if tag in {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "section", "article"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg", "nav", "footer"} and self.skip:
            self.skip -= 1
        if tag == "title" and self.title_depth:
            self.title_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.title_depth:
            self.title.append(data)
        elif not self.skip:
            self.parts.append(data)

    def result(self) -> tuple[str, str]:
        body = re.sub(r"[ \t\r\f\v]+", " ", "".join(self.parts))
        body = re.sub(r"\n\s*\n+", "\n\n", body).strip()
        return " ".join(self.title).strip(), body


class WebProvider:
    def __init__(self, endpoint: Callable[[], str], *, transport: httpx.AsyncBaseTransport | None = None,
                 resolver: Callable[[str, int], Any] | None = None) -> None:
        self.endpoint = endpoint
        self.transport = transport
        self.resolver = resolver
        self.cache: dict[str, tuple[float, dict[str, Any]]] = {}

    async def status(self) -> dict[str, Any]:
        url = self.endpoint()
        if not url:
            return {"available": False, "endpoint": "", "message": "Configure a SearXNG endpoint in Settings. See docs/INTEGRATIONS.md."}
        try:
            async with httpx.AsyncClient(timeout=5, transport=self.transport, trust_env=False) as client:
                response = await client.get(endpoint_url(url) + "/config")
                response.raise_for_status()
            return {"available": True, "endpoint": url, "message": "SearXNG responds. Use Test search to verify enabled JSON search."}
        except (httpx.HTTPError, IntegrationError):
            return {"available": False, "endpoint": url, "message": "SearXNG is unavailable. Start the optional Compose service and enable JSON search."}

    async def search(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        try:
            return await asyncio.wait_for(self._search(query, limit), timeout=25)
        except TimeoutError as exc:
            raise IntegrationError("SearXNG search exceeded its 25-second total limit.", "search_timeout") from exc

    async def _search(self, query: str, limit: int) -> list[dict[str, Any]]:
        query = query.strip()
        if not query or len(query) > 2000:
            raise IntegrationError("Search query must contain 1–2000 characters.", "invalid_query", 422)
        if not self.endpoint():
            raise IntegrationError("Configure SearXNG in Settings > Web. Start config/docker-compose.optional.yml.", "searxng_missing")
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=5), transport=self.transport, trust_env=False) as client:
                async with client.stream("GET", endpoint_url(self.endpoint()) + "/search", params={"q": query, "format": "json"}) as response:
                    if response.status_code == 403:
                        raise IntegrationError("SearXNG JSON search is disabled. Add json to search.formats in settings.yml.", "json_disabled")
                    response.raise_for_status()
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 2_000_000:
                            raise IntegrationError("SearXNG response exceeded 2 MB.", "response_too_large")
                    import json
                    payload = json.loads(raw)
        except (httpx.HTTPError, ValueError) as exc:
            raise IntegrationError("SearXNG search failed. Check its endpoint, connection and JSON configuration.", "search_failed") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("results", []), list):
            raise IntegrationError("SearXNG returned an invalid JSON search response.", "invalid_search_response")
        results, seen = [], set()
        for item in payload.get("results", []):
            if not isinstance(item, dict):
                continue
            try:
                raw_url = str(item.get("url", ""))
                parsed = urlsplit(raw_url)
                if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
                    continue
                url = canonical_url(raw_url)
            except ValueError:
                continue
            if url in seen:
                continue
            seen.add(url)
            results.append({"url": url, "title": str(item.get("title", url))[:500],
                            "snippet": str(item.get("content", ""))[:2500], "engine": item.get("engine", "")})
            if len(results) >= min(max(limit, 1), 20):
                break
        return results

    async def _public_target(self, url: str) -> tuple[str, str, str]:
        try:
            parsed = urlsplit(url)
            host, port = parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)
            if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password or port not in {80, 443}:
                raise ValueError("invalid public URL")
            if self.resolver:
                values = self.resolver(host, port)
                if hasattr(values, "__await__"):
                    values = await values
                ips = [str(v) for v in values]
            else:
                infos = await asyncio.to_thread(socket.getaddrinfo, host, port, type=socket.SOCK_STREAM)
                ips = list({str(info[4][0]) for info in infos})
            if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
                raise ValueError("non-public address")
            # Pin the checked address: DNS cannot resolve again between validation and connection.
            ip = ips[0]
            netloc = f"[{ip}]" if ":" in ip else ip
            if port != (443 if parsed.scheme == "https" else 80):
                netloc += f":{port}"
            target = urlunsplit((parsed.scheme, netloc, parsed.path or "/", parsed.query, ""))
            return target, parsed.netloc, host
        except (ValueError, OSError) as exc:
            raise IntegrationError("Only public HTTP(S) webpages on ports 80/443 can be fetched; local and reserved addresses are blocked.", "unsafe_url", 422) from exc

    async def fetch(self, url: str, max_characters: int = 20_000) -> dict[str, Any]:
        try:
            key = canonical_url(url)
        except ValueError as exc:
            raise IntegrationError("Use a valid public HTTP(S) URL.", "unsafe_url", 422) from exc
        cached = self.cache.get(key)
        if cached and time.monotonic() - cached[0] < 300:
            return {**cached[1], "text": cached[1]["text"][:max_characters]}
        try:
            result = await asyncio.wait_for(self._fetch(url), timeout=30)
        except (httpx.HTTPError, TimeoutError) as exc:
            raise IntegrationError("Webpage fetch failed or exceeded its 30-second limit.", "fetch_failed") from exc
        self.cache[key] = (time.monotonic(), result)
        if len(self.cache) > 64:
            self.cache.pop(next(iter(self.cache)))
        return {**result, "text": result["text"][:max_characters]}

    async def _fetch(self, original: str) -> dict[str, Any]:
        current = original
        async with httpx.AsyncClient(timeout=httpx.Timeout(15, connect=5), transport=self.transport, trust_env=False,
                                     follow_redirects=False) as client:
            for _ in range(5):
                target, host_header, host = await self._public_target(current)
                async with client.stream("GET", target, headers={"Host": host_header, "User-Agent": "PixelStation/0.1", "Accept": "text/html,text/plain"},
                                         extensions={"sni_hostname": host}) as response:
                    if response.is_redirect:
                        from urllib.parse import urljoin
                        current = urljoin(current, response.headers.get("location", ""))
                        continue
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "").split(";")[0].lower()
                    if content_type not in {"text/html", "application/xhtml+xml", "text/plain"}:
                        raise IntegrationError("This URL is not an HTML or text webpage. Attach document files through Files.", "unsupported_content", 422)
                    try:
                        declared_length = int(response.headers.get("content-length", "0"))
                    except ValueError:
                        declared_length = 0
                    if declared_length > 2_000_000:
                        raise IntegrationError("Webpage exceeds the 2 MB download limit.", "response_too_large", 422)
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 2_000_000:
                            raise IntegrationError("Webpage exceeds the 2 MB download limit.", "response_too_large", 422)
                    decoded = bytes(raw).decode(response.encoding or "utf-8", errors="replace")
                    title = ""
                    if content_type != "text/plain":
                        parser = ReadableHTML()
                        parser.feed(decoded)
                        title, decoded = parser.result()
                    if not decoded.strip():
                        raise IntegrationError("No readable text found. This page may require JavaScript; open its source in a browser.", "no_text", 422)
                    return {"url": current, "requested_url": original, "title": title, "text": decoded[:40_000], "characters": len(decoded)}
        raise IntegrationError("Webpage exceeded the redirect limit.", "redirect_limit", 422)

    async def research(self, queries: list[str], limit: int = 4, max_steps: int | None = None) -> dict[str, Any]:
        queries = list(dict.fromkeys(q.strip() for q in queries if q.strip()))
        if not 1 <= len(queries) <= 4:
            raise IntegrationError("Research requires 1–4 distinct queries.", "invalid_queries", 422)
        budget = 6 if max_steps is None else max_steps
        if not 1 <= budget <= 12:
            raise IntegrationError("Web tool budget must be from 1–12 steps.", "invalid_budget", 422)
        search_count = min(len(queries), budget)
        # Keep two focused searches for complex research when capacity permits.
        # At three or more steps, reserve one request for reading real evidence.
        if budget >= 3 and search_count == budget and search_count > 2:
            search_count -= 1
        queries = queries[:search_count]
        batches = await asyncio.gather(*(self.search(q, 6) for q in queries))
        unique = {r["url"]: r for batch in batches for r in batch}
        results = list(unique.values())[:min(max(limit, 1), 6)]
        fetch_count = min(len(results), max(0, budget - search_count))
        fetched = await asyncio.gather(*(self.fetch(r["url"], 8000) for r in results[:fetch_count]), return_exceptions=True)
        sources = []
        for index, result in enumerate(results):
            page = fetched[index] if index < len(fetched) else None
            sources.append({**result, "text": page["text"] if isinstance(page, dict) else "", "fetch_error": str(page) if isinstance(page, Exception) else None,
                            "fetched": isinstance(page, dict)})
        return {"queries": queries, "sources": sources, "tool_steps": {"search": search_count, "fetch": fetch_count, "total": search_count + fetch_count, "budget": budget}, "context": "\n\n".join(
            f"[{i}] {s['title']}\n{s['url']}\n{s['text'] or s['snippet']}" for i, s in enumerate(sources, 1))[:32_000]}
