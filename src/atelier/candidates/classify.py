"""Classer une candidate dans l'arbre, et appliquer les défauts de sa catégorie."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import asyncpg
import structlog

from atelier.candidates import repository as repo
from atelier.candidates.categories import CategoryNotFoundError, db_effective_defaults
from atelier.candidates.fields import CATEGORY_DEFAULTS_TO_CANDIDATE

log = structlog.get_logger(__name__)


class CandidateNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class ClassifyResult:
    applied: dict[str, Any]  # champs remplis automatiquement (ils étaient vides)
    proposal: dict[str, Any]  # nouveaux défauts proposés, en attente de confirmation


async def _db_candidate_for_update(conn: repo._Conn, candidate_id: int) -> asyncpg.Record:
    row = await conn.fetchrow(
        "SELECT category_id, description, photo_prompt, parcel_size FROM candidates"
        " WHERE id = $1 FOR UPDATE",
        candidate_id,
    )
    if row is None:
        raise CandidateNotFoundError(candidate_id)
    return row


async def _db_write_fields(conn: repo._Conn, candidate_id: int, values: dict[str, Any]) -> None:
    for column, value in values.items():
        # Colonnes issues de CATEGORY_DEFAULTS_TO_CANDIDATE, liste fermée : sûr à interpoler.
        await conn.execute(
            f"UPDATE candidates SET {column} = $1, updated_at = now() WHERE id = $2",  # noqa: S608
            value,
            candidate_id,
        )


def _candidate_values(defaults: dict[str, Any]) -> dict[str, Any]:
    return {
        CATEGORY_DEFAULTS_TO_CANDIDATE[name]: value
        for name, value in defaults.items()
        if value is not None
    }


async def resolve_classify_candidate(
    pool: asyncpg.Pool, candidate_id: int, category_id: int | None
) -> ClassifyResult:
    async with pool.acquire() as conn, conn.transaction():
        current = await _db_candidate_for_update(conn, candidate_id)
        try:
            await conn.execute(
                "UPDATE candidates SET category_id = $2, updated_at = now() WHERE id = $1",
                candidate_id, category_id,
            )  # fmt: skip
        except asyncpg.ForeignKeyViolationError:
            raise CategoryNotFoundError(category_id) from None
        if category_id is None or category_id == current["category_id"]:
            return ClassifyResult({}, {})
        wanted = _candidate_values(await db_effective_defaults(conn, category_id))
        if current["category_id"] is None:
            # Premier classement : les défauts remplissent les champs encore vides,
            # et ne touchent à aucun champ déjà renseigné.
            applied = {col: v for col, v in wanted.items() if current[col] is None}
            await _db_write_fields(conn, candidate_id, applied)
            log.info("candidate_classee", candidate_id=candidate_id, champs=sorted(applied))
            return ClassifyResult(applied, {})
    # Changement de catégorie : rien n'est réécrit tout seul. On propose les valeurs
    # qui changeraient, et l'écran attend une confirmation explicite.
    proposal = {col: v for col, v in wanted.items() if current[col] != v}
    log.info("candidate_reclassee", candidate_id=candidate_id, propositions=sorted(proposal))
    return ClassifyResult({}, proposal)


async def resolve_apply_category_defaults(pool: asyncpg.Pool, candidate_id: int) -> dict[str, Any]:
    """Applique (après confirmation) les défauts de la catégorie actuelle, en écrasant."""
    async with pool.acquire() as conn, conn.transaction():
        current = await _db_candidate_for_update(conn, candidate_id)
        if current["category_id"] is None:
            return {}
        values = _candidate_values(await db_effective_defaults(conn, current["category_id"]))
        await _db_write_fields(conn, candidate_id, values)
    log.info("defauts_appliques", candidate_id=candidate_id, champs=sorted(values))
    return values
