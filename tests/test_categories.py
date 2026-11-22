from __future__ import annotations

import asyncio

import asyncpg
import pytest

from atelier.candidates import categories as cats
from atelier.candidates.classify import (
    CandidateNotFoundError,
    resolve_apply_category_defaults,
    resolve_classify_candidate,
)
from atelier.candidates.fiche import resolve_update_field
from atelier.candidates.fields import FieldValueError


async def _cat(pool: asyncpg.Pool, label: str, parent: int | None = None, **defaults: str) -> int:
    cid = await cats.resolve_create_category(pool, label, parent)
    for name, value in defaults.items():
        await resolve_update_field(pool, "categories", cid, name, value)
    return cid


async def _candidate(pool: asyncpg.Pool) -> int:
    return await pool.fetchval("INSERT INTO candidates DEFAULT VALUES RETURNING id")  # type: ignore[no-any-return]


def test_build_tree_depth_zero_one_and_three() -> None:
    assert cats.build_tree([]) == []
    rows = [
        {"id": 1, "parent_id": None, "label": "Vêtements", "position": 0},
        {"id": 2, "parent_id": 1, "label": "Robes", "position": 1},
        {"id": 3, "parent_id": 1, "label": "Hauts", "position": 0},
        {"id": 4, "parent_id": 2, "label": "Longues", "position": 0},
        {"id": 5, "parent_id": None, "label": "Accessoires", "position": 0},
    ]
    roots = cats.build_tree(rows)
    assert [n.label for n in roots] == ["Accessoires", "Vêtements"]
    flat = cats.flatten(roots)
    assert [(n.label, n.depth) for n in flat] == [
        ("Accessoires", 0), ("Vêtements", 0), ("Hauts", 1), ("Robes", 1), ("Longues", 2),
    ]  # fmt: skip


def test_build_tree_beyond_max_depth_is_reported() -> None:
    rows = [{"id": 1, "parent_id": None, "label": "n0", "position": 0}] + [
        {"id": i, "parent_id": i - 1, "label": f"n{i}", "position": 0}
        for i in range(2, cats.MAX_DEPTH + 3)
    ]
    with pytest.raises(cats.CategoryTooDeepError):
        cats.build_tree(rows)


async def test_create_rename_and_validate_label(migrated_pool: asyncpg.Pool) -> None:
    cid = await _cat(migrated_pool, "Robes")
    assert await resolve_update_field(migrated_pool, "categories", cid, "label", "Robes d'été")
    with pytest.raises(FieldValueError):
        await cats.resolve_create_category(migrated_pool, "  ", None)
    with pytest.raises(cats.CategoryNotFoundError):
        await cats.resolve_create_category(migrated_pool, "Orpheline", 99_999)


async def test_move_into_own_descendant_is_refused(migrated_pool: asyncpg.Pool) -> None:
    root = await _cat(migrated_pool, "A")
    child = await _cat(migrated_pool, "B", root)
    grandchild = await _cat(migrated_pool, "C", child)
    for target in (root, grandchild):
        with pytest.raises(cats.CategoryCycleError):
            await cats.resolve_move_category(migrated_pool, root, target)
    await cats.resolve_move_category(migrated_pool, grandchild, None)
    assert (
        await migrated_pool.fetchval("SELECT parent_id FROM categories WHERE id=$1", grandchild)
        is None
    )


async def test_concurrent_crossed_moves_never_create_cycle(migrated_pool: asyncpg.Pool) -> None:
    a = await _cat(migrated_pool, "A")
    b = await _cat(migrated_pool, "B")
    results = await asyncio.gather(
        cats.resolve_move_category(migrated_pool, a, b),
        cats.resolve_move_category(migrated_pool, b, a),
        return_exceptions=True,
    )
    assert sum(isinstance(r, cats.CategoryCycleError) for r in results) == 1


async def test_move_unknown_category_or_parent(migrated_pool: asyncpg.Pool) -> None:
    a = await _cat(migrated_pool, "A")
    with pytest.raises(cats.CategoryNotFoundError):
        await cats.resolve_move_category(migrated_pool, 4242, None)
    with pytest.raises(cats.CategoryNotFoundError):
        await cats.resolve_move_category(migrated_pool, a, 4242)


async def test_delete_refused_with_counts_then_allowed(migrated_pool: asyncpg.Pool) -> None:
    parent = await _cat(migrated_pool, "Vêtements")
    child = await _cat(migrated_pool, "Robes", parent)
    cand = await _candidate(migrated_pool)
    await resolve_classify_candidate(migrated_pool, cand, parent)
    with pytest.raises(cats.CategoryInUseError, match=r"1 sous-catégorie.*1 candidate") as exc:
        await cats.resolve_delete_category(migrated_pool, parent)
    assert (exc.value.children, exc.value.candidates) == (1, 1)
    await cats.resolve_delete_category(migrated_pool, child)
    await resolve_classify_candidate(migrated_pool, cand, None)
    await cats.resolve_delete_category(migrated_pool, parent)
    with pytest.raises(cats.CategoryNotFoundError):
        await cats.resolve_delete_category(migrated_pool, parent)


async def test_effective_defaults_inherit_from_nearest_ancestor(
    migrated_pool: asyncpg.Pool,
) -> None:
    root = await _cat(migrated_pool, "Vêtements", parcel_size="M", description_template="Racine")
    mid = await _cat(migrated_pool, "Robes", root, description_template="Robe : {titre}")
    leaf = await _cat(migrated_pool, "Longues", mid, photo_prompt_template="Mannequin, robe longue")
    async with migrated_pool.acquire() as conn:
        effective = await cats.db_effective_defaults(conn, leaf)
        lone = await cats.db_effective_defaults(conn, await _cat(migrated_pool, "Seule"))
    assert effective == {
        "photo_prompt_template": "Mannequin, robe longue",
        "description_template": "Robe : {titre}",
        "parcel_size": "M",
    }
    assert set(lone.values()) == {None}


async def test_first_classification_fills_only_empty_fields(migrated_pool: asyncpg.Pool) -> None:
    cat = await _cat(
        migrated_pool, "Robes", description_template="Desc défaut", parcel_size="S",
        photo_prompt_template="Prompt défaut",
    )  # fmt: skip
    cand = await _candidate(migrated_pool)
    await resolve_update_field(migrated_pool, "candidates", cand, "description", "Ma description")
    result = await resolve_classify_candidate(migrated_pool, cand, cat)
    assert result.applied == {"photo_prompt": "Prompt défaut", "parcel_size": "S"}
    row = await migrated_pool.fetchrow("SELECT * FROM candidates WHERE id = $1", cand)
    assert (row["description"], row["parcel_size"], row["category_id"]) == (
        "Ma description",
        "S",
        cat,
    )


async def test_changing_category_rewrites_nothing_and_proposes(
    migrated_pool: asyncpg.Pool,
) -> None:
    first = await _cat(migrated_pool, "Robes", parcel_size="S")
    second = await _cat(migrated_pool, "Manteaux", parcel_size="L", description_template="Chaud")
    cand = await _candidate(migrated_pool)
    await resolve_classify_candidate(migrated_pool, cand, first)
    result = await resolve_classify_candidate(migrated_pool, cand, second)
    assert result.applied == {}
    assert result.proposal == {"parcel_size": "L", "description": "Chaud"}
    row = await migrated_pool.fetchrow("SELECT * FROM candidates WHERE id = $1", cand)
    assert (row["parcel_size"], row["description"], row["category_id"]) == ("S", None, second)
    applied = await resolve_apply_category_defaults(migrated_pool, cand)
    assert applied == {"parcel_size": "L", "description": "Chaud"}
    assert await migrated_pool.fetchval("SELECT parcel_size FROM candidates") == "L"


async def test_editing_category_defaults_changes_no_candidate(migrated_pool: asyncpg.Pool) -> None:
    cat = await _cat(migrated_pool, "Robes", parcel_size="S")
    cand = await _candidate(migrated_pool)
    await resolve_classify_candidate(migrated_pool, cand, cat)
    await resolve_update_field(migrated_pool, "categories", cat, "parcel_size", "XL")
    assert await migrated_pool.fetchval("SELECT parcel_size FROM candidates") == "S"


async def test_classify_unknown_candidate_or_category(migrated_pool: asyncpg.Pool) -> None:
    cand = await _candidate(migrated_pool)
    with pytest.raises(CandidateNotFoundError):
        await resolve_classify_candidate(migrated_pool, 777, None)
    with pytest.raises(cats.CategoryNotFoundError):
        await resolve_classify_candidate(migrated_pool, cand, 777)
    assert await resolve_apply_category_defaults(migrated_pool, cand) == {}


async def test_mappings_many_platforms_none_and_replace(migrated_pool: asyncpg.Pool) -> None:
    cat = await _cat(migrated_pool, "Robes")
    async with migrated_pool.acquire() as conn:
        assert await cats.db_list_mappings(conn, cat) == []
    await cats.resolve_set_mapping(migrated_pool, cat, "Vinted", "1234")
    await cats.resolve_set_mapping(migrated_pool, cat, "leboncoin", "vetements")
    await cats.resolve_set_mapping(migrated_pool, cat, "vinted", "5678")
    async with migrated_pool.acquire() as conn:
        mappings = await cats.db_list_mappings(conn, cat)
        assert await cats.db_delete_mapping(conn, cat, "leboncoin") is True
        assert await cats.db_delete_mapping(conn, cat, "leboncoin") is False
    assert mappings == [
        {"platform": "leboncoin", "platform_category_id": "vetements"},
        {"platform": "vinted", "platform_category_id": "5678"},
    ]
    with pytest.raises(ValueError, match="minuscules"):
        await cats.resolve_set_mapping(migrated_pool, cat, "plate forme", "x")
    with pytest.raises(cats.CategoryNotFoundError):
        await cats.resolve_set_mapping(migrated_pool, 9999, "vinted", "1")
