"""Clefs d'API des agents MCP : créées ici, affichées une fois, stockées en empreinte."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import asyncpg
import structlog

from atelier.candidates import repository as repo
from atelier.connectors.tokens import API_KEY_PREFIX, new_token, token_hash

log = structlog.get_logger(__name__)

_DISPLAY_PREFIX_CHARS = len(API_KEY_PREFIX) + 4


class ApiKeyError(ValueError):
    pass


@dataclass(frozen=True)
class ApiKeyInfo:
    id: int
    name: str
    prefix: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


async def resolve_create_api_key(pool: asyncpg.Pool, name: str) -> tuple[int, str]:
    """Rend (identifiant, clef en clair). La clef en clair n'existe QUE dans ce retour."""
    label = name.strip()
    if not 1 <= len(label) <= 100:
        raise ApiKeyError("le nom de la clef doit faire de 1 à 100 caractères")
    raw = new_token(API_KEY_PREFIX)
    key_id = await pool.fetchval(
        "INSERT INTO mcp_api_keys (name, prefix, key_hash) VALUES ($1, $2, $3) RETURNING id",
        label, raw[:_DISPLAY_PREFIX_CHARS], token_hash(raw),
    )  # fmt: skip
    log.info("clef_api_creee", clef_id=key_id, nom=label)
    return int(key_id), raw


async def db_list_api_keys(conn: repo._Conn) -> list[ApiKeyInfo]:
    rows = await conn.fetch(
        "SELECT id, name, prefix, created_at, last_used_at, revoked_at FROM mcp_api_keys"
        " ORDER BY revoked_at IS NOT NULL, created_at DESC"
    )
    return [ApiKeyInfo(**dict(r)) for r in rows]


async def db_revoke_api_key(conn: repo._Conn, key_id: int) -> bool:
    done = await conn.fetchval(
        "UPDATE mcp_api_keys SET revoked_at = coalesce(revoked_at, now()) WHERE id = $1"
        " RETURNING 1",
        key_id,
    )
    if done is not None:
        log.info("clef_api_revoquee", clef_id=key_id)
    return done is not None
