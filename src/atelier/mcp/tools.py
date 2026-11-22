"""Outils MCP de l'atelier — lecture seule à ce stade.

Découpés par décision (lister, lire, valider), jamais un outil fourre-tout. Aucun ne
transporte de binaire : une photo est rendue par son URL.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import asyncpg

from atelier.candidates import repository as repo
from atelier.candidates.fiche import db_get_fiche
from atelier.candidates.validation import resolve_validate_candidate
from atelier.images.repository import db_list_candidate_photos
from atelier.images.store import ImageStore


class ToolInputError(ValueError):
    """Argument refusé : rendu à l'agent comme un résultat d'erreur lisible."""


@dataclass(frozen=True)
class ToolContext:
    pool: asyncpg.Pool
    store: ImageStore


Handler = Callable[[ToolContext, dict[str, Any]], Awaitable[Any]]

_ID = {"type": "integer", "minimum": 1, "description": "Identifiant de la candidate."}


def _int(args: dict[str, Any], name: str, default: int | None, low: int, high: int) -> int | None:
    value = args.get(name, default)
    if value is None:
        return None
    # bool est un int en Python : on l'exclut, True ne doit pas valoir 1.
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ToolInputError(f"« {name} » doit être un entier entre {low} et {high}")
    return value


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


async def _list_candidates(ctx: ToolContext, args: dict[str, Any]) -> Any:
    limit = _int(args, "limit", 20, 1, 100)
    before = _int(args, "before_id", None, 1, 2**62)
    async with ctx.pool.acquire() as conn:
        rows = await repo.db_list_candidates(conn, before, limit or 20)
    return {
        "candidates": [
            {
                "id": r.id,
                "title": r.title,
                "status": r.status,
                "category": r.category_label,
                "links": r.link_count,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ],
        "next_before_id": rows[-1].id if len(rows) == limit else None,
    }


async def _get_candidate(ctx: ToolContext, args: dict[str, Any]) -> Any:
    cid = _int(args, "candidate_id", None, 1, 2**62)
    if cid is None:
        raise ToolInputError("« candidate_id » est obligatoire")
    async with ctx.pool.acquire() as conn:
        fiche = await db_get_fiche(conn, cid)
        photos = await db_list_candidate_photos(conn, cid) if fiche else []
    if fiche is None:
        raise ToolInputError(f"candidate n° {cid} introuvable")
    return {
        "candidate": {k: _jsonable(v) for k, v in fiche.candidate.items()},
        "purchase_links": [{k: _jsonable(v) for k, v in link.items()} for link in fiche.links],
        "photos": [
            {
                "id": p.id,
                "kind": p.kind,
                "url": ctx.store.url_for(p.file_path),
                "width": p.width,
                "height": p.height,
            }
            for p in photos
        ],
    }


async def _validate_candidate(ctx: ToolContext, args: dict[str, Any]) -> Any:
    cid = _int(args, "candidate_id", None, 1, 2**62)
    if cid is None:
        raise ToolInputError("« candidate_id » est obligatoire")
    report = await resolve_validate_candidate(ctx.pool, cid)
    if report is None:
        raise ToolInputError(f"candidate n° {cid} introuvable")
    # Même rapport que l'écran et l'API : un seul évaluateur, jamais une copie.
    return report.as_dict()


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    schema: dict[str, Any]
    handler: Handler

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name, "description": self.description, "inputSchema": self.schema,
            "annotations": {"readOnlyHint": True},
        }  # fmt: skip


def _schema(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required,
            "additionalProperties": False}  # fmt: skip


TOOLS: dict[str, Tool] = {
    t.name: t
    for t in (
        Tool(
            "list_candidates",
            "Liste les candidates (articles repérés en vue d'une annonce), des plus récentes"
            " aux plus anciennes. Pour la page suivante, repasser next_before_id en before_id.",
            _schema(
                {
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
                    "before_id": {"type": "integer", "minimum": 1},
                },
                [],
            ),
            _list_candidates,
        ),
        Tool(
            "get_candidate",
            "Lit la fiche complète d'une candidate : attributs de vente, liens d'achat triés"
            " par prix, et photos (par URL, jamais en binaire).",
            _schema({"candidate_id": _ID}, ["candidate_id"]),
            _get_candidate,
        ),
        Tool(
            "validate_candidate",
            "Dit si une candidate est prête à devenir une annonce ; sinon liste chaque manque"
            " avec la règle en cause, le champ visé et la raison. Ne modifie rien.",
            _schema({"candidate_id": _ID}, ["candidate_id"]),
            _validate_candidate,
        ),
    )
}


def check_known_args(tool: Tool, args: dict[str, Any]) -> None:
    unknown = sorted(set(args) - set(tool.schema["properties"]))
    if unknown:
        raise ToolInputError(f"argument(s) inconnu(s) : {', '.join(unknown)}")
