from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import httpx
import pytest
from pydantic import SecretStr

from atelier.app import create_app
from atelier.config import Settings


@pytest.fixture
async def client(tmp_path: Path, migrated_pool: asyncpg.Pool) -> AsyncIterator[httpx.AsyncClient]:
    settings = Settings(
        database_url=SecretStr("postgresql://u:p@h/atelier"),
        images_root=tmp_path,
        capture_token=SecretStr("tok"),
        secrets_key=SecretStr("ab" * 32),
    )
    app = create_app(settings, migrated_pool)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


async def _candidate(pool: asyncpg.Pool) -> int:
    return int(await pool.fetchval("INSERT INTO candidates DEFAULT VALUES RETURNING id"))


async def test_api_and_screen_show_same_missing_list(
    client: httpx.AsyncClient, migrated_pool: asyncpg.Pool
) -> None:
    cid = await _candidate(migrated_pool)
    api = (await client.get(f"/api/candidates/{cid}/validation")).json()
    screen = await client.post(f"/candidates/{cid}/validate")
    assert api["ready"] is False
    assert f"{len(api['missing'])} point(s) à compléter" in screen.text
    for missing in api["missing"]:
        assert missing["reason"].replace("'", "&#39;") in screen.text


async def test_unknown_candidate_answers_cleanly(client: httpx.AsyncClient) -> None:
    api = await client.get("/api/candidates/4040/validation")
    assert api.status_code == 404
    assert "introuvable" in api.json()["error"]
    assert (await client.post("/candidates/4040/validate")).status_code == 404


async def test_saving_incomplete_candidate_still_works(
    client: httpx.AsyncClient, migrated_pool: asyncpg.Pool
) -> None:
    cid = await _candidate(migrated_pool)
    saved = await client.post(f"/candidates/{cid}/fields/brand", data={"value": "Zara"})
    assert saved.status_code == 200


async def test_fiche_has_validation_button(
    client: httpx.AsyncClient, migrated_pool: asyncpg.Pool
) -> None:
    cid = await _candidate(migrated_pool)
    page = await client.get(f"/candidates/{cid}")
    assert f'hx-post="/candidates/{cid}/validate"' in page.text


async def test_rules_page_toggle_and_add(
    client: httpx.AsyncClient, migrated_pool: asyncpg.Pool
) -> None:
    page = await client.get("/rules")
    assert "titre_requis" in page.text
    rule_id = await migrated_pool.fetchval(
        "SELECT id FROM validation_rules WHERE code='titre_requis'"
    )
    off = await client.post(f"/rules/{rule_id}/enabled", data={"enabled": "false"})
    assert off.status_code == 303
    assert (
        await migrated_pool.fetchval("SELECT enabled FROM validation_rules WHERE id=$1", rule_id)
        is False
    )
    added = await client.post("/rules", data={"field": "brand", "message": "Marque svp"})
    assert added.status_code == 303
    assert "champ_brand_requis" in (await client.get("/rules")).text
    assert (await client.post("/rules", data={"field": "id"})).status_code == 422
    assert (await client.post("/rules/99999/enabled", data={"enabled": "true"})).status_code == 404
