from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import httpx
import pytest
from pydantic import SecretStr

from atelier.app import create_app
from atelier.config import Settings
from atelier.web.pages import PAGE_SIZE

TOKEN = "jeton-de-capture-9Qx"


@pytest.fixture
async def client(tmp_path: Path, migrated_pool: asyncpg.Pool) -> AsyncIterator[httpx.AsyncClient]:
    settings = Settings(
        database_url=SecretStr("postgresql://u:p@h/atelier"),
        images_root=tmp_path,
        capture_token=SecretStr(TOKEN),
    )
    app = create_app(settings, migrated_pool)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _auth(token: str = TOKEN) -> dict[str, str]:
    return {"X-Capture-Token": token}


async def test_api_capture_new_url_returns_201_with_outcome(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/capture", json={"url": "https://temu.com/p/1.html"}, headers=_auth()
    )
    assert response.status_code == 201
    body = response.json()
    assert body["created"] is True
    assert body["title"] is None
    assert isinstance(body["candidate_id"], int)
    assert body["message"] == "Candidate créée"


async def test_api_capture_known_url_returns_200_existing(client: httpx.AsyncClient) -> None:
    first = await client.post(
        "/api/capture", json={"url": "https://temu.com/p/2.html"}, headers=_auth()
    )
    again = await client.post(
        "/api/capture", json={"url": "https://temu.com/p/2.html?utm_campaign=z"}, headers=_auth()
    )
    assert again.status_code == 200
    assert again.json()["created"] is False
    assert again.json()["candidate_id"] == first.json()["candidate_id"]


@pytest.mark.parametrize("headers", [{}, {"X-Capture-Token": "faux"}, {"X-Capture-Token": ""}])
async def test_api_capture_without_valid_token_is_refused_identically(
    client: httpx.AsyncClient, headers: dict[str, str]
) -> None:
    await client.post("/api/capture", json={"url": "https://temu.com/p/3.html"}, headers=_auth())
    known = await client.post(
        "/api/capture", json={"url": "https://temu.com/p/3.html"}, headers=headers
    )
    unknown = await client.post(
        "/api/capture", json={"url": "https://temu.com/p/inconnu.html"}, headers=headers
    )
    assert known.status_code == unknown.status_code == 401
    assert known.json() == unknown.json()


async def test_api_capture_token_in_query_string_is_ignored(client: httpx.AsyncClient) -> None:
    response = await client.post(
        f"/api/capture?token={TOKEN}", json={"url": "https://temu.com/p/4.html"}
    )
    assert response.status_code == 401


async def test_api_capture_bearer_header_is_not_accepted(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/capture",
        json={"url": "https://temu.com/p/5.html"},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert response.status_code == 401


async def test_api_capture_not_a_url_returns_422_with_message(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/capture", json={"url": "bonjour"}, headers=_auth())
    assert response.status_code == 422
    assert "Ce n'est pas une URL" in response.json()["error"]


@pytest.mark.parametrize("content", [b"pas du json", b'{"lien": "https://x.fr"}', b"[]"])
async def test_api_capture_malformed_body_returns_422(
    client: httpx.AsyncClient, content: bytes
) -> None:
    response = await client.post("/api/capture", content=content, headers=_auth())
    assert response.status_code == 422


async def test_queue_without_candidates_shows_empty_state_and_form(
    client: httpx.AsyncClient,
) -> None:
    page = await client.get("/")
    assert page.status_code == 200
    assert "Aucune candidate pour l'instant" in page.text
    assert 'name="url"' in page.text
    assert "<table" not in page.text


async def test_form_capture_redirects_and_queue_lists_candidate(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post("/capture", data={"url": "https://shein.com/x-p-7.html"})
    assert response.status_code == 303
    assert response.headers["location"].startswith("/?capture=created&highlight=")
    page = await client.get(response.headers["location"])
    assert "Candidate créée." in page.text
    assert "shein.com" in page.text
    assert 'class="highlight"' in page.text


async def test_form_capture_known_url_says_already_captured(client: httpx.AsyncClient) -> None:
    await client.post("/capture", data={"url": "https://shein.com/x-p-8.html"})
    response = await client.post("/capture", data={"url": "https://shein.com/x-p-8.html"})
    assert "capture=existing" in response.headers["location"]


async def test_form_capture_invalid_url_shows_message_and_keeps_input(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post("/capture", data={"url": "n'importe quoi"})
    assert response.status_code == 422
    assert "Ce n&#39;est pas une URL" in response.text
    assert 'value="n&#39;importe quoi"' in response.text


async def test_queue_pages_rows_instead_of_loading_whole_table(
    client: httpx.AsyncClient, migrated_pool: asyncpg.Pool
) -> None:
    total = PAGE_SIZE + 7
    await migrated_pool.executemany(
        "INSERT INTO candidates (title) VALUES ($1)", [(f"Article {n}",) for n in range(total)]
    )
    page = await client.get("/")
    assert page.text.count('<tr id="candidate-') == PAGE_SIZE
    assert f"Article {total - 1}" in page.text  # la plus récente d'abord
    assert "Article 0<" not in page.text
    marker = 'hx-get="/candidates/rows?before='
    before = page.text.split(marker)[1].split('"')[0]
    more = await client.get(f"/candidates/rows?before={before}")
    assert more.text.count('<tr id="candidate-') == 7
    assert "Article 0" in more.text
    assert "load-more" not in more.text
