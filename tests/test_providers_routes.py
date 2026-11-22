from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import httpx
import pytest
from pydantic import SecretStr
from structlog.testing import capture_logs

from atelier.app import create_app
from atelier.config import Settings
from atelier.vault import service as vault

KEY = "AIza-test-Qm81Zx"


@pytest.fixture
async def app_client(
    tmp_path: Path, migrated_pool: asyncpg.Pool
) -> AsyncIterator[httpx.AsyncClient]:
    settings = Settings(
        database_url=SecretStr("postgresql://u:p@h/atelier"),
        images_root=tmp_path,
        capture_token=SecretStr("tok"),
        secrets_key=SecretStr("ab" * 32),
    )
    app = create_app(settings, migrated_pool)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


def _state(client: httpx.AsyncClient):  # type: ignore[no-untyped-def]
    return client._transport.app.state  # type: ignore[attr-defined]


async def _store_key(client: httpx.AsyncClient, pool: asyncpg.Pool) -> None:
    ia = await pool.fetchval("SELECT id FROM secret_categories WHERE slug = 'ia'")
    await vault.resolve_add_secret(pool, _state(client).secret_cipher, ia, "gemini_api_key", KEY)


async def test_page_shows_missing_then_present_key(
    app_client: httpx.AsyncClient, migrated_pool: asyncpg.Pool
) -> None:
    page = (await app_client.get("/providers")).text
    assert "ia/gemini_api_key" in page and "absente" in page
    await _store_key(app_client, migrated_pool)
    assert "présente" in (await app_client.get("/providers")).text


async def test_check_without_key_names_the_missing_ref(app_client: httpx.AsyncClient) -> None:
    reply = await app_client.post("/providers/gemini/check")
    assert reply.status_code == 200
    assert "Connexion en échec" in reply.text and "ia/gemini_api_key" in reply.text


async def test_check_with_valid_key_and_models(
    app_client: httpx.AsyncClient, migrated_pool: asyncpg.Pool
) -> None:
    await _store_key(app_client, migrated_pool)
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, json={"name": request.url.path})

    _state(app_client).provider_transport = httpx.MockTransport(handler)
    with capture_logs() as logs:
        reply = await app_client.post("/providers/gemini/check")
    assert "Connexion réussie" in reply.text
    assert paths == ["/v1beta/models/gemini-3.6-flash", "/v1beta/models/gemini-nano-banana-2.1"]
    assert all(KEY not in str(entry) for entry in logs)


async def test_check_with_refused_key_stops_after_first(
    app_client: httpx.AsyncClient, migrated_pool: asyncpg.Pool
) -> None:
    await _store_key(app_client, migrated_pool)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(400, json={"error": {"details": [{"reason": "API_KEY_INVALID"}]}})

    _state(app_client).provider_transport = httpx.MockTransport(handler)
    reply = await app_client.post("/providers/gemini/check")
    assert "authentification" in reply.text and len(calls) == 1
    assert KEY not in reply.text


async def test_update_models(app_client: httpx.AsyncClient, migrated_pool: asyncpg.Pool) -> None:
    ok = await app_client.post(
        "/providers/openai/models", data={"text_model": "gpt-x", "image_model": "img-y"}
    )
    assert ok.status_code == 303
    row = await migrated_pool.fetchrow(
        "SELECT text_model, image_model FROM ai_providers WHERE name='openai'"
    )
    assert tuple(row) == ("gpt-x", "img-y")
    bad = await app_client.post(
        "/providers/openai/models", data={"text_model": "a b", "image_model": "x"}
    )
    assert bad.status_code == 422
    assert (
        await app_client.post(
            "/providers/mistral/models", data={"text_model": "a", "image_model": "b"}
        )
    ).status_code == 404
    assert (await app_client.post("/providers/mistral/check")).status_code == 404
