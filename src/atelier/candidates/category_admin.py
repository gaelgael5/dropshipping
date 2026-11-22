"""Écran d'administration des catégories : liste enrichie et enregistrement d'un formulaire.

Séparé de categories.py (structure de l'arbre et héritage) : ici, seulement ce que
l'écran affiche et la saisie en bloc d'une catégorie depuis sa fenêtre.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import asyncpg
import structlog

from atelier.candidates import categories as cats
from atelier.candidates import repository as repo
from atelier.candidates.fields import CATEGORY_FIELDS, FieldValueError

log = structlog.get_logger(__name__)

# Champs saisis dans la fenêtre, dans l'ordre d'affichage. Les colonnes viennent de
# CATEGORY_FIELDS (liste blanche) : aucun nom de colonne ne vient de la requête.
FORM_FIELDS = ("label", "position", "photo_prompt_template", "description_template", "parcel_size")
DEFAULT_FIELDS = ("photo_prompt_template", "description_template", "parcel_size")


@dataclass(frozen=True)
class CategoryRow:
    id: int
    parent_id: int | None
    label: str
    depth: int
    own_defaults: dict[str, Any]  # valeurs posées sur la catégorie elle-même (None = héritée)
    candidate_count: int
    child_count: int
    mappings: list[dict[str, Any]] = field(default_factory=list)


async def db_list_category_rows(conn: repo._Conn) -> list[CategoryRow]:
    rows = await cats.db_list_categories(conn)
    by_id = {r["id"]: r for r in rows}
    counts = {
        r["category_id"]: r["n"]
        for r in await conn.fetch(
            "SELECT category_id, count(*) AS n FROM candidates"
            " WHERE category_id IS NOT NULL GROUP BY category_id"
        )
    }
    mappings: dict[int, list[dict[str, Any]]] = {}
    for m in await conn.fetch(
        "SELECT category_id, platform, platform_category_id FROM category_platform_mappings"
        " ORDER BY platform"
    ):
        mappings.setdefault(m["category_id"], []).append(dict(m))
    return [
        CategoryRow(
            node.id,
            node.parent_id,
            node.label,
            node.depth,
            {name: by_id[node.id][name] for name in DEFAULT_FIELDS},
            counts.get(node.id, 0),
            len(node.children),
            mappings.get(node.id, []),
        )
        for node in cats.flatten(cats.build_tree(rows))
    ]


def descendants_of(rows: list[CategoryRow], category_id: int) -> set[int]:
    """La catégorie et ses descendants : parents interdits pour un déplacement."""
    found, changed = {category_id}, True
    while changed:  # itératif, l'arbre est borné par MAX_DEPTH
        changed = False
        for row in rows:
            if row.parent_id in found and row.id not in found:
                found.add(row.id)
                changed = True
    return found


def parse_category_form(form: dict[str, str]) -> dict[str, object]:
    """Valide TOUS les champs avant d'écrire quoi que ce soit ; nomme le champ fautif."""
    values: dict[str, object] = {}
    for name in FORM_FIELDS:
        spec = CATEGORY_FIELDS[name]
        raw = form.get(name, "")
        if name == "position" and not raw.strip():
            raw = "0"
        try:
            values[spec.column] = spec.parse(raw)
        except FieldValueError as exc:
            raise FieldValueError(f"{spec.label} : {exc}") from None
    return values


async def resolve_create_category(
    pool: asyncpg.Pool, parent_id: int | None, form: dict[str, str]
) -> int:
    values = parse_category_form(form)
    columns = ", ".join(values)  # noms issus de CATEGORY_FIELDS, jamais de la requête
    params = ", ".join(f"${i}" for i in range(2, len(values) + 2))
    try:
        new_id = await pool.fetchval(
            f"INSERT INTO categories (parent_id, {columns}) VALUES ($1, {params})"  # noqa: S608
            " RETURNING id",
            parent_id, *values.values(),
        )  # fmt: skip
    except asyncpg.ForeignKeyViolationError:
        raise cats.CategoryNotFoundError(parent_id) from None
    log.info("categorie_creee", category_id=new_id, parent_id=parent_id)
    return int(new_id)


async def resolve_update_category(
    pool: asyncpg.Pool, category_id: int, parent_id: int | None, form: dict[str, str]
) -> None:
    values = parse_category_form(form)  # validé d'abord : un champ refusé n'écrit rien
    async with pool.acquire() as conn:
        current = await cats.db_get_category(conn, category_id)
    if current is None:
        raise cats.CategoryNotFoundError(category_id)
    if current["parent_id"] != parent_id:
        # Le déplacement passe par le contrôle de boucle et le verrou de l'arbre.
        await cats.resolve_move_category(pool, category_id, parent_id)
    sets = ", ".join(f"{col} = ${i}" for i, col in enumerate(values, start=2))
    await pool.execute(
        f"UPDATE categories SET {sets}, updated_at = now() WHERE id = $1",  # noqa: S608
        category_id, *values.values(),
    )  # fmt: skip
    log.info("categorie_modifiee", category_id=category_id)
