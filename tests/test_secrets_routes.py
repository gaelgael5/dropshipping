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


async def _cat(pool: asyncpg.Pool, slug: str = "ia") -> int:
    return int(await pool.fetchval("SELECT id FROM secret_categories WHERE slug = $1", slug))


async def _value(client: httpx.AsyncClient, pool: asyncpg.Pool, ref: str) -> str:
    cipher = client._transport.app.state.secret_cipher  # type: ignore[attr-defined]
    return (await vault.resolve_secret_value(pool, cipher, ref)).get_secret_value()


async def test_list_shows_rows_with_add_button_and_masked_value(
    client: httpx.AsyncClient, migrated_pool: asyncpg.Pool
) -> None:
    empty = await client.get("/secrets")
    assert "Aucun secret" in empty.text and 'hx-get="/secrets/new"' in empty.text
    added = await client.post(
        "/secrets", data={"category_id": await _cat(migrated_pool), "name": "openai_api_key",
                          "value": VALUE, "description": "Clef OpenAI"},
    )  # fmt: skip
    assert added.status_code == 204 and added.headers["hx-redirect"] == "/secrets"
    page = await client.get("/secrets")
    assert "ia/openai_api_key" in page.text and "Clef OpenAI" in page.text
    assert VALUE not in page.text and page.headers["cache-control"] == "no-store"


async def test_add_form_offers_categories_in_select(client: httpx.AsyncClient) -> None:
    form = await client.get("/secrets/new")
    assert '<select id="f-category" name="category_id"' in form.text
    assert "Fournisseurs d&#39;IA" in form.text and "Plateformes de vente" in form.text


async def test_add_error_keeps_form_open_without_echoing_value(
    client: httpx.AsyncClient, migrated_pool: asyncpg.Pool
) -> None:
    bad = await client.post(
        "/secrets", data={"category_id": await _cat(migrated_pool), "name": "Bad", "value": VALUE}
    )
    assert bad.status_code == 200 and "Nom du secret invalide" in bad.text
    assert 'value="Bad"' in bad.text and VALUE not in bad.text
    unknown = await client.post("/secrets", data={"category_id": 999, "name": "k", "value": "v"})
    assert "Catégorie inconnue" in unknown.text


async def test_edit_form_prefills_all_but_value_and_update_changes_secret(
    client: httpx.AsyncClient, migrated_pool: asyncpg.Pool
) -> None:
    ia = await _cat(migrated_pool)
    await client.post("/secrets", data={"category_id": ia, "name": "k", "value": VALUE})
    sid = await migrated_pool.fetchval("SELECT id FROM secrets")
    form = await client.get(f"/secrets/{sid}/edit")
    assert 'value="k"' in form.text and "Laisser vide pour conserver" in form.text
    assert VALUE not in form.text
    kept = await client.post(f"/secrets/{sid}", data={"category_id": ia, "name": "k", "value": ""})
    assert kept.status_code == 204 and await _value(client, migrated_pool, "ia/k") == VALUE
    changed = await client.post(
        f"/secrets/{sid}", data={"category_id": await _cat(migrated_pool, "divers"),
                                 "name": "k", "value": "v2"},
    )  # fmt: skip
    assert changed.status_code == 204
    assert await _value(client, migrated_pool, "divers/k") == "v2"


async def test_edit_errors_and_not_found(
    client: httpx.AsyncClient, migrated_pool: asyncpg.Pool
) -> None:
    ia = await _cat(migrated_pool)
    await client.post("/secrets", data={"category_id": ia, "name": "k", "value": "v"})
    sid = await migrated_pool.fetchval("SELECT id FROM secrets")
    bad = await client.post(f"/secrets/{sid}", data={"category_id": 999, "name": "k"})
    assert "Catégorie inconnue" in bad.text
    assert (await client.get("/secrets/999/edit")).status_code == 404
    assert (
        await client.post("/secrets/999", data={"category_id": ia, "name": "k"})
    ).status_code == 404
    assert (await client.post("/secrets/999/delete")).status_code == 404
    deleted = await client.post(f"/secrets/{sid}/delete")
    assert deleted.status_code == 204
    assert await migrated_pool.fetchval("SELECT count(*) FROM secrets") == 0


async def test_category_dialog_and_routes(
    client: httpx.AsyncClient, migrated_pool: asyncpg.Pool
) -> None:
    created = await client.post("/secrets/categories", data={"slug": "transport", "label": "T"})
    assert created.status_code == 303
    cid = await _cat(migrated_pool, "transport")
    renamed = await client.post(f"/secrets/categories/{cid}/label", data={"label": "Livreurs"})
    assert renamed.status_code == 303
    dialog = await client.get("/secrets/categories")
    assert "Livreurs" in dialog.text and "transport" in dialog.text
    await client.post("/secrets", data={"category_id": cid, "name": "k", "value": "v"})
    refused = await client.post(f"/secrets/categories/{cid}/delete")
    assert refused.status_code == 422 and "1 secret(s)" in refused.text
    assert (await client.post("/secrets/categories/999/delete")).status_code == 404
