"""Migrations de schéma : fichiers SQL numérotés, appliqués une seule fois.

Choix d'un runner maison plutôt qu'alembic : alembic suppose SQLAlchemy, que
l'atelier n'utilise pas (asyncpg seul). Le besoin se limite à « jouer dans
l'ordre les fichiers pas encore joués », ce que ce module fait en peu de lignes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import asyncpg
import structlog
from asyncpg.pool import PoolConnectionProxy

from atelier.errors import StartupError

log = structlog.get_logger(__name__)

_Conn = asyncpg.Connection | PoolConnectionProxy

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_FILENAME_RE = re.compile(r"^(?P<version>\d{4})_(?P<name>[a-z0-9_]+)\.sql$")
# Clé arbitraire mais fixe : deux instances qui démarrent en même temps
# sérialisent leurs migrations sur ce verrou au lieu de les jouer deux fois.
_ADVISORY_LOCK_KEY = 0x41_54_45_4C  # "ATEL"

_CREATE_LEDGER = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    integer     PRIMARY KEY,
    name       text        NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str


def file_list_migrations(directory: Path) -> list[Migration]:
    migrations: dict[int, Migration] = {}
    for path in sorted(directory.glob("*.sql")):
        match = _FILENAME_RE.match(path.name)
        if match is None:
            raise StartupError(f"Nom de migration invalide : {path.name} (attendu NNNN_nom.sql)")
        version = int(match["version"])
        if version in migrations:
            raise StartupError(f"Numéro de migration en double : {version:04d}")
        migrations[version] = Migration(version, match["name"], path.read_text("utf-8"))
    return [migrations[v] for v in sorted(migrations)]


async def db_applied_versions(conn: _Conn) -> set[int]:
    rows = await conn.fetch("SELECT version FROM schema_migrations")
    return {row["version"] for row in rows}


async def resolve_apply_migrations(pool: asyncpg.Pool, directory: Path) -> list[int]:
    """Applique les migrations manquantes ; rend les versions appliquées."""
    migrations = file_list_migrations(directory)
    async with pool.acquire() as conn:
        await conn.execute("SELECT pg_advisory_lock($1)", _ADVISORY_LOCK_KEY)
        try:
            await conn.execute(_CREATE_LEDGER)
            # Lu APRÈS la prise du verrou : lu avant, deux instances verraient
            # la même migration manquante et la joueraient toutes les deux.
            applied = await db_applied_versions(conn)
            pending = [m for m in migrations if m.version not in applied]
            for migration in pending:
                await _db_apply_one(conn, migration)
            return [m.version for m in pending]
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", _ADVISORY_LOCK_KEY)


async def _db_apply_one(conn: _Conn, migration: Migration) -> None:
    # Le script et son inscription au registre dans la même transaction : une
    # migration qui échoue à mi-chemin ne laisse ni schéma partiel, ni trace
    # « appliquée » qui empêcherait de la rejouer une fois corrigée.
    async with conn.transaction():
        await conn.execute(migration.sql)
        await conn.execute(
            "INSERT INTO schema_migrations (version, name) VALUES ($1, $2)",
            migration.version,
            migration.name,
        )
    log.info("migration_appliquee", version=migration.version, nom=migration.name)
