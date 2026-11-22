"""Catégories et secrets du coffre : écriture, liste sans valeurs, lecture par référence."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

import asyncpg
import structlog
from pydantic import SecretStr

from atelier.candidates import repository as repo
from atelier.vault.cipher import SecretCipher

log = structlog.get_logger(__name__)

SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
MAX_VALUE_CHARS = 10_000
MAX_LABEL_CHARS = 100


class VaultError(ValueError):
    """Erreur de saisie, au message montrable tel quel."""


class VaultNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class SecretInfo:
    # Volontairement SANS valeur : c'est ce qui circule vers les pages.
    id: int
    category_id: int
    ref: str
    name: str
    description: str | None
    updated_at: datetime
    last_used_at: datetime | None


@dataclass(frozen=True)
class CategoryInfo:
    id: int
    slug: str
    label: str
    description: str | None
    secrets: list[SecretInfo]


def _label(raw: str) -> str:
    label = raw.strip()
    if not 1 <= len(label) <= MAX_LABEL_CHARS:
        raise VaultError(f"le libellé doit faire de 1 à {MAX_LABEL_CHARS} caractères")
    return label


def _value(raw: str) -> str:
    # Les clefs collées depuis une console arrivent souvent avec un retour à la ligne :
    # un espace en bord n'appartient à aucune clef d'API, on le retire.
    value = raw.strip()
    if not value:
        raise VaultError("la valeur est vide")
    if len(value) > MAX_VALUE_CHARS:
        raise VaultError(f"la valeur dépasse {MAX_VALUE_CHARS} caractères")
    return value


def _check(pattern: re.Pattern[str], raw: str, what: str) -> str:
    value = raw.strip()
    if not pattern.fullmatch(value):
        raise VaultError(
            f"{what} invalide : minuscules, chiffres et _ uniquement, en commençant par une lettre"
        )
    return value


async def db_list_vault(conn: repo._Conn) -> list[CategoryInfo]:
    cats = await conn.fetch("SELECT * FROM secret_categories ORDER BY position, label, id")
    rows = await conn.fetch(
        "SELECT s.id, s.category_id, s.name, s.description, s.updated_at, s.last_used_at,"
        " c.slug FROM secrets s JOIN secret_categories c ON c.id = s.category_id"
        " ORDER BY s.name"
    )  # fmt: skip
    by_cat: dict[int, list[SecretInfo]] = {}
    for r in rows:
        by_cat.setdefault(r["category_id"], []).append(
            SecretInfo(r["id"], r["category_id"], f"{r['slug']}/{r['name']}", r["name"],
                       r["description"], r["updated_at"], r["last_used_at"])
        )  # fmt: skip
    return [
        CategoryInfo(c["id"], c["slug"], c["label"], c["description"], by_cat.get(c["id"], []))
        for c in cats
    ]


async def resolve_create_category(pool: asyncpg.Pool, slug: str, label: str) -> int:
    clean_slug = _check(SLUG_RE, slug, "identifiant de catégorie")
    try:
        new_id = await pool.fetchval(
            "INSERT INTO secret_categories (slug, label, position) VALUES ($1, $2,"
            " (SELECT coalesce(max(position), 0) + 10 FROM secret_categories)) RETURNING id",
            clean_slug, _label(label),
        )  # fmt: skip
    except asyncpg.UniqueViolationError:
        raise VaultError(f"la catégorie « {clean_slug} » existe déjà") from None
    log.info("categorie_secret_creee", categorie=clean_slug)
    return int(new_id)


async def resolve_rename_category(pool: asyncpg.Pool, category_id: int, label: str) -> None:
    done = await pool.fetchval(
        "UPDATE secret_categories SET label = $2 WHERE id = $1 RETURNING 1",
        category_id, _label(label),
    )  # fmt: skip
    if done is None:
        raise VaultNotFoundError(category_id)


async def resolve_delete_category(pool: asyncpg.Pool, category_id: int) -> None:
    async with pool.acquire() as conn, conn.transaction():
        count = await conn.fetchval(
            "SELECT count(*) FROM secrets WHERE category_id = $1", category_id
        )
        if count:
            raise VaultError(
                f"la catégorie contient encore {count} secret(s) : supprimez-les d'abord"
            )
        done = await conn.fetchval(
            "DELETE FROM secret_categories WHERE id = $1 RETURNING slug", category_id
        )
    if done is None:
        raise VaultNotFoundError(category_id)
    log.info("categorie_secret_supprimee", categorie=done)


async def resolve_add_secret(
    pool: asyncpg.Pool, cipher: SecretCipher, category_id: int, name: str, value: str,
    description: str = "",
) -> int:  # fmt: skip
    clean_name = _check(NAME_RE, name, "nom du secret")
    clean_value = _value(value)
    async with pool.acquire() as conn, conn.transaction():
        slug = await conn.fetchval("SELECT slug FROM secret_categories WHERE id = $1", category_id)
        if slug is None:
            raise VaultNotFoundError(category_id)
        secret_id = int(await conn.fetchval("SELECT nextval('secrets_id_seq')"))
        try:
            await conn.execute(
                "INSERT INTO secrets (id, category_id, name, description, ciphertext)"
                " VALUES ($1, $2, $3, $4, $5)",
                secret_id, category_id, clean_name, description.strip() or None,
                cipher.encrypt(clean_value, secret_id),
            )  # fmt: skip
        except asyncpg.UniqueViolationError:
            raise VaultError(f"« {slug}/{clean_name} » existe déjà : remplacez sa valeur") from None
    # La référence, jamais la valeur : c'est tout ce qu'un journal a le droit de porter.
    log.info("secret_enregistre", ref=f"{slug}/{clean_name}")
    return secret_id


async def resolve_replace_secret_value(
    pool: asyncpg.Pool, cipher: SecretCipher, secret_id: int, value: str
) -> None:
    ref = await pool.fetchval(
        "UPDATE secrets s SET ciphertext = $2, updated_at = now() FROM secret_categories c"
        " WHERE s.id = $1 AND c.id = s.category_id RETURNING c.slug || '/' || s.name",
        secret_id, cipher.encrypt(_value(value), secret_id),
    )  # fmt: skip
    if ref is None:
        raise VaultNotFoundError(secret_id)
    log.info("secret_remplace", ref=ref)


async def resolve_delete_secret(pool: asyncpg.Pool, secret_id: int) -> None:
    ref = await pool.fetchval(
        "DELETE FROM secrets s USING secret_categories c"
        " WHERE s.id = $1 AND c.id = s.category_id RETURNING c.slug || '/' || s.name",
        secret_id,
    )
    if ref is None:
        raise VaultNotFoundError(secret_id)
    log.info("secret_supprime", ref=ref)


async def resolve_secret_value(pool: asyncpg.Pool, cipher: SecretCipher, ref: str) -> SecretStr:
    """Point d'injection : seul endroit où une valeur sort du coffre.

    Rendue en SecretStr, pour qu'un repr ou un journal accidentel n'affiche que des
    étoiles ; l'appelant ne la déballe qu'au moment de l'appel au fournisseur.
    Une référence inconnue lève une erreur : une clef vide envoyée au fournisseur
    donnerait un refus d'authentification trompeur, loin de la cause.
    """
    slug, _, name = ref.partition("/")
    if not (SLUG_RE.fullmatch(slug) and NAME_RE.fullmatch(name)):
        raise VaultError(f"référence de secret invalide : {ref!r} (attendu « categorie/nom »)")
    row = await pool.fetchrow(
        "UPDATE secrets s SET last_used_at = now() FROM secret_categories c"
        " WHERE c.id = s.category_id AND c.slug = $1 AND s.name = $2"
        " RETURNING s.id, s.ciphertext",
        slug, name,
    )  # fmt: skip
    if row is None:
        raise VaultNotFoundError(f"secret « {ref} » absent du coffre")
    return SecretStr(cipher.decrypt(bytes(row["ciphertext"]), row["id"]))
