from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import httpx
import pytest
from pydantic import SecretStr

from atelier.app import create_app
from atelier.config import Settings
from tests.images_helpers import make_image_bytes


@pytest.fixture
async def client(tmp_path: Path, migrated_pool: asyncpg.Pool) -> AsyncIterator[httpx.AsyncClient]:
    settings = Settings(
        database_url=SecretStr("postgresql://u:p@h/atelier"),
        images_root=tmp_path / "images",
        capture_token=SecretStr("tok"),
        secrets_key=SecretStr("ab" * 32),
    )
    (tmp_path / "images").mkdir()
    (tmp_path / "secret.txt").write_text("hors volume")
    app = create_app(settings, migrated_pool)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


async def _new_candidate(pool: asyncpg.Pool) -> int:
    return await pool.fetchval("INSERT INTO candidates DEFAULT VALUES RETURNING id")  # type: ignore[no-any-return]


def _upload(raw: bytes) -> dict[str, tuple[str, bytes, str]]:
    return {"file": ("visuel.png", raw, "image/png")}


async def test_upload_photo_then_media_serves_it(
    client: httpx.AsyncClient, migrated_pool: asyncpg.Pool
) -> None:
    cid = await _new_candidate(migrated_pool)
    response = await client.post(f"/candidates/{cid}/photos", files=_upload(make_image_bytes()))
    assert response.status_code == 200
    src = response.text.split('src="')[1].split('"')[0]
    assert src.startswith(f"/media/candidates/{cid}/")
    media = await client.get(src)
    assert media.status_code == 200
    assert media.headers["content-type"] == "image/jpeg"


async def test_upload_non_image_shows_message_and_writes_nothing(
    client: httpx.AsyncClient, migrated_pool: asyncpg.Pool, tmp_path: Path
) -> None:
    cid = await _new_candidate(migrated_pool)
    response = await client.post(f"/candidates/{cid}/photos", files=_upload(b"pas une image"))
    assert response.status_code == 422
    assert "pas une image" in response.text
    assert list((tmp_path / "images").rglob("*.jpg")) == []


async def test_upload_to_unknown_candidate_is_404(client: httpx.AsyncClient) -> None:
    response = await client.post("/candidates/98765/photos", files=_upload(make_image_bytes()))
    assert response.status_code == 404


@pytest.mark.parametrize(
    "path", ["/media/../secret.txt", "/media/%2e%2e/secret.txt", "/media/..%2fsecret.txt"]
)
async def test_media_traversal_is_refused(client: httpx.AsyncClient, path: str) -> None:
    response = await client.get(path)
    assert response.status_code == 404
    assert "hors volume" not in response.text


async def test_media_unknown_file_is_404(client: httpx.AsyncClient) -> None:
    assert (await client.get("/media/candidates/1/absent.jpg")).status_code == 404


async def test_photo_with_missing_file_is_shown_as_missing(
    client: httpx.AsyncClient, migrated_pool: asyncpg.Pool, tmp_path: Path
) -> None:
    cid = await _new_candidate(migrated_pool)
    await client.post(f"/candidates/{cid}/photos", files=_upload(make_image_bytes()))
    for f in (tmp_path / "images").rglob("*.jpg"):
        f.unlink()
    page = await client.get(f"/candidates/{cid}")
    assert page.status_code == 200
    assert "Fichier manquant" in page.text


async def test_delete_photo_route_removes_file(
    client: httpx.AsyncClient, migrated_pool: asyncpg.Pool, tmp_path: Path
) -> None:
    cid = await _new_candidate(migrated_pool)
    await client.post(f"/candidates/{cid}/photos", files=_upload(make_image_bytes()))
    photo_id = await migrated_pool.fetchval("SELECT id FROM candidate_photos")
    response = await client.post(f"/photos/{photo_id}/delete")
    assert response.status_code == 200
    assert "Aucune photo" in response.text
    assert list((tmp_path / "images").rglob("*.jpg")) == []
    assert (await client.post(f"/photos/{photo_id}/delete")).status_code == 404


async def test_candidate_page_unknown_is_404(client: httpx.AsyncClient) -> None:
    assert (await client.get("/candidates/123456")).status_code == 404
    assert (await client.get("/candidates/123456/photos")).status_code == 404


async def test_candidate_photos_fragment_lists_photos(
    client: httpx.AsyncClient, migrated_pool: asyncpg.Pool
) -> None:
    cid = await _new_candidate(migrated_pool)
    await client.post(f"/candidates/{cid}/photos", files=_upload(make_image_bytes()))
    fragment = await client.get(f"/candidates/{cid}/photos")
    assert fragment.text.count('class="photo-card"') == 1


async def test_references_upload_list_and_default(client: httpx.AsyncClient) -> None:
    empty = await client.get("/references")
    assert "Aucune photo de référence" in empty.text
    first = await client.post(
        "/references", data={"label": "Face"}, files=_upload(make_image_bytes())
    )
    assert first.status_code == 303
    await client.post("/references", data={"label": "Profil"}, files=_upload(make_image_bytes()))
    page = await client.get("/references")
    assert page.text.count("Par défaut</span>") == 1
    ref_id = page.text.split('action="/references/')[1].split("/")[0]
    switched = await client.post(f"/references/{ref_id}/default")
    assert switched.status_code == 303
    assert (await client.post("/references/999999/default")).status_code == 404


async def test_references_invalid_upload_shows_message(client: httpx.AsyncClient) -> None:
    response = await client.post("/references", data={"label": "x"}, files=_upload(b"zzz"))
    assert response.status_code == 422
    assert "pas une image" in response.text
