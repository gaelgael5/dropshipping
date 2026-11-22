from __future__ import annotations

import base64
import hashlib
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import asyncpg
import httpx
import pytest
from pydantic import SecretStr
from structlog.testing import capture_logs

from atelier.app import create_app
from atelier.config import Settings

REDIRECT = "https://claude.ai/api/mcp/auth_callback"
VERIFIER = "v" * 20 + "erifier-0123456789-abcdefghijk"  # 50 caractères admis par RFC 7636
CHALLENGE = (
    base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b"=").decode()
)
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}}}  # fmt: skip


@pytest.fixture
async def client(tmp_path: Path, migrated_pool: asyncpg.Pool) -> AsyncIterator[httpx.AsyncClient]:
    settings = Settings(
        database_url=SecretStr("postgresql://u:p@h/atelier"),
        images_root=tmp_path,
        capture_token=SecretStr("tok"),
        secrets_key=SecretStr("ab" * 32),
        public_url="https://atelier.example",
    )
    app = create_app(settings, migrated_pool)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


async def _register(client: httpx.AsyncClient) -> str:
    reply = await client.post(
        "/oauth/register", json={"client_name": "Claude", "redirect_uris": [REDIRECT]}
    )
    assert reply.status_code == 201
    return str(reply.json()["client_id"])


async def _pairing(client: httpx.AsyncClient) -> str:
    page = await client.post("/connectors/pairing-code")
    match = re.search(r'class="code-big">([A-Z0-9]{4}-[A-Z0-9]{4})<', page.text)
    assert match
    return match.group(1)


def _authorize_form(client_id: str, **extra: str) -> dict[str, str]:
    return {"response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT,
            "code_challenge": CHALLENGE, "code_challenge_method": "S256", "state": "xyz",
            **extra}  # fmt: skip


async def _code(client: httpx.AsyncClient, client_id: str) -> str:
    pairing = await _pairing(client)
    reply = await client.post(
        "/oauth/authorize", data=_authorize_form(client_id, pairing=pairing, decision="approve")
    )
    assert reply.status_code == 302
    query = parse_qs(urlsplit(reply.headers["location"]).query)
    assert query["state"] == ["xyz"]
    return query["code"][0]


async def _token(
    client: httpx.AsyncClient, client_id: str, code: str, verifier: str = VERIFIER
) -> httpx.Response:
    return await client.post("/oauth/token", data={
        "grant_type": "authorization_code", "code": code, "client_id": client_id,
        "redirect_uri": REDIRECT, "code_verifier": verifier,
    })  # fmt: skip


async def _mcp(client: httpx.AsyncClient, headers: dict[str, str], body: Any) -> httpx.Response:
    return await client.post("/mcp", json=body, headers=headers)


async def test_discovery_points_to_public_url(client: httpx.AsyncClient) -> None:
    unauth = await client.post("/mcp", json=INIT)
    assert unauth.status_code == 401
    assert (
        "https://atelier.example/.well-known/oauth-protected-resource"
        in unauth.headers["www-authenticate"]
    )
    res = (await client.get("/.well-known/oauth-protected-resource/mcp")).json()
    assert res["resource"] == "https://atelier.example/mcp"
    meta = (await client.get("/.well-known/oauth-authorization-server")).json()
    assert meta["token_endpoint"] == "https://atelier.example/oauth/token"
    assert meta["code_challenge_methods_supported"] == ["S256"]


async def test_full_oauth_flow_then_mcp_call_then_refresh(client: httpx.AsyncClient) -> None:
    client_id = await _register(client)
    consent = await client.get("/oauth/authorize", params=_authorize_form(client_id))
    assert consent.status_code == 200 and "Claude" in consent.text
    assert consent.headers["x-frame-options"] == "DENY"
    tokens = (await _token(client, client_id, await _code(client, client_id))).json()
    bearer = {"Authorization": f"Bearer {tokens['access_token']}"}
    init = (await _mcp(client, bearer, INIT)).json()
    assert init["result"]["protocolVersion"] == "2025-06-18"
    listed = (
        await _mcp(client, bearer, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    ).json()
    assert {t["name"] for t in listed["result"]["tools"]} == {
        "list_candidates",
        "get_candidate",
        "validate_candidate",
    }
    refresh = {"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]}
    refresh["client_id"] = client_id
    refreshed = await client.post("/oauth/token", data=refresh)
    assert refreshed.status_code == 200
    replay = await client.post("/oauth/token", data=refresh)
    assert replay.status_code == 400  # rotation : l'ancien jeton est brûlé


async def test_code_is_single_use_and_bad_pkce_burns_it(client: httpx.AsyncClient) -> None:
    client_id = await _register(client)
    code = await _code(client, client_id)
    bad = await _token(client, client_id, code, verifier="w" * 50)
    assert bad.status_code == 400 and "PKCE" in bad.json()["error_description"]
    assert (await _token(client, client_id, code)).status_code == 400  # brûlé par l'échec


async def test_wrong_pairing_refused_and_locks_after_five_failures(
    client: httpx.AsyncClient,
) -> None:
    client_id = await _register(client)
    good = await _pairing(client)
    for _ in range(5):
        reply = await client.post(
            "/oauth/authorize",
            data=_authorize_form(client_id, pairing="ZZZZ-ZZZZ", decision="approve"),
        )
        assert reply.status_code == 400 and "incorrect" in reply.text
    locked = await client.post(
        "/oauth/authorize", data=_authorize_form(client_id, pairing=good, decision="approve")
    )
    assert locked.status_code == 400  # le bon code a été annulé par le verrou


async def test_expired_or_reused_pairing_refused(
    client: httpx.AsyncClient, migrated_pool: asyncpg.Pool
) -> None:
    client_id = await _register(client)
    await _code(client, client_id)
    await migrated_pool.execute("UPDATE oauth_pairing_failures SET count = 0")
    pairing = await _pairing(client)
    await migrated_pool.execute(
        "UPDATE oauth_pairing_codes SET expires_at = now() - interval '1 s'"
    )
    reply = await client.post(
        "/oauth/authorize", data=_authorize_form(client_id, pairing=pairing, decision="approve")
    )
    assert reply.status_code == 400


@pytest.mark.parametrize(("field", "value"), [
    ("redirect_uri", "https://evil.example/cb"), ("code_challenge_method", "plain"),
    ("client_id", "inconnu"), ("response_type", "token"),
])  # fmt: skip
async def test_bad_authorize_request_shown_never_redirected(
    client: httpx.AsyncClient, field: str, value: str
) -> None:
    client_id = await _register(client)
    reply = await client.get(
        "/oauth/authorize", params={**_authorize_form(client_id), field: value}
    )
    assert reply.status_code == 400 and "location" not in reply.headers


async def test_deny_redirects_with_access_denied(client: httpx.AsyncClient) -> None:
    client_id = await _register(client)
    reply = await client.post("/oauth/authorize", data=_authorize_form(client_id, decision="deny"))
    assert reply.status_code == 302 and "error=access_denied" in reply.headers["location"]


async def test_register_refuses_http_redirect(client: httpx.AsyncClient) -> None:
    reply = await client.post("/oauth/register", json={"redirect_uris": ["http://evil.example/cb"]})
    assert reply.status_code == 400 and reply.json()["error"] == "invalid_redirect_uri"
    assert (await client.post("/oauth/register", content=b"[]")).status_code == 400


async def test_revoked_client_tokens_refused_immediately(client: httpx.AsyncClient) -> None:
    client_id = await _register(client)
    tokens = (await _token(client, client_id, await _code(client, client_id))).json()
    bearer = {"Authorization": f"Bearer {tokens['access_token']}"}
    assert (await _mcp(client, bearer, INIT)).status_code == 200
    assert (await client.post(f"/connectors/clients/{client_id}/revoke")).status_code == 303
    assert (await _mcp(client, bearer, INIT)).status_code == 401


async def test_api_key_shown_once_hashed_and_revocable(
    client: httpx.AsyncClient, migrated_pool: asyncpg.Pool
) -> None:
    with capture_logs() as logs:
        page = await client.post("/connectors/api-keys", data={"name": "Agent rédaction"})
        match = re.search(r'class="code-big">(atl_key_[A-Za-z0-9_-]+)<', page.text)
        assert match
        key = match.group(1)
        assert key not in (await client.get("/connectors")).text
        stored = await migrated_pool.fetchval("SELECT key_hash FROM mcp_api_keys")
        assert key.encode() not in bytes(stored)
        via_header = await _mcp(client, {"X-Api-Key": key}, INIT)
        assert via_header.status_code == 200
    assert all(key not in str(entry) for entry in logs)
    key_id = await migrated_pool.fetchval("SELECT id FROM mcp_api_keys")
    assert (await client.post(f"/connectors/api-keys/{key_id}/revoke")).status_code == 303
    assert (await _mcp(client, {"Authorization": f"Bearer {key}"}, INIT)).status_code == 401
    assert (await client.post("/connectors/api-keys/9999/revoke")).status_code == 404
    assert (await client.post("/connectors/api-keys", data={"name": " "})).status_code == 422


async def _api_key(client: httpx.AsyncClient) -> dict[str, str]:
    page = await client.post("/connectors/api-keys", data={"name": "test"})
    match = re.search(r'class="code-big">(atl_key_[A-Za-z0-9_-]+)<', page.text)
    assert match
    return {"X-Api-Key": match.group(1)}


def _call(name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 9,
        "method": "tools/call",
        "params": {"name": name, "arguments": args},
    }


async def test_tools_read_and_validate(
    client: httpx.AsyncClient, migrated_pool: asyncpg.Pool
) -> None:
    headers = await _api_key(client)
    cid = await migrated_pool.fetchval(
        "INSERT INTO candidates (title) VALUES ('Robe') RETURNING id"
    )
    listed = (await _mcp(client, headers, _call("list_candidates", {}))).json()["result"]
    assert listed["structuredContent"]["candidates"][0]["title"] == "Robe"
    got = (await _mcp(client, headers, _call("get_candidate", {"candidate_id": cid}))).json()[
        "result"
    ]
    assert got["isError"] is False and got["structuredContent"]["candidate"]["title"] == "Robe"
    report = (
        await _mcp(client, headers, _call("validate_candidate", {"candidate_id": cid}))
    ).json()
    api = (await client.get(f"/api/candidates/{cid}/validation")).json()
    assert report["result"]["structuredContent"] == api


@pytest.mark.parametrize(("name", "args", "expected"), [
    ("get_candidate", {"candidate_id": 4040}, "introuvable"),
    ("get_candidate", {"candidate_id": 1, "title": "x"}, "inconnu(s) : title"),
    ("get_candidate", {"candidate_id": True}, "entier"),
    ("list_candidates", {"limit": 1000}, "entre 1 et 100"),
    ("delete_candidate", {}, "outil inconnu"),
])  # fmt: skip
async def test_tool_errors_are_readable_results(
    client: httpx.AsyncClient, name: str, args: dict[str, Any], expected: str
) -> None:
    reply = (await _mcp(client, await _api_key(client), _call(name, args))).json()["result"]
    assert reply["isError"] is True and expected in reply["content"][0]["text"]


async def test_protocol_edges(client: httpx.AsyncClient) -> None:
    headers = await _api_key(client)
    note = await _mcp(client, headers, {"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert note.status_code == 202
    unknown = (await _mcp(client, headers, {"jsonrpc": "2.0", "id": 3, "method": "x/y"})).json()
    assert unknown["error"]["code"] == -32601
    assert (await _mcp(client, headers, [INIT])).status_code == 400
    bad_json = await client.post("/mcp", content=b"{", headers=headers)
    assert bad_json.status_code == 400
    assert (await client.get("/mcp")).status_code == 405
    evil = await client.post(
        "/mcp", json=INIT, headers={**headers, "Origin": "https://evil.example"}
    )
    assert evil.status_code == 403
    ok = await client.post("/mcp", json=INIT, headers={**headers, "Origin": "https://claude.ai"})
    assert ok.status_code == 200
