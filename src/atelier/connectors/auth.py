"""Authentification d'un appel au point d'entrée MCP."""

from __future__ import annotations

from dataclasses import dataclass

import asyncpg

from atelier.connectors.tokens import ACCESS_TOKEN_PREFIX, API_KEY_PREFIX, token_hash


@dataclass(frozen=True)
class Principal:
    kind: str  # "api_key" ou "oauth"
    label: str  # nom de la clef ou du client, pour les journaux


async def resolve_authenticate(pool: asyncpg.Pool, raw: str) -> Principal | None:
    """Rend l'appelant, ou None si la clef ou le jeton est inconnu, expiré ou révoqué.

    L'état (révocation, expiration) est relu en base à CHAQUE appel plutôt que mis en
    cache : une révocation sur la page Connecteurs doit couper l'accès immédiatement.
    """
    if raw.startswith(API_KEY_PREFIX):
        name = await pool.fetchval(
            "UPDATE mcp_api_keys SET last_used_at = now()"
            " WHERE key_hash = $1 AND revoked_at IS NULL RETURNING name",
            token_hash(raw),
        )
        return Principal("api_key", name) if name is not None else None
    if raw.startswith(ACCESS_TOKEN_PREFIX):
        client = await pool.fetchval(
            "UPDATE oauth_tokens t SET last_used_at = now() FROM oauth_clients c"
            " WHERE t.token_hash = $1 AND t.kind = 'access' AND t.revoked_at IS NULL"
            " AND t.expires_at > now() AND c.client_id = t.client_id AND c.revoked_at IS NULL"
            " RETURNING c.client_name",
            token_hash(raw),
        )  # fmt: skip
        return Principal("oauth", client) if client is not None else None
    return None
