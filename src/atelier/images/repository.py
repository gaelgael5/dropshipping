from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import asyncpg
from asyncpg.pool import PoolConnectionProxy

_Conn = asyncpg.Connection | PoolConnectionProxy


@dataclass(frozen=True)
class PhotoRow:
    id: int
    candidate_id: int
    kind: str
    file_path: str
    width: int
    height: int
    created_at: datetime


@dataclass(frozen=True)
class ReferenceRow:
    id: int
    label: str
    file_path: str
    width: int
    height: int
    is_default: bool
    created_at: datetime


def _returned(row: asyncpg.Record | None) -> asyncpg.Record:
    # INSERT ... RETURNING rend toujours une ligne ; l'absence serait une panne.
    if row is None:
        raise RuntimeError("INSERT ... RETURNING n'a rendu aucune ligne")
    return row


def _photo(r: asyncpg.Record) -> PhotoRow:
    return PhotoRow(
        r["id"], r["candidate_id"], r["kind"], r["file_path"], r["width"], r["height"],
        r["created_at"],
    )  # fmt: skip


def _reference(r: asyncpg.Record) -> ReferenceRow:
    return ReferenceRow(
        r["id"], r["label"], r["file_path"], r["width"], r["height"], r["is_default"],
        r["created_at"],
    )  # fmt: skip


async def db_candidate_exists(conn: _Conn, candidate_id: int) -> bool:
    return bool(await conn.fetchval("SELECT 1 FROM candidates WHERE id = $1", candidate_id))


async def db_insert_candidate_photo(
    conn: _Conn, candidate_id: int, kind: str, file_path: str, width: int, height: int
) -> PhotoRow:
    row = await conn.fetchrow(
        "INSERT INTO candidate_photos (candidate_id, kind, file_path, width, height)"
        " VALUES ($1, $2, $3, $4, $5) RETURNING *",
        candidate_id, kind, file_path, width, height,
    )  # fmt: skip
    return _photo(_returned(row))


async def db_list_candidate_photos(conn: _Conn, candidate_id: int) -> list[PhotoRow]:
    rows = await conn.fetch(
        "SELECT * FROM candidate_photos WHERE candidate_id = $1 ORDER BY id", candidate_id
    )
    return [_photo(r) for r in rows]


async def db_delete_candidate_photo(conn: _Conn, photo_id: int) -> PhotoRow | None:
    row = await conn.fetchrow("DELETE FROM candidate_photos WHERE id = $1 RETURNING *", photo_id)
    return _photo(row) if row else None


async def db_insert_reference(
    conn: _Conn, label: str, file_path: str, width: int, height: int
) -> ReferenceRow:
    # La première référence déposée devient celle par défaut : sans cela, la
    # génération n'aurait aucune référence tant qu'on n'a pas pensé à en désigner une.
    row = await conn.fetchrow(
        "INSERT INTO reference_photos (label, file_path, width, height, is_default)"
        " VALUES ($1, $2, $3, $4, NOT EXISTS (SELECT 1 FROM reference_photos WHERE is_default))"
        " RETURNING *",
        label, file_path, width, height,
    )  # fmt: skip
    return _reference(_returned(row))


async def db_list_references(conn: _Conn) -> list[ReferenceRow]:
    rows = await conn.fetch("SELECT * FROM reference_photos ORDER BY id DESC")
    return [_reference(r) for r in rows]


async def db_get_default_reference(conn: _Conn) -> ReferenceRow | None:
    row = await conn.fetchrow("SELECT * FROM reference_photos WHERE is_default")
    return _reference(row) if row else None


async def db_set_default_reference(conn: _Conn, reference_id: int) -> bool:
    # Une seule transaction : retirer l'ancien défaut et poser le nouveau. Entre
    # deux transactions, l'index d'unicité partiel refuserait l'état intermédiaire
    # ou laisserait un instant sans référence par défaut.
    async with conn.transaction():
        if not await conn.fetchval("SELECT 1 FROM reference_photos WHERE id = $1", reference_id):
            return False
        await conn.execute("UPDATE reference_photos SET is_default = false WHERE is_default")
        await conn.execute(
            "UPDATE reference_photos SET is_default = true WHERE id = $1", reference_id
        )
    return True
