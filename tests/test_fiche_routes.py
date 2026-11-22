from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import httpx
import pytest
from pydantic import SecretStr

from atelier.app import create_app
from atelier.candidates.capture import resolve_capture_url
from atelier.config import Settings


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


async def _capture(pool: asyncpg.Pool, url: str = "https://temu.com/p/1.html") -> int:
    return (await resolve_capture_url(pool, url)).candidate_id


async def test_fiche_page_shows_fields_links_and_photos(
    client: httpx.AsyncClient, migrated_pool: asyncpg.Pool
) -> None:
    cid = await _capture(migrated_pool)
    page = await client.get(f"/candidates/{cid}")
    assert page.status_code == 200
    for label in ("Titre", "Prix de vente", "Marque", "Prix d&#39;achat", "Tailles disponibles"):
        assert label in page.text
    assert f'hx-post="/candidates/{cid}/fields/title"' in page.text
    assert "Temu" in page.text
    assert 'id="candidate-photos"' in page.text
    # Pas de formulaire unique : chaque champ s'enregistre seul.
    assert "<form" not in page.text.split('id="candidate-links"')[0]


async def test_save_field_returns_stored_value(
    client: httpx.AsyncClient, migrated_pool: asyncpg.Pool
) -> None:
    cid = await _capture(migrated_pool)
    response = await client.post(f"/candidates/{cid}/fields/sale_price", data={"value": "12,5"})
    assert response.status_code == 200
    assert 'value="12,50"' in response.text
    assert "Enregistré" in response.text


async def test_save_field_refused_keeps_input_and_message(
    client: httpx.AsyncClient, migrated_pool: asyncpg.Pool
) -> None:
    cid = await _capture(migrated_pool)
    response = await client.post(f"/candidates/{cid}/fields/sale_price", data={"value": "-4"})
    assert response.status_code == 422
    assert 'value="-4"' in response.text
    assert "négatif" in response.text


@pytest.mark.parametrize("path", ["/candidates/1/fields/id", "/users/1/fields/title"])
async def test_save_field_outside_whitelist_is_404(client: httpx.AsyncClient, path: str) -> None:
    assert (await client.post(path, data={"value": "x"})).status_code == 404


async def test_save_field_unknown_row_is_404(client: httpx.AsyncClient) -> None:
    response = await client.post("/candidates/424242/fields/title", data={"value": "x"})
    assert response.status_code == 404


async def test_link_field_save_and_tristate_select(
    client: httpx.AsyncClient, migrated_pool: asyncpg.Pool
) -> None:
    await _capture(migrated_pool)
    link_id = await migrated_pool.fetchval("SELECT id FROM purchase_links")
    response = await client.post(
        f"/links/{link_id}/fields/ships_from_europe", data={"value": "oui"}
    )
    assert '<option value="oui" selected>' in response.text


async def test_add_duplicate_link_shows_owner(
    client: httpx.AsyncClient, migrated_pool: asyncpg.Pool
) -> None:
    owner = await _capture(migrated_pool, "https://shein.com/a-p-1.html")
    other = await _capture(migrated_pool)
    response = await client.post(
        f"/candidates/{other}/links", data={"url": "https://shein.com/a-p-1.html"}
    )
    assert response.status_code == 422
    assert f"n° {owner}" in response.text


async def test_add_then_remove_links(
    client: httpx.AsyncClient, migrated_pool: asyncpg.Pool
) -> None:
    cid = await _capture(migrated_pool)
    added = await client.post(
        f"/candidates/{cid}/links", data={"url": "https://boutique.example/p"}
    )
    assert added.status_code == 200
    assert added.text.count('class="link-card"') == 2
    for link_id in [r["id"] for r in await migrated_pool.fetch("SELECT id FROM purchase_links")]:
        removed = await client.post(f"/links/{link_id}/delete")
    assert "Aucun lien d'achat" in removed.text
    assert (await client.post("/links/1/delete")).status_code == 404


async def test_add_link_unknown_candidate_is_404(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/candidates/5555/links", data={"url": "https://boutique.example/z"}
    )
    assert response.status_code == 404


async def test_delete_candidate_redirects_home(
    client: httpx.AsyncClient, migrated_pool: asyncpg.Pool
) -> None:
    cid = await _capture(migrated_pool)
    response = await client.post(f"/candidates/{cid}/delete")
    assert response.status_code == 204
    assert response.headers["HX-Redirect"] == "/"
    assert (await client.get(f"/candidates/{cid}")).status_code == 404
    assert (await client.post(f"/candidates/{cid}/delete")).status_code == 404
