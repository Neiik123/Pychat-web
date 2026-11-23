"""PyChat Web API.

The existing PyChat browser application is served unchanged apart from its
separate authentication bootstrap. Cloud state is isolated by Supabase user ID.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import threading
import time
from collections import defaultdict, deque
from typing import Any
from urllib.parse import urlparse

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
APP_ENCRYPTION_KEY = os.environ.get("APP_ENCRYPTION_KEY", "")
MAX_ACCOUNT_BYTES = 15 * 1024 * 1024

app = FastAPI(title="PyChat Web API", version="0.2.0")
_origins = [x.strip() for x in os.environ.get("ALLOWED_ORIGINS", "").split(",") if x.strip()]
if _origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )


def auth_config_ready() -> None:
    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        raise HTTPException(503, "Authentication is not configured on this server.")


def storage_config_ready() -> None:
    auth_config_ready()
    if not SUPABASE_SERVICE_ROLE_KEY:
        raise HTTPException(503, "Cloud storage is not configured on this server.")
    if not APP_ENCRYPTION_KEY:
        raise HTTPException(503, "API-key encryption is not configured on this server.")
    cipher()  # Validate the key before accepting account data.


def cipher() -> Fernet:
    try:
        return Fernet(APP_ENCRYPTION_KEY.encode("ascii"))
    except Exception as exc:
        raise HTTPException(503, "APP_ENCRYPTION_KEY must be a valid Fernet key.") from exc


async def current_user(authorization: str | None) -> dict[str, Any]:
    auth_config_ready()
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Sign in is required.")
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(401, "Sign in is required.")
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            response = await client.get(
                f"{SUPABASE_URL}/auth/v1/user",
                headers={"apikey": SUPABASE_ANON_KEY, "Authorization": f"Bearer {token}"},
            )
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Could not verify your sign-in session.") from exc
    if response.status_code != 200:
        raise HTTPException(401, "Your session is invalid or expired.")
    user = response.json()
    if not isinstance(user, dict) or not user.get("id"):
        raise HTTPException(401, "Supabase returned an invalid user session.")
    return user


def transform_secrets(value: Any, encrypt: bool) -> Any:
    """Encrypt config values stored under fields named `key`; decrypt on read."""
    f = cipher()
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if key.lower() == "key":
                if encrypt and isinstance(item, str) and item:
                    out[key] = {"__pychat_secret__": f.encrypt(item.encode("utf-8")).decode("ascii")}
                elif not encrypt and isinstance(item, dict) and isinstance(item.get("__pychat_secret__"), str):
                    try:
                        out[key] = f.decrypt(item["__pychat_secret__"].encode("ascii")).decode("utf-8")
                    except (InvalidToken, ValueError, UnicodeDecodeError):
                        out[key] = ""
                else:
                    out[key] = item
            else:
                out[key] = transform_secrets(item, encrypt)
        return out
    if isinstance(value, list):
        return [transform_secrets(item, encrypt) for item in value]
    return value


async def db_request(method: str, user_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    storage_config_ready()
    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }
    params = {"user_id": f"eq.{user_id}", "select": "data"}
    async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
        if method == "GET":
            response = await client.get(f"{SUPABASE_URL}/rest/v1/user_data", headers=headers, params=params)
            if response.status_code >= 400:
                raise HTTPException(502, "Could not load account data.")
            rows = response.json()
            if not rows:
                return {}
            return transform_secrets(rows[0]["data"], False)

        safe_data = transform_secrets(payload or {}, True)
        response = await client.post(
            f"{SUPABASE_URL}/rest/v1/user_data?on_conflict=user_id",
            headers={**headers, "Prefer": "resolution=merge-duplicates,return=minimal"},
            json={"user_id": user_id, "data": safe_data},
        )
        if response.status_code not in (200, 201, 204):
            raise HTTPException(502, "Could not save account data.")
        return {"ok": True}


# A small per-process guardrail. Production deployments should also configure
# provider-level limits because multiple workers/instances have separate memory.
_rate_lock = threading.Lock()
_rate_events: dict[tuple[str, str], deque[float]] = defaultdict(deque)


def enforce_rate_limit(user_id: str, bucket: str, limit: int, window: int = 60) -> None:
    now = time.monotonic()
    key = (user_id, bucket)
    with _rate_lock:
        events = _rate_events[key]
        while events and now - events[0] >= window:
            events.popleft()
        if len(events) >= limit:
            raise HTTPException(429, "Too many requests. Please wait a moment and try again.")
        events.append(now)


@app.get("/api/info")
async def info() -> dict[str, Any]:
    return {"v": 7, "mode": "web", "app": "PyChat Web"}


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "app": "PyChat Web", "version": app.version}


@app.get("/api/config")
async def public_config() -> dict[str, str]:
    auth_config_ready()
    # Supabase anon/publishable keys are designed to be public. Never return
    # the service-role key or the Fernet encryption key here.
    return {"supabaseUrl": SUPABASE_URL, "supabaseAnonKey": SUPABASE_ANON_KEY}


@app.get("/api/db")
async def get_db(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    user = await current_user(authorization)
    enforce_rate_limit(user["id"], "db-read", 120)
    return await db_request("GET", user["id"])


@app.put("/api/db")
async def put_db(request: Request, authorization: str | None = Header(default=None)) -> dict[str, bool]:
    user = await current_user(authorization)
    enforce_rate_limit(user["id"], "db-write", 120)
    raw = await request.body()
    if len(raw) > MAX_ACCOUNT_BYTES:
        raise HTTPException(413, "Account data is too large (15 MB limit).")
    try:
        data = json.loads(raw or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(400, "Invalid JSON data.") from exc
    if not isinstance(data, dict):
        raise HTTPException(400, "Expected a JSON object.")
    return await db_request("PUT", user["id"], data)


def assert_public_https(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise HTTPException(400, "Web mode requires a public HTTPS API endpoint. For local Ollama, use the optional local connector.")
    host = parsed.hostname.lower().rstrip(".")
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
        raise HTTPException(400, "Localhost endpoints are not reachable from the cloud server.")
    try:
        addresses = socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise HTTPException(400, "Could not resolve API hostname.") from exc
    if not addresses:
        raise HTTPException(400, "Could not resolve API hostname.")
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0].split("%", 1)[0])
        if not ip.is_global:
            raise HTTPException(400, "Private or local network endpoints are blocked.")


@app.post("/api/proxy")
async def proxy(request: Request, authorization: str | None = Header(default=None)) -> StreamingResponse:
    user = await current_user(authorization)
    enforce_rate_limit(user["id"], "proxy", 90)
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(400, "Invalid request body.") from exc
    if not isinstance(payload, dict):
        raise HTTPException(400, "Invalid request body.")
    url = str(payload.get("url", ""))
    assert_public_https(url)
    method = str(payload.get("method", "POST")).upper()
    if method not in {"GET", "POST", "PUT", "DELETE"}:
        raise HTTPException(405, "Unsupported upstream method.")
    headers = {
        str(key): str(value)
        for key, value in (payload.get("headers") or {}).items()
        if str(key).lower() not in {"host", "connection", "content-length", "transfer-encoding"}
    }
    body = payload.get("body")
    content = None if body is None else json.dumps(body).encode("utf-8")
    if content is not None and len(content) > 10 * 1024 * 1024:
        raise HTTPException(413, "Upstream request body is too large.")

    client = httpx.AsyncClient(timeout=httpx.Timeout(900, connect=20), follow_redirects=False)
    try:
        outgoing = client.build_request(method, url, headers=headers, content=content)
        upstream = await client.send(outgoing, stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        raise HTTPException(502, f"Could not reach the configured API endpoint: {str(exc)[:240]}") from exc

    async def relay():
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    response_headers = {}
    for name in ("cache-control", "x-request-id", "retry-after"):
        if name in upstream.headers:
            response_headers[name] = upstream.headers[name]
    return StreamingResponse(
        relay(),
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/octet-stream"),
        headers=response_headers,
    )


@app.post("/api/models")
async def models(request: Request, authorization: str | None = Header(default=None)) -> JSONResponse:
    user = await current_user(authorization)
    enforce_rate_limit(user["id"], "models", 30)
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(400, "Invalid request body.") from exc
    base = str(payload.get("url", "")).rstrip("/") if isinstance(payload, dict) else ""
    assert_public_https(base)
    async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
        try:
            response = await client.get(base + "/api/tags")
            response.raise_for_status()
            data = response.json()
            return JSONResponse({"models": [model["name"] for model in data.get("models", []) if isinstance(model, dict) and "name" in model], "used": base})
        except Exception as exc:
            return JSONResponse({"error": f"{base}: {str(exc)[:220]} - is the remote Ollama instance running?"})


@app.post("/api/search")
async def search(request: Request, authorization: str | None = Header(default=None)) -> dict[str, str]:
    user = await current_user(authorization)
    enforce_rate_limit(user["id"], "search", 20)
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(400, "Invalid request body.") from exc
    query = str(payload.get("q", "")).strip()[:500] if isinstance(payload, dict) else ""
    if not query:
        return {"text": "No search query."}
    from urllib.parse import quote
    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=False) as client:
        try:
            response = await client.get("https://html.duckduckgo.com/html/?q=" + quote(query))
            response.raise_for_status()
            html = response.text
            clean = lambda value: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", value)).strip()
            titles = [clean(value) for value in re.findall(r'class="result__a"[^>]*>(.*?)</a>', html, re.S)]
            snippets = [clean(value) for value in re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', html, re.S)]
            return {"text": "\n".join(f"- {title}: {snippet}" for title, snippet in zip(titles, snippets))[:3000] or "No results."}
        except Exception as exc:
            return {"text": f"Search failed: {str(exc)[:220]}"}


# Mount last so the explicit /api routes above take precedence.
app.mount("/", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "..", "web"), html=True), name="web")
