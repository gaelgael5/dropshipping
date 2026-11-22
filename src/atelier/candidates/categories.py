"""Arbre de catégories : structure, héritage des défauts, classement des candidates."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import asyncpg
import structlog

from atelier.candidates import repository as repo
from atelier.candidates.fields import CATEGORY_DEFAULTS_TO_CANDIDATE, CATEGORY_FIELDS

log = structlog.get_logger(__name__)

# Plafond de profondeur : l'arbre est censé rester à trois ou quatre niveaux. Au-delà,
# c'est une donnée anormale ; on le dit au lieu de parcourir sans fin.
MAX_DEPTH = 20
# Toutes les modifications de structure prennent ce verrou : deux déplacements
# simultanés (A sous B, B sous A) passeraient chacun le contrôle de cycle seuls.
_TREE_LOCK_KEY = 0x43_41_54_53  # "CATS"


class CategoryNotFoundError(LookupError):
    pass


class CategoryCycleError(ValueError):
    pass


class CategoryTooDeepError(ValueError):
    pass


class CategoryInUseError(ValueError):
    def __init__(self, children: int, candidates: int) -> None:
        self.children, self.candidates = children, candidates
        parts = [f"{children} sous-catégorie(s)"] if children else []
        parts += [f"{candidates} candidate(s)"] if candidates else []
        super().__init__("Suppression impossible : la catégorie porte " + " et ".join(parts) + ".")


@dataclass
class CategoryNode:
    id: int
    parent_id: int | None
    label: str
    position: int
    depth: int = 0
    children: list[CategoryNode] = field(default_factory=list)


def build_tree(rows: list[dict[str, Any]]) -> list[CategoryNode]:
    """Construit l'arbre trié (ordre, puis libellé) ; parcours itératif, profondeur bornée."""
    nodes = {
        r["id"]: CategoryNode(r["id"], r["parent_id"], r["label"], r["position"]) for r in rows
    }
    roots: list[CategoryNode] = []
    for node in nodes.values():
        parent = nodes.get(node.parent_id) if node.parent_id is not None else None
        (parent.children if parent else roots).append(node)
    stack = [(root, 0) for root in roots]
    while stack:
        node, depth = stack.pop()
        if depth > MAX_DEPTH:
            raise CategoryTooDeepError(f"Arbre plus profond que {MAX_DEPTH} niveaux.")
        node.depth = depth
        node.children.sort(key=lambda n: (n.position, n.label.lower()))
        stack.extend((child, depth + 1) for child in node.children)
    roots.sort(key=lambda n: (n.position, n.label.lower()))
    return roots


def flatten(roots: list[CategoryNode]) -> list[CategoryNode]:
    """Ordre d'affichage en profondeur d'abord (pour un menu déroulant indenté)."""
    out: list[CategoryNode] = []
    stack = list(reversed(roots))
    while stack:
        node = stack.pop()
        out.append(node)
        stack.extend(reversed(node.children))
    return out


async def db_list_categories(conn: repo._Conn) -> list[dict[str, Any]]:
    return [dict(r) for r in await conn.fetch("SELECT * FROM categories")]


async def db_get_category(conn: repo._Conn, category_id: int) -> dict[str, Any] | None:
    row = await conn.fetchrow("SELECT * FROM categories WHERE id = $1", category_id)
    return dict(row) if row else None


async def resolve_create_category(pool: asyncpg.Pool, label: str, parent_id: int | None) -> int:
    clean = CATEGORY_FIELDS["label"].parse(label)
    async with pool.acquire() as conn:
        try:
            new_id = await conn.fetchval(
                "INSERT INTO categories (label, parent_id) VALUES ($1, $2) RETURNING id",
                clean, parent_id,
            )  # fmt: skip
        except asyncpg.ForeignKeyViolationError:
            raise CategoryNotFoundError(parent_id) from None
    log.info("categorie_creee", category_id=new_id, parent_id=parent_id)
    return int(new_id)


async def resolve_move_category(
    pool: asyncpg.Pool, category_id: int, new_parent_id: int | None
) -> None:
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock($1)", _TREE_LOCK_KEY)
        if await db_get_category(conn, category_id) is None:
            raise CategoryNotFoundError(category_id)
        if new_parent_id is not None:
            if await db_get_category(conn, new_parent_id) is None:
                raise CategoryNotFoundError(new_parent_id)
            # Le nouveau parent ne doit être ni la catégorie elle-même, ni l'un de
            # ses descendants : sinon le sous-arbre se détacherait en boucle.
            is_descendant = await conn.fetchval(
                """
                WITH RECURSIVE sub(id, depth) AS (
                    SELECT id, 0 FROM categories WHERE id = $1
                    UNION ALL
                    SELECT c.id, s.depth + 1 FROM categories c JOIN sub s ON c.parent_id = s.id
                    WHERE s.depth < $3
                )
                SELECT EXISTS (SELECT 1 FROM sub WHERE id = $2)
                """,
                category_id, new_parent_id, MAX_DEPTH,
            )  # fmt: skip
            if is_descendant:
                raise CategoryCycleError("Déplacement impossible : il créerait une boucle.")
        await conn.execute(
            "UPDATE categories SET parent_id = $2, updated_at = now() WHERE id = $1",
            category_id, new_parent_id,
        )  # fmt: skip
    log.info("categorie_deplacee", category_id=category_id, parent_id=new_parent_id)


async def resolve_delete_category(pool: asyncpg.Pool, category_id: int) -> None:
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock($1)", _TREE_LOCK_KEY)
        row = await conn.fetchrow(
            "SELECT (SELECT count(*) FROM categories WHERE parent_id = $1) AS children,"
            " (SELECT count(*) FROM candidates WHERE category_id = $1) AS candidates,"
            " EXISTS (SELECT 1 FROM categories WHERE id = $1) AS found",
            category_id,
        )
        if row is None or not row["found"]:
            raise CategoryNotFoundError(category_id)
        if row["children"] or row["candidates"]:
            raise CategoryInUseError(row["children"], row["candidates"])
        await conn.execute("DELETE FROM categories WHERE id = $1", category_id)
    log.info("categorie_supprimee", category_id=category_id)


async def db_effective_defaults(conn: repo._Conn, category_id: int) -> dict[str, Any]:
    """Défauts effectifs : une valeur vide est héritée du plus proche ancêtre qui en porte une."""
    rows = await conn.fetch(
        """
        WITH RECURSIVE up(id, parent_id, depth) AS (
            SELECT id, parent_id, 0 FROM categories WHERE id = $1
            UNION ALL
            SELECT c.id, c.parent_id, u.depth + 1 FROM categories c JOIN up u ON c.id = u.parent_id
            WHERE u.depth < $2
        )
        SELECT c.photo_prompt_template, c.description_template, c.parcel_size
        FROM up JOIN categories c ON c.id = up.id ORDER BY up.depth
        """,
        category_id, MAX_DEPTH,
    )  # fmt: skip
    defaults: dict[str, Any] = dict.fromkeys(CATEGORY_DEFAULTS_TO_CANDIDATE)
    for row in rows:  # du nœud vers la racine : la première valeur trouvée gagne
        for name in defaults:
            if defaults[name] is None and row[name] is not None:
                defaults[name] = row[name]
    return defaults


async def db_list_mappings(conn: repo._Conn, category_id: int) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        "SELECT platform, platform_category_id FROM category_platform_mappings"
        " WHERE category_id = $1 ORDER BY platform",
        category_id,
    )
    return [dict(r) for r in rows]


async def resolve_set_mapping(
    pool: asyncpg.Pool, category_id: int, platform: str, platform_category_id: str
) -> None:
    """Pose (ou remplace) la correspondance d'une catégorie vers UNE plateforme."""
    name, external = platform.strip().lower(), platform_category_id.strip()
    async with pool.acquire() as conn:
        try:
            await conn.execute(
                "INSERT INTO category_platform_mappings"
                " (category_id, platform, platform_category_id) VALUES ($1, $2, $3)"
                " ON CONFLICT (category_id, platform)"
                " DO UPDATE SET platform_category_id = EXCLUDED.platform_category_id",
                category_id, name, external,
            )  # fmt: skip
        except asyncpg.ForeignKeyViolationError:
            raise CategoryNotFoundError(category_id) from None
        except asyncpg.CheckViolationError:
            raise ValueError(
                "Plateforme en minuscules sans espace (ex. vinted) et identifiant non vide."
            ) from None


async def db_delete_mapping(conn: repo._Conn, category_id: int, platform: str) -> bool:
    deleted = await conn.fetchval(
        "DELETE FROM category_platform_mappings WHERE category_id = $1 AND platform = $2"
        " RETURNING 1",
        category_id,
        platform,
    )
    return deleted is not None
