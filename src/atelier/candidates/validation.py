"""Validation d'une candidate : dire si elle est prête, et sinon ce qui manque et pourquoi.

Les règles vivent dans la table validation_rules. Ce module ne porte que les TYPES
d'évaluateurs (le « langage » des règles) ; aucune règle concrète n'est écrite ici.
La validation ne modifie jamais la candidate, et n'empêche jamais de l'enregistrer.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

import asyncpg
import structlog

from atelier.candidates import repository as repo

log = structlog.get_logger(__name__)

# Champs qu'une règle « field_required » peut viser, avec leur nom lisible.
REQUIRABLE_FIELDS: dict[str, str] = {
    "title": "Titre",
    "description": "Description",
    "brand": "Marque",
    "size": "Taille",
    "sale_price": "Prix de vente",
    "category_id": "Catégorie",
    "photo_prompt": "Prompt de génération de photo",
    "parcel_size": "Taille de colis",
}


@dataclass(frozen=True)
class Missing:
    rule: str  # code de la règle
    target: str  # champ ou objet concerné
    reason: str  # en clair, pour la personne comme pour un agent


@dataclass(frozen=True)
class ValidationReport:
    candidate_id: int
    ready: bool
    missing: list[Missing]

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "ready": self.ready,
            "missing": [asdict(m) for m in self.missing],
        }


@dataclass(frozen=True)
class _Snapshot:
    candidate: dict[str, Any]
    photo_count: int
    links: list[dict[str, Any]]


class RuleConfigError(ValueError):
    pass


def _field_required(snap: _Snapshot, params: dict[str, Any], message: str) -> Missing | None:
    field = params.get("field")
    if field not in REQUIRABLE_FIELDS:
        raise RuleConfigError(f"champ inconnu : {field!r}")
    value = snap.candidate.get(field)
    if value is None or (isinstance(value, str) and not value.strip()):
        return Missing("", field, message)
    return None


def _min_photos(snap: _Snapshot, params: dict[str, Any], message: str) -> Missing | None:
    minimum = params.get("min", 1)
    if not isinstance(minimum, int) or minimum < 0:
        raise RuleConfigError(f"minimum invalide : {minimum!r}")
    if snap.photo_count >= minimum:
        return None
    return Missing("", "photos", f"{message} ({snap.photo_count} sur {minimum} requise(s)).")


def _link_size_and_europe(snap: _Snapshot, params: dict[str, Any], message: str) -> Missing | None:
    # Une candidate sans lien produit un manque explicite, jamais une erreur technique.
    if not snap.links:
        return Missing("", "purchase_links", f"{message} Aucun lien d'achat n'est rattaché.")
    size = (snap.candidate.get("size") or "").strip().lower()
    if not size:
        return Missing("", "size", f"{message} La taille retenue n'est pas renseignée.")
    for link in snap.links:
        sizes = {s.strip().lower() for s in link["available_sizes"]}
        if link["ships_from_europe"] is True and size in sizes:
            return None
    return Missing("", "purchase_links", message)


_EVALUATORS: dict[str, Callable[[_Snapshot, dict[str, Any], str], Missing | None]] = {
    "field_required": _field_required,
    "min_photos": _min_photos,
    "link_size_and_europe": _link_size_and_europe,
}
RULE_KINDS = tuple(_EVALUATORS)


async def _db_snapshot(conn: repo._Conn, candidate_id: int) -> _Snapshot | None:
    row = await conn.fetchrow("SELECT * FROM candidates WHERE id = $1", candidate_id)
    if row is None:
        return None
    photos = await conn.fetchval(
        "SELECT count(*) FROM candidate_photos WHERE candidate_id = $1", candidate_id
    )
    links = await conn.fetch(
        "SELECT ships_from_europe, available_sizes FROM purchase_links WHERE candidate_id = $1",
        candidate_id,
    )
    return _Snapshot(dict(row), int(photos), [dict(link) for link in links])


async def db_list_rules(conn: repo._Conn, enabled_only: bool = False) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        "SELECT * FROM validation_rules WHERE NOT $1 OR enabled ORDER BY position, id",
        enabled_only,
    )
    return [{**dict(r), "params": _params(r["params"])} for r in rows]


def _params(raw: object) -> dict[str, Any]:
    value = json.loads(raw) if isinstance(raw, str) else raw
    return value if isinstance(value, dict) else {}


async def resolve_validate_candidate(
    pool: asyncpg.Pool, candidate_id: int
) -> ValidationReport | None:
    """Évalue TOUTES les règles actives (pas seulement jusqu'au premier échec).

    Rend None pour une candidate inexistante : à l'appelant de répondre proprement.
    """
    async with pool.acquire() as conn:
        snap = await _db_snapshot(conn, candidate_id)
        if snap is None:
            return None
        rules = await db_list_rules(conn, enabled_only=True)
    missing = [m for rule in rules if (m := _evaluate(rule, snap)) is not None]
    return ValidationReport(candidate_id, ready=not missing, missing=missing)


def _evaluate(rule: dict[str, Any], snap: _Snapshot) -> Missing | None:
    evaluator = _EVALUATORS.get(rule["kind"])
    try:
        if evaluator is None:
            raise RuleConfigError(f"type de règle inconnu : {rule['kind']!r}")
        found = evaluator(snap, rule["params"], rule["message"])
    except RuleConfigError as exc:
        # Une règle mal saisie en base ne fait pas tomber la validation : elle est
        # rendue comme un manque lisible, pour être vue et corrigée.
        log.warning("regle_mal_configuree", code=rule["code"], raison=str(exc))
        return Missing(rule["code"], "règle", f"Règle « {rule['code']} » mal configurée : {exc}.")
    return Missing(rule["code"], found.target, found.reason) if found else None


async def resolve_add_field_rule(pool: asyncpg.Pool, field: str, message: str) -> int:
    if field not in REQUIRABLE_FIELDS:
        raise ValueError(f"Champ inconnu : {field}")
    text = message.strip() or f"Le champ « {REQUIRABLE_FIELDS[field]} » est obligatoire."
    async with pool.acquire() as conn:
        rule_id = await conn.fetchval(
            "INSERT INTO validation_rules (code, kind, params, message, position)"
            " VALUES ($1, 'field_required', $2::jsonb, $3,"
            " (SELECT coalesce(max(position), 0) + 10 FROM validation_rules))"
            " ON CONFLICT (code) DO UPDATE SET enabled = true RETURNING id",
            f"champ_{field}_requis", json.dumps({"field": field}), text,
        )  # fmt: skip
    return int(rule_id)


async def db_set_rule_enabled(conn: repo._Conn, rule_id: int, enabled: bool) -> bool:
    done = await conn.fetchval(
        "UPDATE validation_rules SET enabled = $2 WHERE id = $1 RETURNING 1", rule_id, enabled
    )
    return done is not None
