"""Flux OAuth 2.1 : demande d'autorisation, code, échange contre jetons, rafraîchissement."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
from dataclasses import dataclass
from typing import Any

import asyncpg
import structlog

from atelier.candidates import repository as repo
from atelier.connectors.oauth_clients import (
    OAuthError,
    db_get_active_client,
    resolve_consume_pairing_code,
)
from atelier.connectors.tokens import (
    ACCESS_TOKEN_PREFIX,
    AUTH_CODE_PREFIX,
    REFRESH_TOKEN_PREFIX,
    new_token,
    token_hash,
)

log = structlog.get_logger(__name__)

AUTH_CODE_TTL_S = 300
ACCESS_TOKEN_TTL_S = 3600
REFRESH_TOKEN_TTL_S = 30 * 24 * 3600
_VERIFIER_RE = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")  # RFC 7636 §4.1
_CHALLENGE_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")  # SHA-256 en base64url sans bourrage


@dataclass(frozen=True)
class AuthorizeRequest:
    client_id: str
    client_name: str
    redirect_uri: str
    code_challenge: str
    state: str | None


class AuthorizeRejected(ValueError):
    """Demande invalide AVANT d'avoir un retour sûr : on affiche, on ne redirige pas."""


async def resolve_check_authorize(pool: asyncpg.Pool, params: dict[str, str]) -> AuthorizeRequest:
    async with pool.acquire() as conn:
        client = await db_get_active_client(conn, params.get("client_id", ""))
    if client is None:
        raise AuthorizeRejected("Client inconnu ou révoqué.")
    redirect = params.get("redirect_uri") or (
        client.redirect_uris[0] if len(client.redirect_uris) == 1 else ""
    )
    # Comparaison exacte avec une adresse inscrite : rediriger ailleurs enverrait le
    # code d'autorisation à un tiers (redirection ouverte).
    if redirect not in client.redirect_uris:
        raise AuthorizeRejected("Adresse de retour non inscrite pour ce client.")
    if params.get("response_type") != "code":
        raise AuthorizeRejected("Seul response_type=code est pris en charge.")
    # PKCE S256 exigé, jamais « plain » : sans lui, un code intercepté suffit.
    challenge = params.get("code_challenge", "")
    if params.get("code_challenge_method") != "S256" or not _CHALLENGE_RE.fullmatch(challenge):
        raise AuthorizeRejected("PKCE S256 obligatoire (code_challenge manquant ou invalide).")
    return AuthorizeRequest(
        client.client_id, client.client_name, redirect, challenge, params.get("state")
    )


async def resolve_approve(pool: asyncpg.Pool, req: AuthorizeRequest, pairing: str) -> str | None:
    """Rend le code d'autorisation si le code d'appairage est bon, None sinon."""
    code = new_token(AUTH_CODE_PREFIX)
    async with pool.acquire() as conn, conn.transaction():
        if not await resolve_consume_pairing_code(conn, pairing):
            return None
        await conn.execute(
            "INSERT INTO oauth_auth_codes (code_hash, client_id, redirect_uri, code_challenge,"
            " expires_at) VALUES ($1, $2, $3, $4, now() + make_interval(secs => $5))",
            token_hash(code), req.client_id, req.redirect_uri, req.code_challenge,
            AUTH_CODE_TTL_S,
        )  # fmt: skip
    log.info("autorisation_accordee", client_id=req.client_id)
    return code


def _pkce_ok(verifier: str, challenge: str) -> bool:
    if not _VERIFIER_RE.fullmatch(verifier):
        return False
    digest = hashlib.sha256(verifier.encode()).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return hmac.compare_digest(computed, challenge)


async def _issue_tokens(conn: repo._Conn, client_id: str) -> dict[str, Any]:
    access, refresh = new_token(ACCESS_TOKEN_PREFIX), new_token(REFRESH_TOKEN_PREFIX)
    await conn.execute(
        "INSERT INTO oauth_tokens (token_hash, kind, client_id, expires_at) VALUES"
        " ($1, 'access', $3, now() + make_interval(secs => $4)),"
        " ($2, 'refresh', $3, now() + make_interval(secs => $5))",
        token_hash(access), token_hash(refresh), client_id, ACCESS_TOKEN_TTL_S,
        REFRESH_TOKEN_TTL_S,
    )  # fmt: skip
    return {
        "access_token": access, "token_type": "Bearer", "expires_in": ACCESS_TOKEN_TTL_S,
        "refresh_token": refresh,
    }  # fmt: skip


async def resolve_token(pool: asyncpg.Pool, form: dict[str, str]) -> dict[str, Any]:
    grant = form.get("grant_type")
    if grant == "authorization_code":
        return await _exchange_code(pool, form)
    if grant == "refresh_token":
        return await _refresh(pool, form)
    raise OAuthError("unsupported_grant_type", f"grant_type non pris en charge : {grant}")


async def _exchange_code(pool: asyncpg.Pool, form: dict[str, str]) -> dict[str, Any]:
    async with pool.acquire() as conn:
        # Consommé par l'UPDATE lui-même, HORS transaction : deux échanges simultanés ne
        # peuvent pas réussir tous les deux, et un échec de contrôle plus bas (PKCE…)
        # brûle quand même le code au lieu de l'annuler avec la transaction.
        row = await conn.fetchrow(
            "UPDATE oauth_auth_codes SET used_at = now() WHERE code_hash = $1"
            " AND used_at IS NULL AND expires_at > now()"
            " RETURNING client_id, redirect_uri, code_challenge",
            token_hash(form.get("code", "")),
        )
        if row is None:
            raise OAuthError("invalid_grant", "code d'autorisation inconnu, expiré ou déjà utilisé")
        if row["client_id"] != form.get("client_id") or row["redirect_uri"] != form.get(
            "redirect_uri"
        ):
            raise OAuthError("invalid_grant", "client ou adresse de retour différents")
        if not _pkce_ok(form.get("code_verifier", ""), row["code_challenge"]):
            raise OAuthError("invalid_grant", "code_verifier PKCE invalide")
        if await db_get_active_client(conn, row["client_id"]) is None:
            raise OAuthError("invalid_client", "client révoqué")
        tokens = await _issue_tokens(conn, row["client_id"])
    log.info("jetons_emis", client_id=row["client_id"], voie="code")
    return tokens


async def _refresh(pool: asyncpg.Pool, form: dict[str, str]) -> dict[str, Any]:
    async with pool.acquire() as conn:
        # Rotation : l'ancien jeton de rafraîchissement est révoqué à l'usage (hors
        # transaction, comme le code), si bien qu'un jeton volé puis rejoué après le
        # client légitime est refusé.
        client_id = await conn.fetchval(
            "UPDATE oauth_tokens SET revoked_at = now() WHERE token_hash = $1"
            " AND kind = 'refresh' AND revoked_at IS NULL AND expires_at > now()"
            " RETURNING client_id",
            token_hash(form.get("refresh_token", "")),
        )
        if client_id is None or client_id != form.get("client_id"):
            raise OAuthError("invalid_grant", "jeton de rafraîchissement invalide")
        if await db_get_active_client(conn, client_id) is None:
            raise OAuthError("invalid_client", "client révoqué")
        tokens = await _issue_tokens(conn, client_id)
    log.info("jetons_emis", client_id=client_id, voie="rafraichissement")
    return tokens
