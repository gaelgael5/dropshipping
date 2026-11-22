from __future__ import annotations

from pathlib import Path
from typing import Any

import asyncpg
import httpx
import pytest
from pydantic import SecretStr

from atelier.app import create_app
from atelier.config import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=SecretStr("postgresql://u:p@h/atelier"),
        images_root=tmp_path,
        capture_token=SecretStr("tok"),
    )


class _UnreachablePool:
    async def fetchval(self, *_args: Any, **_kwargs: Any) -> Any:
        raise ConnectionRefusedError("base arrêtée")


def _client(settings: Settings, pool: Any) -> httpx.AsyncClient:
    app = create_app(settings, pool)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_health_reachable_database_returns_200(
    settings: Settings, pool: asyncpg.Pool
) -> None:
    async with _client(settings, pool) as client:
        response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "reachable"}


async def test_health_unreachable_database_returns_503(settings: Settings) -> None:
    async with _client(settings, _UnreachablePool()) as client:
        response = await client.get("/api/health")
    assert response.status_code == 503
    assert response.json()["database"] == "unreachable"


async def test_home_page_uses_layout_with_local_htmx(
    settings: Settings, migrated_pool: asyncpg.Pool
) -> None:
    async with _client(settings, migrated_pool) as client:
        page = await client.get("/")
        script = await client.get("/static/htmx.min.js")
    assert page.status_code == 200
    assert 'class="site-header"' in page.text
    assert "/static/htmx.min.js" in page.text
    # Aucune ressource externe chargée (le texte d'aide du champ peut contenir https://).
    assert 'src="https://' not in page.text
    assert 'href="https://' not in page.text
    assert script.status_code == 200
    assert "htmx" in script.text[:200]
