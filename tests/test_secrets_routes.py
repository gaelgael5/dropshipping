from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import httpx
import pytest
from pydantic import SecretStr

from atelier.app import create_app
from atelier.config import Settings
from atelier.vault import service as vault

VALUE = "sk-proj-Qw7Ertz9"


@pytest.fixture
async def client(tmp_path: Path, migrated_pool: asyncpg.Pool) -> AsyncIterator[httpx.AsyncClient]:
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


async def test_added_secret_is_listed_but_value_never_shown(
    client: httpx.AsyncClient, migrated_pool: asyncpg.Pool
) -> None:
    ia = await migrated_pool.fetchval("SELECT id FROM secret_categories WHERE slug='ia'")
    added = await client.post(
        "/secrets", data={"category_id": ia, "name": "openai_api_key", "value": VALUE}
    )
    assert added.status_code == 303
    page = await client.get("/secrets")
    assert "ia/openai_api_key" in page.text
    assert VALUE not in page.text
    assert page.headers["cache-control"] == "no-store"
    sid = await migrated_pool.fetchval("SELECT id FROM secrets")
    replaced = await client.post(f"/secrets/{sid}/value", data={"value": "v2"})
    assert replaced.status_code == 303
    state = client._transport.app.state  # type: ignore[attr-defined]
    got = await vault.resolve_secret_value(migrated_pool, state.secret_cipher, "ia/openai_api_key")
    assert got.get_secret_value() == "v2"


async def test_errors_are_readable_and_value_not_echoed(
    client: httpx.AsyncClient, migrated_pool: asyncpg.Pool
) -> None:
    ia = await migrated_pool.fetchval("SELECT id FROM secret_categories WHERE slug='ia'")
    bad = await client.post("/secrets", data={"category_id": ia, "name": "Bad", "value": VALUE})
    assert bad.status_code == 422
    assert "Nom du secret invalide" in bad.text
    assert VALUE not in bad.text
    assert (await client.post("/secrets/999/delete")).status_code == 404
    assert (await client.post("/secrets/999/value", data={"value": "x"})).status_code == 404


async def test_category_routes(client: httpx.AsyncClient, migrated_pool: asyncpg.Pool) -> None:
    created = await client.post("/secrets/categories", data={"slug": "transport", "label": "T"})
    assert created.status_code == 303
    cid = await migrated_pool.fetchval("SELECT id FROM secret_categories WHERE slug='transport'")
    renamed = await client.post(f"/secrets/categories/{cid}/label", data={"label": "Livreurs"})
    assert renamed.status_code == 303
    assert "Livreurs" in (await client.get("/secrets")).text
    await client.post("/secrets", data={"category_id": cid, "name": "k", "value": "v"})
    refused = await client.post(f"/secrets/categories/{cid}/delete")
    assert refused.status_code == 422
    assert "1 secret(s)" in refused.text
    assert (await client.post("/secrets/categories/999/delete")).status_code == 404
