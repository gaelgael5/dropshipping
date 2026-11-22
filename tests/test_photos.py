from __future__ import annotations

from pathlib import Path

import asyncpg
import pytest

from atelier.images import repository as repo
from atelier.images.normalize import InvalidImageError
from atelier.images.photos import (
    CandidateNotFoundError,
    resolve_add_candidate_photo,
    resolve_add_reference,
    resolve_delete_candidate_photo,
)
from atelier.images.store import ImageStore
from tests.images_helpers import make_image_bytes


async def _new_candidate(pool: asyncpg.Pool) -> int:
    return await pool.fetchval("INSERT INTO candidates DEFAULT VALUES RETURNING id")  # type: ignore[no-any-return]


def _files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file()]


async def test_add_photo_writes_normalized_file_and_relative_row(
    migrated_pool: asyncpg.Pool, tmp_path: Path
) -> None:
    store = ImageStore(tmp_path)
    cid = await _new_candidate(migrated_pool)
    photo = await resolve_add_candidate_photo(migrated_pool, store, cid, make_image_bytes())
    assert photo.file_path.startswith(f"candidates/{cid}/") and photo.file_path.endswith(".jpg")
    assert store.file_exists(photo.file_path)
    assert (photo.width, photo.height, photo.kind) == (64, 48, "visuel")


async def test_add_same_file_twice_keeps_both(migrated_pool: asyncpg.Pool, tmp_path: Path) -> None:
    store = ImageStore(tmp_path)
    cid = await _new_candidate(migrated_pool)
    raw = make_image_bytes()
    a = await resolve_add_candidate_photo(migrated_pool, store, cid, raw)
    b = await resolve_add_candidate_photo(migrated_pool, store, cid, raw)
    assert a.file_path != b.file_path
    assert len(_files(tmp_path)) == 2


async def test_add_invalid_image_writes_nothing(
    migrated_pool: asyncpg.Pool, tmp_path: Path
) -> None:
    cid = await _new_candidate(migrated_pool)
    with pytest.raises(InvalidImageError):
        await resolve_add_candidate_photo(migrated_pool, ImageStore(tmp_path), cid, b"texte")
    assert _files(tmp_path) == []
    assert await migrated_pool.fetchval("SELECT count(*) FROM candidate_photos") == 0


async def test_add_photo_unknown_candidate_writes_nothing(
    migrated_pool: asyncpg.Pool, tmp_path: Path
) -> None:
    with pytest.raises(CandidateNotFoundError):
        await resolve_add_candidate_photo(
            migrated_pool, ImageStore(tmp_path), 999_999, make_image_bytes()
        )
    assert _files(tmp_path) == []


async def test_add_photo_db_failure_removes_written_file(
    migrated_pool: asyncpg.Pool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid = await _new_candidate(migrated_pool)

    async def fail(*_args: object, **_kwargs: object) -> None:
        raise asyncpg.PostgresError("panne")

    monkeypatch.setattr("atelier.images.photos.repo.db_insert_candidate_photo", fail)
    with pytest.raises(asyncpg.PostgresError):
        await resolve_add_candidate_photo(
            migrated_pool, ImageStore(tmp_path), cid, make_image_bytes()
        )
    assert _files(tmp_path) == []


async def test_delete_photo_removes_row_and_file(
    migrated_pool: asyncpg.Pool, tmp_path: Path
) -> None:
    store = ImageStore(tmp_path)
    cid = await _new_candidate(migrated_pool)
    photo = await resolve_add_candidate_photo(migrated_pool, store, cid, make_image_bytes())
    deleted = await resolve_delete_candidate_photo(migrated_pool, store, photo.id)
    assert deleted is not None and deleted.id == photo.id
    assert not store.file_exists(photo.file_path)
    assert await migrated_pool.fetchval("SELECT count(*) FROM candidate_photos") == 0


async def test_delete_unknown_photo_returns_none(
    migrated_pool: asyncpg.Pool, tmp_path: Path
) -> None:
    assert await resolve_delete_candidate_photo(migrated_pool, ImageStore(tmp_path), 424242) is None


async def test_list_candidate_photos_empty_returns_nothing(migrated_pool: asyncpg.Pool) -> None:
    async with migrated_pool.acquire() as conn:
        assert await repo.db_list_candidate_photos(conn, 1) == []


async def test_first_reference_is_default_and_next_is_not(
    migrated_pool: asyncpg.Pool, tmp_path: Path
) -> None:
    store = ImageStore(tmp_path)
    first = await resolve_add_reference(migrated_pool, store, "Face", make_image_bytes())
    second = await resolve_add_reference(migrated_pool, store, "  ", make_image_bytes())
    assert first.is_default is True
    assert second.is_default is False
    assert second.label == "Référence"
    assert second.file_path.startswith("references/")


async def test_set_default_reference_switches_and_keeps_previous_file(
    migrated_pool: asyncpg.Pool, tmp_path: Path
) -> None:
    store = ImageStore(tmp_path)
    old = await resolve_add_reference(migrated_pool, store, "v1", make_image_bytes(color="red"))
    new = await resolve_add_reference(migrated_pool, store, "v2", make_image_bytes(color="blue"))
    async with migrated_pool.acquire() as conn:
        assert await repo.db_set_default_reference(conn, new.id) is True
        default = await repo.db_get_default_reference(conn)
        listed = await repo.db_list_references(conn)
    assert default is not None and default.id == new.id
    assert [r.is_default for r in listed] == [True, False]
    # L'ancienne version reste intacte pour les photos générées avec elle.
    assert store.file_exists(old.file_path)


async def test_set_default_unknown_reference_returns_false(migrated_pool: asyncpg.Pool) -> None:
    async with migrated_pool.acquire() as conn:
        assert await repo.db_set_default_reference(conn, 999) is False
        assert await repo.db_get_default_reference(conn) is None


async def test_reference_invalid_image_writes_nothing(
    migrated_pool: asyncpg.Pool, tmp_path: Path
) -> None:
    with pytest.raises(InvalidImageError):
        await resolve_add_reference(migrated_pool, ImageStore(tmp_path), "x", b"nope")
    assert _files(tmp_path) == []


async def test_database_refuses_absolute_or_escaping_paths(migrated_pool: asyncpg.Pool) -> None:
    cid = await _new_candidate(migrated_pool)
    for bad in ("/data/images/a.jpg", "candidates/../../etc/passwd"):
        with pytest.raises(asyncpg.CheckViolationError):
            await migrated_pool.execute(
                "INSERT INTO candidate_photos (candidate_id, file_path, width, height)"
                " VALUES ($1, $2, 1, 1)",
                cid,
                bad,
            )
