from __future__ import annotations

from urllib.parse import urlsplit

import asyncpg

from atelier.errors import StartupError

POOL_MIN_SIZE = 1
POOL_MAX_SIZE = 5
PING_TIMEOUT_S = 2.0

_CONNECTION_ERRORS = (OSError, TimeoutError, asyncpg.PostgresError, asyncpg.InterfaceError)


def describe_db_target(dsn: str) -> str:
    """Rend « hôte:port/base » sans l'identifiant ni le mot de passe."""
    parts = urlsplit(dsn)
    host = parts.hostname or "socket locale"
    port = parts.port or 5432
    database = parts.path.lstrip("/") or "?"
    return f"{host}:{port}/{database}"


async def db_open_pool(dsn: str, timeout_s: float) -> asyncpg.Pool:
    try:
        return await asyncpg.create_pool(
            dsn, min_size=POOL_MIN_SIZE, max_size=POOL_MAX_SIZE, timeout=timeout_s
        )
    except _CONNECTION_ERRORS as exc:
        # Message lisible nommant l'hôte visé (exigence du socle) ; la cible est
        # recalculée sans identifiants pour ne jamais afficher le mot de passe
        # que contient la chaîne de connexion.
        raise StartupError(
            f"Base PostgreSQL injoignable sur {describe_db_target(dsn)} : "
            f"{type(exc).__name__}: {exc}"
        ) from None


async def db_ping(pool: asyncpg.Pool) -> bool:
    try:
        await pool.fetchval("SELECT 1", timeout=PING_TIMEOUT_S)
    except _CONNECTION_ERRORS:
        return False
    return True
