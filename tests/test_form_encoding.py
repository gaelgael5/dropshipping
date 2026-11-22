from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import httpx
import pytest
from pydantic import SecretStr

from atelier.app import create_app
from atelier.config import Settings

FORM = {"Content-Type": "application/x-www-form-urlencoded"}


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


async def test_form_raw_utf8_body_stored_as_utf8(
    client: httpx.AsyncClient, migrated_pool: asyncpg.Pool
) -> None:
    # Constaté sur test1 : « curl -d label=Vêtements » envoie l'UTF-8 sans pourcent-encodage,
    # et le libellé était stocké « VÃªtements » (relu en Latin-1).
    raw = "label=Vêtements d'été&parent_id=".encode()
    reply = await client.post("/categories", content=raw, headers=FORM)
    assert reply.status_code == 204
    assert await migrated_pool.fetchval("SELECT label FROM categories") == "Vêtements d'été"


async def test_form_percent_encoded_body_unchanged(
    client: httpx.AsyncClient, migrated_pool: asyncpg.Pool
) -> None:
    reply = await client.post("/categories", data={"label": "Chaussures à talon", "parent_id": ""})
    assert reply.status_code == 204
    assert await migrated_pool.fetchval("SELECT label FROM categories") == "Chaussures à talon"
