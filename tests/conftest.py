from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest

# DSN d'administration d'un serveur PostgreSQL de test (jamais celui de l'atelier).
# Une base jetable y est créée par exécution, puis supprimée.
ADMIN_DSN_ENV = "ATELIER_TEST_ADMIN_DSN"


def _with_database(dsn: str, database: str) -> str:
    parts = urlsplit(dsn)
    return urlunsplit(parts._replace(path=f"/{database}"))


@pytest.fixture(scope="session")
async def database_dsn() -> AsyncIterator[str]:
    admin_dsn = os.environ.get(ADMIN_DSN_ENV)
    if not admin_dsn:
        pytest.skip(f"{ADMIN_DSN_ENV} non défini : tests PostgreSQL ignorés")
    name = f"atelier_test_{uuid.uuid4().hex[:12]}"
    admin = await asyncpg.connect(admin_dsn)
    try:
        # Le nom est généré ici (hex), jamais issu d'une entrée : CREATE DATABASE
        # n'accepte pas de paramètre lié.
        await admin.execute(f'CREATE DATABASE "{name}"')
        yield _with_database(admin_dsn, name)
    finally:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


@pytest.fixture
async def pool(database_dsn: str) -> AsyncIterator[asyncpg.Pool]:
    created = await asyncpg.create_pool(database_dsn, min_size=1, max_size=2)
    try:
        yield created
    finally:
        await created.close()


@pytest.fixture
async def migrated_pool(pool: asyncpg.Pool) -> AsyncIterator[asyncpg.Pool]:
    # La base jetable est partagée par la session : chaque test repart d'un schéma
    # vide, puis des migrations réellement livrées (pas d'un schéma de test à part).
    from atelier.migrations_runner import MIGRATIONS_DIR, resolve_apply_migrations

    await pool.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    await resolve_apply_migrations(pool, MIGRATIONS_DIR)
    yield pool
