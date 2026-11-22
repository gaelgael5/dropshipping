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


def _form(**over: str) -> dict[str, str]:
    return {"label": "Vêtements", "parent_id": "", "position": "0", "parcel_size": "",
            "photo_prompt_template": "", "description_template": "", **over}  # fmt: skip


async def test_page_lists_tree_as_indented_rows(
    client: httpx.AsyncClient, migrated_pool: asyncpg.Pool
) -> None:
    empty = await client.get("/categories")
    assert "Aucune catégorie" in empty.text and 'hx-get="/categories/new"' in empty.text
    created = await client.post("/categories", data=_form(parcel_size="moyen"))
    assert created.status_code == 204 and created.headers["hx-redirect"] == "/categories"
    root = await migrated_pool.fetchval("SELECT id FROM categories")
    await client.post("/categories", data=_form(label="Robes", parent_id=str(root)))
    page = (await client.get("/categories")).text
    assert page.index("Vêtements") < page.index("Robes")
    assert "padding-left: 1.25rem" in page  # sous-catégorie indentée
    assert "hérité" in page and "défini" in page


async def test_add_dialog_preselects_parent_and_shows_inherited(
    client: httpx.AsyncClient, migrated_pool: asyncpg.Pool
) -> None:
    await client.post("/categories", data=_form(parcel_size="moyen"))
    root = await migrated_pool.fetchval("SELECT id FROM categories")
    dialog = (await client.get(f"/categories/new?parent_id={root}")).text
    assert f'<option value="{root}" selected>' in dialog
    assert "Hérité : moyen" in dialog


async def test_add_errors_keep_dialog_open(client: httpx.AsyncClient) -> None:
    empty = await client.post("/categories", data=_form(label=" "))
    assert empty.status_code == 200 and "Libellé" in empty.text
    bad_pos = await client.post("/categories", data=_form(position="x"))
    assert "Ordre d'affichage" in bad_pos.text
    orphan = await client.post("/categories", data=_form(parent_id="98765"))
    assert "parente introuvable" in orphan.text


async def test_edit_saves_fields_and_moves(
    client: httpx.AsyncClient, migrated_pool: asyncpg.Pool
) -> None:
    a = await cats.resolve_create_category(migrated_pool, "A", None)
    b = await cats.resolve_create_category(migrated_pool, "B", None)
    dialog = (await client.get(f"/categories/{b}/edit")).text
    assert 'value="B"' in dialog and "Correspondance vers les plateformes" in dialog
    saved = await client.post(
        f"/categories/{b}", data=_form(label="B2", parent_id=str(a), description_template="Belle")
    )
    assert saved.status_code == 204
    row = await migrated_pool.fetchrow(
        "SELECT label, parent_id, description_template FROM categories WHERE id=$1", b
    )
    assert tuple(row) == ("B2", a, "Belle")


async def test_edit_cycle_refused_and_parent_list_excludes_descendants(
    client: httpx.AsyncClient, migrated_pool: asyncpg.Pool
) -> None:
    root = await cats.resolve_create_category(migrated_pool, "A", None)
    child = await cats.resolve_create_category(migrated_pool, "B", root)
    dialog = (await client.get(f"/categories/{root}/edit")).text
    assert f'<option value="{child}"' not in dialog
    forged = await client.post(f"/categories/{root}", data=_form(label="A", parent_id=str(child)))
    assert forged.status_code == 200 and "boucle" in forged.text
    assert await migrated_pool.fetchval("SELECT label FROM categories WHERE id=$1", root) == "A"


async def test_edit_invalid_field_writes_nothing(
    client: httpx.AsyncClient, migrated_pool: asyncpg.Pool
) -> None:
    a = await cats.resolve_create_category(migrated_pool, "A", None)
    b = await cats.resolve_create_category(migrated_pool, "B", None)
    refused = await client.post(
        f"/categories/{b}", data=_form(label="B", parent_id=str(a), position="x")
    )
    assert "Ordre d'affichage" in refused.text
    assert await migrated_pool.fetchval("SELECT parent_id FROM categories WHERE id=$1", b) is None


async def test_delete_in_use_shows_reason_in_dialog(
    client: httpx.AsyncClient, migrated_pool: asyncpg.Pool
) -> None:
    root = await cats.resolve_create_category(migrated_pool, "A", None)
    leaf = await cats.resolve_create_category(migrated_pool, "B", root)
    refused = await client.post(f"/categories/{root}/delete")
    assert refused.status_code == 200 and "1 sous-catégorie" in refused.text
    done = await client.post(f"/categories/{leaf}/delete")
    assert done.status_code == 204
    assert (await client.post("/categories/98765/delete")).status_code == 404


async def test_mappings_add_and_remove_in_dialog(
    client: httpx.AsyncClient, migrated_pool: asyncpg.Pool
) -> None:
    cat = await cats.resolve_create_category(migrated_pool, "Robes", None)
    added = await client.post(
        f"/categories/{cat}/mappings", data={"platform": "vinted", "platform_category_id": "12"}
    )
    assert added.status_code == 200 and "<strong>vinted</strong>" in added.text
    assert "vinted → 12" in (await client.get("/categories")).text
    bad = await client.post(
        f"/categories/{cat}/mappings", data={"platform": "a b", "platform_category_id": "1"}
    )
    assert "minuscules" in bad.text
    removed = await client.post(f"/categories/{cat}/mappings/vinted/delete")
    assert removed.status_code == 200 and "Aucune correspondance" in removed.text
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


async def test_unknown_category_edit_is_404_and_old_link_redirects(
    client: httpx.AsyncClient,
) -> None:
    assert (await client.get("/categories/31337/edit")).status_code == 404
    assert (await client.post("/categories/31337", data=_form())).status_code == 404
    old = await client.get("/categories/31337")
    assert old.status_code == 303 and old.headers["location"] == "/categories"
