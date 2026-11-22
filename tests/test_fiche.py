from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest

from atelier.candidates.capture import resolve_capture_url
from atelier.candidates.fiche import (
    LinkAlreadyAttachedError,
    NotFoundError,
    db_get_fiche,
    resolve_add_link,
    resolve_delete_candidate,
    resolve_remove_link,
    resolve_update_field,
)
from atelier.candidates.fields import FieldValueError
from atelier.candidates.url_normalizer import InvalidUrlError
from atelier.images.photos import resolve_add_candidate_photo
from atelier.images.store import ImageStore
from tests.images_helpers import make_image_bytes


async def _capture(pool: asyncpg.Pool, url: str = "https://temu.com/p/1.html") -> int:
    return (await resolve_capture_url(pool, url)).candidate_id


async def test_update_field_stores_only_that_field(migrated_pool: asyncpg.Pool) -> None:
    cid = await _capture(migrated_pool)
    await resolve_update_field(migrated_pool, "candidates", cid, "brand", "Zara")
    stored = await resolve_update_field(migrated_pool, "candidates", cid, "sale_price", "24,9")
    assert stored == Decimal("24.90")
    row = await migrated_pool.fetchrow("SELECT * FROM candidates WHERE id = $1", cid)
    assert (row["brand"], row["sale_price"], row["title"]) == ("Zara", Decimal("24.90"), None)


async def test_update_field_empty_clears_without_inventing(migrated_pool: asyncpg.Pool) -> None:
    cid = await _capture(migrated_pool)
    await resolve_update_field(migrated_pool, "candidates", cid, "title", "Robe")
    assert await resolve_update_field(migrated_pool, "candidates", cid, "title", " ") is None


async def test_update_field_negative_price_refused_and_nothing_written(
    migrated_pool: asyncpg.Pool,
) -> None:
    cid = await _capture(migrated_pool)
    with pytest.raises(FieldValueError):
        await resolve_update_field(migrated_pool, "candidates", cid, "sale_price", "-3")
    assert await migrated_pool.fetchval("SELECT sale_price FROM candidates") is None


async def test_update_field_unknown_attribute_or_row(migrated_pool: asyncpg.Pool) -> None:
    cid = await _capture(migrated_pool)
    with pytest.raises(KeyError):
        await resolve_update_field(migrated_pool, "candidates", cid, "id", "1")
    with pytest.raises(NotFoundError):
        await resolve_update_field(migrated_pool, "candidates", 999_999, "title", "x")


async def test_concurrent_updates_store_one_whole_value(migrated_pool: asyncpg.Pool) -> None:
    cid = await _capture(migrated_pool)
    values = ["Première description complète", "Seconde description, bien différente"]
    results = await asyncio.gather(
        *(resolve_update_field(migrated_pool, "candidates", cid, "description", v) for v in values)
    )
    final = await migrated_pool.fetchval("SELECT description FROM candidates")
    assert final in values
    assert set(results) == set(values)  # chaque appel rend ce que la base a retenu pour lui


async def test_link_fields_sizes_tristate_currency(migrated_pool: asyncpg.Pool) -> None:
    cid = await _capture(migrated_pool)
    link_id = await migrated_pool.fetchval(
        "SELECT id FROM purchase_links WHERE candidate_id=$1", cid
    )
    assert await resolve_update_field(
        migrated_pool, "purchase_links", link_id, "available_sizes", "S, M"
    ) == ["S", "M"]
    assert (
        await resolve_update_field(migrated_pool, "purchase_links", link_id, "available_sizes", "")
        == []
    )
    assert (
        await resolve_update_field(
            migrated_pool, "purchase_links", link_id, "ships_from_europe", "oui"
        )
        is True
    )
    assert (
        await resolve_update_field(migrated_pool, "purchase_links", link_id, "currency", "eur")
        == "EUR"
    )


async def test_add_link_to_existing_candidate(migrated_pool: asyncpg.Pool) -> None:
    cid = await _capture(migrated_pool)
    await resolve_add_link(migrated_pool, cid, "https://shein.com/robe-p-2.html")
    async with migrated_pool.acquire() as conn:
        fiche = await db_get_fiche(conn, cid)
    assert fiche is not None and len(fiche.links) == 2
    assert {link["site_name"] for link in fiche.links} == {"Temu", "Shein"}


async def test_add_link_owned_by_other_candidate_names_it(migrated_pool: asyncpg.Pool) -> None:
    owner = await _capture(migrated_pool, "https://shein.com/x-p-3.html")
    await resolve_update_field(migrated_pool, "candidates", owner, "title", "Pull laine")
    other = await _capture(migrated_pool)
    with pytest.raises(LinkAlreadyAttachedError, match=r"« Pull laine » \(n° \d+\)"):
        await resolve_add_link(migrated_pool, other, "https://www.shein.com/x-p-3.html?utm_x=1")


async def test_add_link_already_on_same_candidate_says_so(migrated_pool: asyncpg.Pool) -> None:
    cid = await _capture(migrated_pool)
    with pytest.raises(LinkAlreadyAttachedError, match="cette candidate"):
        await resolve_add_link(migrated_pool, cid, "https://temu.com/p/1.html")


async def test_add_link_invalid_url_or_unknown_candidate(migrated_pool: asyncpg.Pool) -> None:
    cid = await _capture(migrated_pool)
    with pytest.raises(InvalidUrlError):
        await resolve_add_link(migrated_pool, cid, "pas un lien")
    with pytest.raises(NotFoundError):
        await resolve_add_link(migrated_pool, 888_888, "https://boutique.example/p/9")


async def test_remove_last_link_keeps_candidate(migrated_pool: asyncpg.Pool) -> None:
    cid = await _capture(migrated_pool)
    link_id = await migrated_pool.fetchval("SELECT id FROM purchase_links")
    assert await resolve_remove_link(migrated_pool, link_id) == cid
    assert await migrated_pool.fetchval("SELECT count(*) FROM candidates") == 1
    with pytest.raises(NotFoundError):
        await resolve_remove_link(migrated_pool, link_id)


async def test_fiche_unknown_site_and_unknown_candidate(migrated_pool: asyncpg.Pool) -> None:
    cid = await _capture(migrated_pool, "https://boutique-inconnue.example/p/1")
    async with migrated_pool.acquire() as conn:
        fiche = await db_get_fiche(conn, cid)
        assert await db_get_fiche(conn, 777_777) is None
    assert fiche is not None and fiche.links[0]["site_name"] is None


async def test_delete_candidate_removes_links_photos_and_files(
    migrated_pool: asyncpg.Pool, tmp_path: Path
) -> None:
    store = ImageStore(tmp_path)
    cid = await _capture(migrated_pool)
    keep = await _capture(migrated_pool, "https://temu.com/p/autre.html")
    await resolve_add_candidate_photo(migrated_pool, store, cid, make_image_bytes())
    kept = await resolve_add_candidate_photo(migrated_pool, store, keep, make_image_bytes())
    await resolve_delete_candidate(migrated_pool, store, cid)
    assert await migrated_pool.fetchval("SELECT count(*) FROM candidates WHERE id=$1", cid) == 0
    assert (
        await migrated_pool.fetchval(
            "SELECT count(*) FROM purchase_links WHERE candidate_id=$1", cid
        )
        == 0
    )
    assert not (tmp_path / f"candidates/{cid}").exists()
    assert store.file_exists(kept.file_path)
    with pytest.raises(NotFoundError):
        await resolve_delete_candidate(migrated_pool, store, cid)
