from __future__ import annotations

import asyncio

import asyncpg
import pytest

from atelier.candidates import repository as repo
from atelier.candidates.capture import resolve_capture_url
from atelier.candidates.url_normalizer import InvalidUrlError


async def _site_name_of_candidate(pool: asyncpg.Pool, candidate_id: int) -> str | None:
    return await pool.fetchval(  # type: ignore[no-any-return]
        "SELECT s.name FROM purchase_links l LEFT JOIN purchase_sites s ON s.id = l.site_id"
        " WHERE l.candidate_id = $1",
        candidate_id,
    )


async def test_capture_new_url_creates_candidate_and_link_with_site(
    migrated_pool: asyncpg.Pool,
) -> None:
    result = await resolve_capture_url(migrated_pool, "https://fr.aliexpress.com/item/77.html")
    assert result.created is True
    assert result.title is None
    assert await _site_name_of_candidate(migrated_pool, result.candidate_id) == "AliExpress"


async def test_capture_same_article_with_tracking_returns_existing(
    migrated_pool: asyncpg.Pool,
) -> None:
    first = await resolve_capture_url(migrated_pool, "https://www.temu.com/p/9.html")
    again = await resolve_capture_url(migrated_pool, "https://temu.com/p/9.html?utm_source=ig")
    assert again.created is False
    assert again.candidate_id == first.candidate_id
    assert await migrated_pool.fetchval("SELECT count(*) FROM candidates") == 1
    assert await migrated_pool.fetchval("SELECT count(*) FROM purchase_links") == 1


async def test_capture_existing_returns_current_title(migrated_pool: asyncpg.Pool) -> None:
    first = await resolve_capture_url(migrated_pool, "https://shein.com/robe-p-1.html")
    await migrated_pool.execute(
        "UPDATE candidates SET title = 'Robe lin' WHERE id = $1", first.candidate_id
    )
    again = await resolve_capture_url(migrated_pool, "https://shein.com/robe-p-1.html")
    assert again.title == "Robe lin"


async def test_capture_unknown_domain_creates_candidate_without_site(
    migrated_pool: asyncpg.Pool,
) -> None:
    result = await resolve_capture_url(migrated_pool, "https://boutique-inconnue.example/p/1")
    assert result.created is True
    assert await _site_name_of_candidate(migrated_pool, result.candidate_id) is None


async def test_capture_invalid_url_raises_and_writes_nothing(migrated_pool: asyncpg.Pool) -> None:
    with pytest.raises(InvalidUrlError):
        await resolve_capture_url(migrated_pool, "pas une url")
    assert await migrated_pool.fetchval("SELECT count(*) FROM candidates") == 0


async def test_capture_concurrent_same_url_creates_one_candidate(
    migrated_pool: asyncpg.Pool,
) -> None:
    results = await asyncio.gather(
        resolve_capture_url(migrated_pool, "https://amazon.fr/dp/B01?tag=aff-21"),
        resolve_capture_url(migrated_pool, "https://www.amazon.fr/dp/B01"),
    )
    assert sorted(r.created for r in results) == [False, True]
    assert results[0].candidate_id == results[1].candidate_id
    # Le perdant ne laisse pas de candidate orpheline : sa transaction est annulée.
    assert await migrated_pool.fetchval("SELECT count(*) FROM candidates") == 1


async def test_find_candidate_by_link_unknown_returns_none(migrated_pool: asyncpg.Pool) -> None:
    async with migrated_pool.acquire() as conn:
        assert await repo.db_find_candidate_by_link(conn, "https://nulle.part/") is None


async def test_find_site_unknown_domain_returns_none(migrated_pool: asyncpg.Pool) -> None:
    async with migrated_pool.acquire() as conn:
        assert await repo.db_find_site(conn, ["inconnu.example"]) is None


async def test_find_site_prefers_longest_matching_domain(migrated_pool: asyncpg.Pool) -> None:
    await migrated_pool.execute(
        "INSERT INTO purchase_sites (name, domain) VALUES ('AE FR', 'fr.aliexpress.com')"
    )
    async with migrated_pool.acquire() as conn:
        site = await repo.db_find_site(conn, ["fr.aliexpress.com", "aliexpress.com"])
    assert site is not None and site.domain == "fr.aliexpress.com"


async def test_list_candidates_empty_returns_nothing(migrated_pool: asyncpg.Pool) -> None:
    async with migrated_pool.acquire() as conn:
        assert await repo.db_list_candidates(conn, None, 10) == []


async def test_list_candidates_newest_first_with_cursor_and_link_count(
    migrated_pool: asyncpg.Pool,
) -> None:
    ids = [
        (await resolve_capture_url(migrated_pool, f"https://shop.example/p/{n}")).candidate_id
        for n in range(5)
    ]
    async with migrated_pool.acquire() as conn:
        first = await repo.db_list_candidates(conn, None, 2)
        after = await repo.db_list_candidates(conn, first[-1].id, 10)
    assert [r.id for r in first] == [ids[4], ids[3]]
    assert [r.id for r in after] == [ids[2], ids[1], ids[0]]
    assert first[0].link_count == 1
    assert first[0].first_link_host == "shop.example"
    assert first[0].status == "brouillon"


async def test_capture_same_article_on_country_subdomain_returns_existing(
    migrated_pool: asyncpg.Pool,
) -> None:
    # Défaut constaté sur la machine de test : fr. et www. d'un même site connu
    # créaient deux candidates pour un seul article.
    first = await resolve_capture_url(migrated_pool, "https://fr.aliexpress.com/item/55.html?spm=x")
    again = await resolve_capture_url(migrated_pool, "https://www.aliexpress.com/item/55.html")
    other = await resolve_capture_url(migrated_pool, "https://de.aliexpress.com/item/55.html")
    assert again.created is False
    assert other.created is False
    assert again.candidate_id == other.candidate_id == first.candidate_id


async def test_capture_unknown_site_subdomains_stay_distinct(migrated_pool: asyncpg.Pool) -> None:
    # Hors des sites connus, rien ne dit que deux sous-domaines vendent le même
    # article : seul « www. » est assimilé.
    a = await resolve_capture_url(migrated_pool, "https://fr.boutique.example/p/1")
    b = await resolve_capture_url(migrated_pool, "https://de.boutique.example/p/1")
    assert a.candidate_id != b.candidate_id
