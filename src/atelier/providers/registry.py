"""Réglages des fournisseurs en base, et construction d'un client prêt à appeler."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

import asyncpg
import httpx
import structlog

from atelier.candidates import repository as repo
from atelier.providers.base import ProviderClient, ProviderConfig
from atelier.providers.errors import ErrorKind, ProviderError
from atelier.providers.gemini_client import GeminiClient
from atelier.providers.openai_client import OpenAIClient
from atelier.vault import service as vault
from atelier.vault.cipher import SecretCipher, SecretDecryptError

log = structlog.get_logger(__name__)

_CLIENTS: dict[str, type[ProviderClient]] = {"openai": OpenAIClient, "gemini": GeminiClient}
KINDS: dict[str, str] = {"gemini": "Google Gemini", "openai": "OpenAI (ChatGPT)"}
# Modèles proposés à la création ; réglables ensuite, les noms changent vite.
DEFAULT_MODELS: dict[str, tuple[str, str]] = {
    "gemini": ("gemini-3.6-flash", "gemini-nano-banana-2.1"),
    "openai": ("gpt-5-mini", "gpt-image-1.5"),
}
MODEL_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
REF_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}/[a-z][a-z0-9_]{0,63}$")
_COLUMNS = (
    "SELECT name, kind, label, secret_ref, text_model, image_model, last_check_at,"
    " last_check_ok FROM ai_providers"
)


class ProviderInputError(ValueError):
    """Saisie refusée, au message montrable tel quel."""


@dataclass(frozen=True)
class ProviderSettings:
    name: str
    kind: str
    label: str
    secret_ref: str
    text_model: str
    image_model: str
    last_check_at: datetime | None = None
    last_check_ok: bool | None = None


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    lines: list[str]  # une ligne par contrôle, montrable telle quelle


async def db_list_providers(conn: repo._Conn) -> list[ProviderSettings]:
    rows = await conn.fetch(f"{_COLUMNS} ORDER BY label, name")
    return [ProviderSettings(**dict(r)) for r in rows]


async def db_get_provider(conn: repo._Conn, name: str) -> ProviderSettings | None:
    row = await conn.fetchrow(f"{_COLUMNS} WHERE name = $1", name)
    return ProviderSettings(**dict(row)) if row else None


def _clean(
    kind: str, label: str, secret_ref: str, text_model: str, image_model: str
) -> tuple[str, ...]:
    if kind not in KINDS:
        raise ProviderInputError(f"type de fournisseur inconnu : {kind}")
    label, secret_ref = label.strip(), secret_ref.strip()
    text_model, image_model = text_model.strip(), image_model.strip()
    if not 1 <= len(label) <= 100:
        raise ProviderInputError("le libellé doit faire de 1 à 100 caractères")
    if not REF_RE.fullmatch(secret_ref):
        raise ProviderInputError("choisissez la clef dans le coffre (référence catégorie/nom)")
    if not (MODEL_RE.fullmatch(text_model) and MODEL_RE.fullmatch(image_model)):
        raise ProviderInputError("nom de modèle invalide (lettres, chiffres, . _ - uniquement)")
    return kind, label, secret_ref, text_model, image_model


async def resolve_create_provider(
    pool: asyncpg.Pool, name: str, kind: str, label: str, secret_ref: str,
    text_model: str, image_model: str,
) -> None:  # fmt: skip
    clean_name = name.strip()
    if not NAME_RE.fullmatch(clean_name):
        raise ProviderInputError(
            "identifiant invalide : minuscules, chiffres et _ uniquement,"
            " en commençant par une lettre"
        )
    values = _clean(kind, label, secret_ref, text_model, image_model)
    try:
        await pool.execute(
            "INSERT INTO ai_providers (name, kind, label, secret_ref, text_model, image_model)"
            " VALUES ($1, $2, $3, $4, $5, $6)",
            clean_name,
            *values,
        )
    except asyncpg.UniqueViolationError:
        raise ProviderInputError(f"l'identifiant « {clean_name} » est déjà pris") from None
    log.info("fournisseur_ajoute", fournisseur=clean_name, type=kind)


async def resolve_update_provider(
    pool: asyncpg.Pool, name: str, kind: str, label: str, secret_ref: str,
    text_model: str, image_model: str,
) -> bool:  # fmt: skip
    values = _clean(kind, label, secret_ref, text_model, image_model)
    # Le résultat du dernier test est effacé : il portait sur l'ancienne configuration.
    done = await pool.fetchval(
        "UPDATE ai_providers SET kind = $2, label = $3, secret_ref = $4, text_model = $5,"
        " image_model = $6, updated_at = now(), last_check_at = NULL, last_check_ok = NULL"
        " WHERE name = $1 RETURNING 1",
        name,
        *values,
    )
    if done is not None:
        log.info("fournisseur_modifie", fournisseur=name)
    return done is not None


async def resolve_delete_provider(pool: asyncpg.Pool, name: str) -> bool:
    done = await pool.fetchval("DELETE FROM ai_providers WHERE name = $1 RETURNING 1", name)
    if done is not None:
        log.info("fournisseur_supprime", fournisseur=name)
    return done is not None


async def resolve_client(
    pool: asyncpg.Pool, cipher: SecretCipher, settings: ProviderSettings,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ProviderClient:  # fmt: skip
    try:
        key = await vault.resolve_secret_value(pool, cipher, settings.secret_ref)
    except vault.VaultNotFoundError:
        raise ProviderError(
            settings.name, ErrorKind.MISSING_KEY,
            f"clef absente du coffre : ajoutez « {settings.secret_ref} » sur la page Secrets",
        ) from None  # fmt: skip
    except SecretDecryptError as exc:
        raise ProviderError(settings.name, ErrorKind.MISSING_KEY, str(exc)) from None
    config = ProviderConfig(settings.name, key, settings.text_model, settings.image_model)
    return _CLIENTS[settings.kind](config, transport)


async def resolve_check_connection(
    pool: asyncpg.Pool, cipher: SecretCipher, settings: ProviderSettings,
    transport: httpx.AsyncBaseTransport | None = None,
) -> CheckResult:  # fmt: skip
    """Vérifie clef et modèles par des lectures de fiches de modèle : rien de facturé."""
    try:
        client = await resolve_client(pool, cipher, settings, transport)
    except ProviderError as exc:
        await pool.execute(
            "UPDATE ai_providers SET last_check_at = now(), last_check_ok = false WHERE name = $1",
            settings.name,
        )
        return CheckResult(False, [exc.detail])
    lines, ok = [], True
    for usage, model in (("texte", settings.text_model), ("image", settings.image_model)):
        try:
            await client.check_model(model)
            lines.append(f"Modèle {usage} « {model} » : accessible.")
        except ProviderError as exc:
            ok = False
            lines.append(f"Modèle {usage} « {model} » : {exc.kind.value} — {exc.detail}")
            if exc.kind is ErrorKind.AUTH:
                break  # clef refusée : inutile de tester le second modèle
    await pool.execute(
        "UPDATE ai_providers SET last_check_at = now(), last_check_ok = $2 WHERE name = $1",
        settings.name, ok,
    )  # fmt: skip
    log.info("connexion_fournisseur_testee", fournisseur=settings.name, ok=ok)
    return CheckResult(ok, lines)
