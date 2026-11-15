from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import pytest

from atelier.errors import StartupError
from atelier.migrations_runner import (
    MIGRATIONS_DIR,
    db_applied_versions,
    file_list_migrations,
    resolve_apply_migrations,
)


@pytest.fixture
async def clean_pool(pool: asyncpg.Pool) -> AsyncIterator[asyncpg.Pool]:
    # La base jetable est partagée par la session : chaque test repart d'un schéma vide.
    await pool.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    yield pool


def _write(directory: Path, name: str, sql: str) -> None:
    (directory / name).write_text(sql, encoding="utf-8")


def test_list_migrations_sorts_by_version(tmp_path: Path) -> None:
    _write(tmp_path, "0010_b.sql", "SELECT 1")
    _write(tmp_path, "0002_a.sql", "SELECT 1")
    assert [m.version for m in file_list_migrations(tmp_path)] == [2, 10]


def test_list_migrations_empty_dir_returns_nothing(tmp_path: Path) -> None:
    assert file_list_migrations(tmp_path) == []


def test_list_migrations_bad_name_raises(tmp_path: Path) -> None:
    _write(tmp_path, "init.sql", "SELECT 1")
    with pytest.raises(StartupError, match=r"init\.sql"):
        file_list_migrations(tmp_path)


def test_list_migrations_duplicate_version_raises(tmp_path: Path) -> None:
    _write(tmp_path, "0001_a.sql", "SELECT 1")
    _write(tmp_path, "0001_b.sql", "SELECT 1")
    with pytest.raises(StartupError, match="0001"):
        file_list_migrations(tmp_path)


def test_shipped_migrations_are_well_formed() -> None:
    file_list_migrations(MIGRATIONS_DIR)


async def test_apply_on_blank_database_applies_all(
    clean_pool: asyncpg.Pool, tmp_path: Path
) -> None:
    _write(tmp_path, "0001_t.sql", "CREATE TABLE t (id int)")
    _write(tmp_path, "0002_u.sql", "CREATE TABLE u (id int)")
    assert await resolve_apply_migrations(clean_pool, tmp_path) == [1, 2]
    assert await clean_pool.fetchval("SELECT count(*) FROM u") == 0


async def test_apply_twice_applies_nothing_and_duplicates_nothing(
    clean_pool: asyncpg.Pool, tmp_path: Path
) -> None:
    _write(tmp_path, "0001_t.sql", "CREATE TABLE t (id int)")
    await resolve_apply_migrations(clean_pool, tmp_path)
    assert await resolve_apply_migrations(clean_pool, tmp_path) == []
    assert await clean_pool.fetchval("SELECT count(*) FROM schema_migrations") == 1


async def test_apply_on_existing_database_applies_only_new(
    clean_pool: asyncpg.Pool, tmp_path: Path
) -> None:
    _write(tmp_path, "0001_t.sql", "CREATE TABLE t (id int)")
    await resolve_apply_migrations(clean_pool, tmp_path)
    _write(tmp_path, "0002_u.sql", "CREATE TABLE u (id int)")
    assert await resolve_apply_migrations(clean_pool, tmp_path) == [2]


async def test_apply_failing_migration_rolls_back_and_is_not_recorded(
    clean_pool: asyncpg.Pool, tmp_path: Path
) -> None:
    _write(tmp_path, "0001_t.sql", "CREATE TABLE t (id int)")
    _write(tmp_path, "0002_bad.sql", "CREATE TABLE v (id int); SELECT * FROM table_inexistante")
    with pytest.raises(asyncpg.UndefinedTableError):
        await resolve_apply_migrations(clean_pool, tmp_path)
    async with clean_pool.acquire() as conn:
        assert await db_applied_versions(conn) == {1}
    assert await clean_pool.fetchval("SELECT to_regclass('v')") is None


async def test_applied_versions_empty_ledger_returns_empty_set(
    clean_pool: asyncpg.Pool, tmp_path: Path
) -> None:
    await resolve_apply_migrations(clean_pool, tmp_path)
    async with clean_pool.acquire() as conn:
        assert await db_applied_versions(conn) == set()
