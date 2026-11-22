from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import httpx
import pytest
from pydantic import SecretStr

from atelier.app import create_app
from atelier.candidates import categories as cats
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


async def test_categories_page_empty_then_create_tree(client: httpx.AsyncClient) -> None:
    empty = await client.get("/categories")
    assert "Aucune catégorie" in empty.text
    root = await client.post("/categories", data={"label": "Vêtements", "parent_id": ""})
    assert root.status_code == 303
    root_id = root.headers["location"].rsplit("/", 1)[1]
    child = await client.post("/categories", data={"label": "Robes", "parent_id": root_id})
    page = await client.get(child.headers["location"])
    assert page.status_code == 200
    assert page.text.count('<ul class="tree">') == 2  # deux niveaux affichés
    assert "Gabarit de prompt photo" in page.text


async def test_create_empty_label_shows_error(client: httpx.AsyncClient) -> None:
    response = await client.post("/categories", data={"label": " "})
    assert response.status_code == 422
    assert "Libellé" in response.text


async def test_move_creating_cycle_shows_error(
    client: httpx.AsyncClient, migrated_pool: asyncpg.Pool
) -> None:
    root = await cats.resolve_create_category(migrated_pool, "A", None)
    child = await cats.resolve_create_category(migrated_pool, "B", root)
    response = await client.post(f"/categories/{root}/move", data={"parent_id": str(child)})
    assert response.status_code == 422
    assert "boucle" in response.text
    ok = await client.post(f"/categories/{child}/move", data={"parent_id": ""})
    assert ok.status_code == 303


async def test_delete_in_use_shows_counts(
    client: httpx.AsyncClient, migrated_pool: asyncpg.Pool
) -> None:
    root = await cats.resolve_create_category(migrated_pool, "A", None)
    await cats.resolve_create_category(migrated_pool, "B", root)
    response = await client.post(f"/categories/{root}/delete")
    assert response.status_code == 422
    assert "1 sous-catégorie" in response.text
    assert (await client.post("/categories/98765/delete")).status_code == 404


async def test_mappings_add_and_remove(
    client: httpx.AsyncClient, migrated_pool: asyncpg.Pool
) -> None:
    cat = await cats.resolve_create_category(migrated_pool, "Robes", None)
    added = await client.post(
        f"/categories/{cat}/mappings", data={"platform": "vinted", "platform_category_id": "12"}
    )
    assert added.status_code == 303
    page = await client.get(f"/categories/{cat}")
    assert "vinted</strong> → 12" in page.text
    bad = await client.post(
        f"/categories/{cat}/mappings", data={"platform": "a b", "platform_category_id": "1"}
    )
    assert bad.status_code == 422
    removed = await client.post(f"/categories/{cat}/mappings/vinted/delete")
    assert removed.status_code == 303
    assert (await client.post(f"/categories/{cat}/mappings/vinted/delete")).status_code == 404


async def test_classify_from_fiche_refreshes_when_defaults_applied(
    client: httpx.AsyncClient, migrated_pool: asyncpg.Pool
) -> None:
    cat = await cats.resolve_create_category(migrated_pool, "Robes", None)
    await migrated_pool.execute("UPDATE categories SET parcel_size = 'S' WHERE id = $1", cat)
    cand = await migrated_pool.fetchval("INSERT INTO candidates DEFAULT VALUES RETURNING id")
    response = await client.post(f"/candidates/{cand}/category", data={"category_id": str(cat)})
    assert response.status_code == 204
    assert response.headers["HX-Refresh"] == "true"
    queue = await client.get("/")
    assert "Robes" in queue.text


async def test_reclassify_shows_proposal_then_apply(
    client: httpx.AsyncClient, migrated_pool: asyncpg.Pool
) -> None:
    first = await cats.resolve_create_category(migrated_pool, "Robes", None)
    second = await cats.resolve_create_category(migrated_pool, "Manteaux", None)
    await migrated_pool.execute("UPDATE categories SET parcel_size = 'L' WHERE id = $1", second)
    cand = await migrated_pool.fetchval("INSERT INTO candidates DEFAULT VALUES RETURNING id")
    await client.post(f"/candidates/{cand}/category", data={"category_id": str(first)})
    response = await client.post(f"/candidates/{cand}/category", data={"category_id": str(second)})
    assert response.status_code == 200
    assert "Taille de colis</strong> : L" in response.text
    assert await migrated_pool.fetchval("SELECT parcel_size FROM candidates") is None
    keep = await client.get(f"/candidates/{cand}/category")
    assert "Appliquer" not in keep.text
    applied = await client.post(f"/candidates/{cand}/apply-category-defaults")
    assert applied.status_code == 204
    assert await migrated_pool.fetchval("SELECT parcel_size FROM candidates") == "L"


async def test_fiche_shows_category_select_and_prep_fields(
    client: httpx.AsyncClient, migrated_pool: asyncpg.Pool
) -> None:
    await cats.resolve_create_category(migrated_pool, "Robes", None)
    cand = await migrated_pool.fetchval("INSERT INTO candidates DEFAULT VALUES RETURNING id")
    page = await client.get(f"/candidates/{cand}")
    assert "— Non classée —" in page.text
    assert "Robes</option>" in page.text
    assert "Prompt de génération de photo" in page.text


@pytest.mark.parametrize(
    "path", ["/candidates/9999/category", "/candidates/9999/apply-category-defaults"]
)
async def test_candidate_category_routes_unknown_candidate(
    client: httpx.AsyncClient, path: str
) -> None:
    assert (await client.post(path, data={"category_id": ""})).status_code == 404


async def test_unknown_category_page_is_404(client: httpx.AsyncClient) -> None:
    assert (await client.get("/categories/31337")).status_code == 404
