"""Fiche d'une candidate : mise à jour champ par champ, liens d'achat, suppression."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import asyncpg
import structlog

from atelier.candidates import repository as repo
from atelier.candidates.fields import CANDIDATE_FIELDS, LINK_FIELDS, FieldSpec
from atelier.candidates.link_key import resolve_link_key
from atelier.images.photos import candidate_dir
from atelier.images.store import ImageStore

log = structlog.get_logger(__name__)


class NotFoundError(LookupError):
    pass


class LinkAlreadyAttachedError(Exception):
    def __init__(self, owner: repo.CandidateRef, same_candidate: bool) -> None:
        self.owner = owner
        self.same_candidate = same_candidate
        name = owner.title or "Sans titre"
        super().__init__(
            "Ce lien est déjà rattaché à cette candidate."
            if same_candidate
            else f"Ce lien est déjà rattaché à la candidate « {name} » (n° {owner.id})."
        )


@dataclass(frozen=True)
class Fiche:
    candidate: dict[str, Any]
    links: list[dict[str, Any]]


# Tables et colonnes viennent EXCLUSIVEMENT de ces constantes et des listes
# fermées de fields.py, jamais de la requête : c'est ce qui rend sûr d'interpoler
# l'identifiant SQL (un identifiant ne peut pas être passé en paramètre lié).
_EDITABLE = {"candidates": CANDIDATE_FIELDS, "purchase_links": LINK_FIELDS}


async def _db_update_field(
    conn: repo._Conn, table: str, row_id: int, spec: FieldSpec, value: object
) -> tuple[bool, object]:
    if spec.column not in _EDITABLE[table]:
        raise KeyError(spec.column)
    # Un seul UPDATE ... RETURNING : la valeur affichée est celle que la base a
    # retenue (normalisée, ou écrite par l'enregistrement concurrent gagnant), jamais
    # celle qui a été envoyée. Deux écritures du même champ ne se mélangent pas.
    sql = (
        f"UPDATE {table} SET {spec.column} = $1, updated_at = now()"  # noqa: S608 — liste blanche
        f" WHERE id = $2 RETURNING {spec.column}"
    )
    row = await conn.fetchrow(sql, value, row_id)
    return (row is not None, row[spec.column] if row else None)


async def resolve_update_field(
    pool: asyncpg.Pool, table: str, row_id: int, name: str, raw: str
) -> object:
    """Valide puis enregistre un seul attribut ; rend la valeur réellement stockée.

    Lève KeyError (attribut hors liste), FieldValueError (valeur refusée) ou
    NotFoundError.
    """
    spec = _EDITABLE[table][name]
    value = spec.parse(raw)
    async with pool.acquire() as conn:
        found, stored = await _db_update_field(conn, table, row_id, spec, value)
    if not found:
        raise NotFoundError(row_id)
    return stored


async def db_get_fiche(conn: repo._Conn, candidate_id: int) -> Fiche | None:
    row = await conn.fetchrow("SELECT * FROM candidates WHERE id = $1", candidate_id)
    if row is None:
        return None
    links = await conn.fetch(
        "SELECT l.*, s.name AS site_name FROM purchase_links l"
        " LEFT JOIN purchase_sites s ON s.id = l.site_id"
        " WHERE l.candidate_id = $1 ORDER BY l.purchase_price NULLS LAST, l.id",
        candidate_id,
    )
    return Fiche(dict(row), [dict(link) for link in links])


async def resolve_add_link(pool: asyncpg.Pool, candidate_id: int, raw_url: str) -> int:
    """Rattache un lien à une candidate existante ; rend l'identifiant du lien."""
    async with pool.acquire() as conn:
        key = await resolve_link_key(conn, raw_url)
        try:
            link_id = await conn.fetchval(
                "INSERT INTO purchase_links (candidate_id, url, normalized_url, site_id)"
                " VALUES ($1, $2, $3, $4) ON CONFLICT (normalized_url) DO NOTHING RETURNING id",
                candidate_id, key.url.original, key.url.normalized, key.site_id,
            )  # fmt: skip
        except asyncpg.ForeignKeyViolationError:
            raise NotFoundError(candidate_id) from None
        if link_id is None:
            owner = await repo.db_find_candidate_by_link(conn, key.url.normalized)
            if owner is not None:
                raise LinkAlreadyAttachedError(owner, same_candidate=owner.id == candidate_id)
            raise RuntimeError(f"lien en conflit introuvable : {key.url.normalized}")
    log.info("lien_ajoute", candidate_id=candidate_id, link_id=link_id)
    return int(link_id)


async def resolve_remove_link(pool: asyncpg.Pool, link_id: int) -> int:
    """Retire un lien (même le dernier : la candidate subsiste) ; rend sa candidate."""
    async with pool.acquire() as conn:
        candidate_id = await conn.fetchval(
            "DELETE FROM purchase_links WHERE id = $1 RETURNING candidate_id", link_id
        )
    if candidate_id is None:
        raise NotFoundError(link_id)
    log.info("lien_retire", candidate_id=candidate_id, link_id=link_id)
    return int(candidate_id)


async def resolve_delete_candidate(
    pool: asyncpg.Pool, store: ImageStore, candidate_id: int
) -> None:
    """Supprime la candidate, ses liens et ses photos, fichiers compris."""
    async with pool.acquire() as conn, conn.transaction():
        paths = [
            r["file_path"]
            for r in await conn.fetch(
                "DELETE FROM candidate_photos WHERE candidate_id = $1 RETURNING file_path",
                candidate_id,
            )
        ]
        deleted = await conn.fetchval(
            "DELETE FROM candidates WHERE id = $1 RETURNING id", candidate_id
        )
        if deleted is None:
            raise NotFoundError(candidate_id)
    # Fichiers supprimés APRÈS la validation de la transaction : si elle avait
    # échoué, les lignes seraient restées et pointeraient vers des fichiers disparus.
    await asyncio.to_thread(_delete_files, store, paths, candidate_dir(candidate_id))
    log.info("candidate_supprimee", candidate_id=candidate_id, photos=len(paths))


def _delete_files(store: ImageStore, paths: list[str], directory: str) -> None:
    for path in paths:
        store.file_delete(path)
    try:
        store.resolve(directory).rmdir()
    except OSError:
        # Répertoire absent ou non vide (fichier étranger) : on le laisse, sans bruit.
        pass
