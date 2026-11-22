"""Réglages des fournisseurs en base, et construction d'un client prêt à appeler."""

from __future__ import annotations

import re
from dataclasses import dataclass

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
MODEL_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


@dataclass(frozen=True)
class ProviderSettings:
    name: str
    label: str
    secret_ref: str
    text_model: str
    image_model: str


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    lines: list[str]  # une ligne par contrôle, montrable telle quelle


async def db_list_providers(conn: repo._Conn) -> list[ProviderSettings]:
    rows = await conn.fetch(
        "SELECT name, label, secret_ref, text_model, image_model FROM ai_providers ORDER BY name"
    )
    return [ProviderSettings(**dict(r)) for r in rows]


async def db_get_provider(conn: repo._Conn, name: str) -> ProviderSettings | None:
    row = await conn.fetchrow(
        "SELECT name, label, secret_ref, text_model, image_model FROM ai_providers WHERE name = $1",
        name,
    )
    return ProviderSettings(**dict(row)) if row else None


async def resolve_update_models(
    pool: asyncpg.Pool, name: str, text_model: str, image_model: str
) -> bool:
    text_model, image_model = text_model.strip(), image_model.strip()
    if not (MODEL_RE.fullmatch(text_model) and MODEL_RE.fullmatch(image_model)):
        raise ValueError("nom de modèle invalide (lettres, chiffres, . _ - uniquement)")
    done = await pool.fetchval(
        "UPDATE ai_providers SET text_model = $2, image_model = $3, updated_at = now()"
        " WHERE name = $1 RETURNING 1",
        name, text_model, image_model,
    )  # fmt: skip
    if done is not None:
        log.info(
            "modeles_fournisseur_modifies", fournisseur=name, texte=text_model, image=image_model
        )
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
    return _CLIENTS[settings.name](config, transport)


async def resolve_check_connection(
    pool: asyncpg.Pool, cipher: SecretCipher, settings: ProviderSettings,
    transport: httpx.AsyncBaseTransport | None = None,
) -> CheckResult:  # fmt: skip
    """Vérifie clef et modèles par des lectures de fiches de modèle : rien de facturé."""
    try:
        client = await resolve_client(pool, cipher, settings, transport)
    except ProviderError as exc:
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
    log.info("connexion_fournisseur_testee", fournisseur=settings.name, ok=ok)
    return CheckResult(ok, lines)
