from __future__ import annotations

import asyncpg

from atelier.candidates.validation import (
    db_set_rule_enabled,
    resolve_add_field_rule,
    resolve_validate_candidate,
)


async def _candidate(pool: asyncpg.Pool, **values: object) -> int:
    cid = await pool.fetchval("INSERT INTO candidates DEFAULT VALUES RETURNING id")
    for column, value in values.items():
        await pool.execute(f"UPDATE candidates SET {column} = $1 WHERE id = $2", value, cid)  # noqa: S608
    return int(cid)


async def _complete(pool: asyncpg.Pool) -> int:
    cat = await pool.fetchval("INSERT INTO categories (label) VALUES ('Robes') RETURNING id")
    cid = await _candidate(
        pool, title="Robe", description="Belle robe", sale_price=25, size="M", category_id=cat
    )
    await pool.execute(
        "INSERT INTO candidate_photos (candidate_id, file_path, width, height)"
        " VALUES ($1, $2, 10, 10)",
        cid, f"candidates/{cid}/a.jpg",
    )  # fmt: skip
    await pool.execute(
        "INSERT INTO purchase_links (candidate_id, url, normalized_url, ships_from_europe,"
        " available_sizes) VALUES ($1, 'https://x.example/p', 'https://x.example/p', true,"
        " ARRAY['S', 'm'])",
        cid,
    )
    return cid


async def test_complete_candidate_is_ready(migrated_pool: asyncpg.Pool) -> None:
    report = await resolve_validate_candidate(migrated_pool, await _complete(migrated_pool))
    assert report is not None
    assert report.ready is True
    assert report.missing == []


async def test_empty_candidate_reports_every_failed_rule(migrated_pool: asyncpg.Pool) -> None:
    report = await resolve_validate_candidate(migrated_pool, await _candidate(migrated_pool))
    assert report is not None and report.ready is False
    assert [m.rule for m in report.missing] == [
        "titre_requis", "description_requise", "prix_vente_requis", "categorie_requise",
        "au_moins_une_photo", "lien_taille_europe",
    ]  # fmt: skip
    by_rule = {m.rule: m for m in report.missing}
    assert by_rule["categorie_requise"].target == "category_id"
    assert "Aucun lien d'achat" in by_rule["lien_taille_europe"].reason
    assert "0 sur 1" in by_rule["au_moins_une_photo"].reason


async def test_validation_never_modifies_candidate(migrated_pool: asyncpg.Pool) -> None:
    cid = await _candidate(migrated_pool, title="  ")
    before = await migrated_pool.fetchrow("SELECT * FROM candidates WHERE id = $1", cid)
    await resolve_validate_candidate(migrated_pool, cid)
    after = await migrated_pool.fetchrow("SELECT * FROM candidates WHERE id = $1", cid)
    assert dict(before) == dict(after)


async def test_blank_title_counts_as_missing(migrated_pool: asyncpg.Pool) -> None:
    report = await resolve_validate_candidate(
        migrated_pool, await _candidate(migrated_pool, title=" ")
    )
    assert report is not None and "titre_requis" in {m.rule for m in report.missing}


async def test_link_without_europe_or_size_is_missing(migrated_pool: asyncpg.Pool) -> None:
    cid = await _complete(migrated_pool)
    await migrated_pool.execute("UPDATE purchase_links SET ships_from_europe = NULL")
    report = await resolve_validate_candidate(migrated_pool, cid)
    assert report is not None and [m.rule for m in report.missing] == ["lien_taille_europe"]
    await migrated_pool.execute("UPDATE purchase_links SET ships_from_europe = true")
    await migrated_pool.execute("UPDATE candidates SET size = 'XL'")
    report = await resolve_validate_candidate(migrated_pool, cid)
    assert report is not None and [m.rule for m in report.missing] == ["lien_taille_europe"]
    await migrated_pool.execute("UPDATE candidates SET size = NULL")
    report = await resolve_validate_candidate(migrated_pool, cid)
    assert report is not None and "taille retenue" in report.missing[0].reason


async def test_disabled_rule_is_not_evaluated(migrated_pool: asyncpg.Pool) -> None:
    rule_id = await migrated_pool.fetchval(
        "SELECT id FROM validation_rules WHERE code = 'au_moins_une_photo'"
    )
    async with migrated_pool.acquire() as conn:
        assert await db_set_rule_enabled(conn, rule_id, False) is True
        assert await db_set_rule_enabled(conn, 99_999, False) is False
    report = await resolve_validate_candidate(migrated_pool, await _candidate(migrated_pool))
    assert report is not None and "au_moins_une_photo" not in {m.rule for m in report.missing}


async def test_new_rule_is_data_only(migrated_pool: asyncpg.Pool) -> None:
    cid = await _complete(migrated_pool)
    await resolve_add_field_rule(migrated_pool, "brand", "")
    report = await resolve_validate_candidate(migrated_pool, cid)
    assert report is not None
    assert [(m.rule, m.target) for m in report.missing] == [("champ_brand_requis", "brand")]
    assert "Marque" in report.missing[0].reason


async def test_misconfigured_rule_reported_as_readable_missing(migrated_pool: asyncpg.Pool) -> None:
    await migrated_pool.execute(
        "INSERT INTO validation_rules (code, kind, params, message) VALUES"
        " ('cassee', 'inexistant', '{}', 'x'),"
        " ('champ_faux', 'field_required', '{\"field\": \"id\"}', 'y')"
    )
    report = await resolve_validate_candidate(migrated_pool, await _complete(migrated_pool))
    assert report is not None
    reasons = [m.reason for m in report.missing]
    assert any("cassee" in r and "type de règle inconnu" in r for r in reasons)
    assert any("champ inconnu" in r for r in reasons)


async def test_unknown_candidate_returns_none(migrated_pool: asyncpg.Pool) -> None:
    assert await resolve_validate_candidate(migrated_pool, 123_456) is None


async def test_report_as_dict_is_split_per_missing(migrated_pool: asyncpg.Pool) -> None:
    report = await resolve_validate_candidate(migrated_pool, await _candidate(migrated_pool))
    assert report is not None
    data = report.as_dict()
    assert data["ready"] is False
    assert all(set(m) == {"rule", "target", "reason"} for m in data["missing"])
