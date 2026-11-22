"""Clients OAuth (inscription dynamique RFC 7591) et codes d'appairage."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

import asyncpg
import structlog

from atelier.candidates import repository as repo
from atelier.connectors.tokens import token_hash

log = structlog.get_logger(__name__)

# Plafond de clients actifs : l'inscription est ouverte (claude.ai s'inscrit seul), elle
# ne doit pas permettre de remplir la table. Un client inscrit n'obtient rien sans
# consentement, donc l'ouverture ne donne aucun accès par elle-même.
MAX_ACTIVE_CLIENTS = 50
PAIRING_TTL_MINUTES = 10
# Au-delà de ce nombre d'échecs, tous les codes en cours sont annulés : 40 bits ne
# résistent pas à un essai illimité, même en 10 minutes.
MAX_PAIRING_FAILURES = 5
_PAIRING_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # sans 0/O ni 1/I : lu à l'écran
_PAIRING_LEN = 8
_LOCAL_HOSTS = {"localhost", "127.0.0.1"}


class OAuthError(ValueError):
    """Erreur au format OAuth : code normalisé + description lisible."""

    def __init__(self, error: str, description: str) -> None:
        super().__init__(description)
        self.error, self.description = error, description

    def as_dict(self) -> dict[str, str]:
        return {"error": self.error, "error_description": self.description}


@dataclass(frozen=True)
class ClientInfo:
    client_id: str
    client_name: str
    redirect_uris: list[str]
    created_at: datetime
    revoked_at: datetime | None
    active_tokens: int = 0
    last_used_at: datetime | None = None


def _check_redirect_uri(uri: object) -> str:
    if not isinstance(uri, str) or len(uri) > 500:
        raise OAuthError("invalid_redirect_uri", "adresse de retour invalide")
    parts = urlsplit(uri)
    # https obligatoire, sauf en local (clients de bureau) : un retour en http clair
    # ferait transiter le code d'autorisation lisible sur le réseau.
    local = parts.scheme == "http" and parts.hostname in _LOCAL_HOSTS
    if not (parts.scheme == "https" or local) or not parts.hostname or parts.fragment:
        raise OAuthError("invalid_redirect_uri", f"adresse de retour refusée : {uri}")
    return uri


async def resolve_register_client(pool: asyncpg.Pool, body: dict[str, Any]) -> dict[str, Any]:
    uris = body.get("redirect_uris")
    if not isinstance(uris, list) or not 1 <= len(uris) <= 10:
        raise OAuthError("invalid_redirect_uri", "redirect_uris : 1 à 10 adresses attendues")
    clean = [_check_redirect_uri(u) for u in uris]
    name = str(body.get("client_name") or "Client MCP").strip()[:100] or "Client MCP"
    client_id = "atl_client_" + secrets.token_urlsafe(16)
    async with pool.acquire() as conn, conn.transaction():
        active = await conn.fetchval("SELECT count(*) FROM oauth_clients WHERE revoked_at IS NULL")
        if active >= MAX_ACTIVE_CLIENTS:
            raise OAuthError("invalid_client_metadata", "trop de clients inscrits")
        created = await conn.fetchval(
            "INSERT INTO oauth_clients (client_id, client_name, redirect_uris)"
            " VALUES ($1, $2, $3) RETURNING extract(epoch FROM created_at)::bigint",
            client_id, name, clean,
        )  # fmt: skip
    log.info("client_oauth_inscrit", client_id=client_id, nom=name)
    # Client public uniquement (PKCE, sans secret) : c'est ce que fait claude.ai, et un
    # secret de client ne prouverait rien de plus pour un client inscrit par lui-même.
    return {
        "client_id": client_id, "client_id_issued_at": created, "client_name": name,
        "redirect_uris": clean, "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
    }  # fmt: skip


async def db_get_active_client(conn: repo._Conn, client_id: str) -> ClientInfo | None:
    row = await conn.fetchrow(
        "SELECT client_id, client_name, redirect_uris, created_at, revoked_at"
        " FROM oauth_clients WHERE client_id = $1 AND revoked_at IS NULL",
        client_id,
    )
    return ClientInfo(**dict(row)) if row else None


async def db_list_clients(conn: repo._Conn) -> list[ClientInfo]:
    rows = await conn.fetch(
        "SELECT c.client_id, c.client_name, c.redirect_uris, c.created_at, c.revoked_at,"
        " count(t.id) FILTER (WHERE t.revoked_at IS NULL AND t.expires_at > now())"
        "   AS active_tokens, max(t.last_used_at) AS last_used_at"
        " FROM oauth_clients c LEFT JOIN oauth_tokens t ON t.client_id = c.client_id"
        " GROUP BY c.client_id ORDER BY c.revoked_at IS NOT NULL, c.created_at DESC"
    )  # fmt: skip
    return [ClientInfo(**dict(r)) for r in rows]


async def resolve_revoke_client(pool: asyncpg.Pool, client_id: str) -> bool:
    async with pool.acquire() as conn, conn.transaction():
        done = await conn.fetchval(
            "UPDATE oauth_clients SET revoked_at = coalesce(revoked_at, now())"
            " WHERE client_id = $1 RETURNING 1",
            client_id,
        )
        await conn.execute(
            "UPDATE oauth_tokens SET revoked_at = now()"
            " WHERE client_id = $1 AND revoked_at IS NULL",
            client_id,
        )
    if done is not None:
        log.info("client_oauth_revoque", client_id=client_id)
    return done is not None


async def resolve_create_pairing_code(pool: asyncpg.Pool) -> str:
    code = "".join(secrets.choice(_PAIRING_ALPHABET) for _ in range(_PAIRING_LEN))
    await pool.execute(
        "INSERT INTO oauth_pairing_codes (code_hash, expires_at)"
        " VALUES ($1, now() + make_interval(mins => $2))",
        token_hash(code), PAIRING_TTL_MINUTES,
    )  # fmt: skip
    log.info("code_appairage_cree", validite_min=PAIRING_TTL_MINUTES)
    return f"{code[:4]}-{code[4:]}"


def _normalize_pairing(raw: str) -> str:
    return "".join(ch for ch in raw.upper() if ch.isalnum())


async def resolve_consume_pairing_code(conn: repo._Conn, raw: str) -> bool:
    """Consomme le code (une seule fois) ; compte les échecs et verrouille au-delà du plafond.

    À appeler dans une transaction : la consommation et le compteur vont ensemble.
    """
    used = await conn.fetchval(
        "UPDATE oauth_pairing_codes SET used_at = now() WHERE code_hash = $1"
        " AND used_at IS NULL AND expires_at > now() RETURNING id",
        token_hash(_normalize_pairing(raw)),
    )
    if used is not None:
        await conn.execute("UPDATE oauth_pairing_failures SET count = 0")
        return True
    failures = await conn.fetchval(
        "UPDATE oauth_pairing_failures SET count = count + 1 RETURNING count"
    )
    if failures >= MAX_PAIRING_FAILURES:
        await conn.execute("UPDATE oauth_pairing_codes SET used_at = now() WHERE used_at IS NULL")
        await conn.execute("UPDATE oauth_pairing_failures SET count = 0")
        log.warning("codes_appairage_annules", raison="trop d'échecs de saisie")
    else:
        log.info("code_appairage_refuse", echecs=failures)
    return False
