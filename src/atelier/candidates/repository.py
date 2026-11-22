from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import asyncpg
from asyncpg.pool import PoolConnectionProxy

_Conn = asyncpg.Connection | PoolConnectionProxy


@dataclass(frozen=True)
class CandidateRef:
    id: int
    title: str | None
    status: str = "brouillon"


@dataclass(frozen=True)
class CandidateRow:
    id: int
    title: str | None
    status: str
    created_at: datetime
    link_count: int
    first_link_host: str | None
    category_label: str | None = None


@dataclass(frozen=True)
class SiteRef:
    id: int
    domain: str


async def db_find_site(conn: _Conn, domains: list[str]) -> SiteRef | None:
    # Le domaine le plus long gagne : « fr.aliexpress.com » préfère une entrée
    # dédiée au sous-domaine, s'il y en a une, à celle du domaine parent.
    row = await conn.fetchrow(
        "SELECT id, domain FROM purchase_sites WHERE domain = ANY($1::text[])"
        " ORDER BY length(domain) DESC LIMIT 1",
        domains,
    )
    return SiteRef(row["id"], row["domain"]) if row else None


async def db_insert_candidate(conn: _Conn) -> int:
    return await conn.fetchval("INSERT INTO candidates DEFAULT VALUES RETURNING id")  # type: ignore[no-any-return]


async def db_insert_link_if_new(
    conn: _Conn, candidate_id: int, url: str, normalized_url: str, site_id: int | None
) -> bool:
    # ON CONFLICT DO NOTHING plutôt qu'attraper la violation d'unicité : une
    # violation avorterait la transaction entière, alors qu'ici le doublon est un
    # cas normal que l'appelant traite.
    inserted = await conn.fetchval(
        "INSERT INTO purchase_links (candidate_id, url, normalized_url, site_id)"
        " VALUES ($1, $2, $3, $4) ON CONFLICT (normalized_url) DO NOTHING RETURNING id",
        candidate_id,
        url,
        normalized_url,
        site_id,
    )
    return inserted is not None


async def db_find_candidate_by_link(conn: _Conn, normalized_url: str) -> CandidateRef | None:
    row = await conn.fetchrow(
        "SELECT c.id, c.title FROM purchase_links l JOIN candidates c ON c.id = l.candidate_id"
        " WHERE l.normalized_url = $1",
        normalized_url,
    )
    return CandidateRef(row["id"], row["title"]) if row else None


async def db_list_candidates(conn: _Conn, before_id: int | None, limit: int) -> list[CandidateRow]:
    # Pagination par curseur sur l'identifiant (croissant avec la création) plutôt
    # que par OFFSET : le coût reste constant quelle que soit la page, à 2 000
    # candidates comme à 10.
    rows = await conn.fetch(
        """
        SELECT c.id, c.title, c.status, c.created_at,
               count(l.id) AS link_count,
               min(substring(l.url FROM '^[a-zA-Z]+://([^/:?#]+)')) AS first_link_host,
               min(cat.label) AS category_label
        FROM candidates c
        LEFT JOIN purchase_links l ON l.candidate_id = c.id
        LEFT JOIN categories cat ON cat.id = c.category_id
        WHERE $1::bigint IS NULL OR c.id < $1
        GROUP BY c.id
        ORDER BY c.id DESC
        LIMIT $2
        """,
        before_id,
        limit,
    )
    return [
        CandidateRow(
            r["id"],
            r["title"],
            r["status"],
            r["created_at"],
            r["link_count"],
            r["first_link_host"],
            r["category_label"],
        )
        for r in rows
    ]


async def db_get_candidate(conn: _Conn, candidate_id: int) -> CandidateRef | None:
    row = await conn.fetchrow(
        "SELECT id, title, status FROM candidates WHERE id = $1", candidate_id
    )
    return CandidateRef(row["id"], row["title"], row["status"]) if row else None
