from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import pytest
from pydantic import SecretStr
from structlog.testing import capture_logs

from atelier.app import create_app
from atelier.config import Settings
from atelier.vault import service as vault

KEY = "AIza-test-Qm81Zx"


@pytest.fixture
async def app_client(
    tmp_path: Path, migrated_pool: asyncpg.Pool
) -> AsyncIterator[httpx.AsyncClient]:
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


def _state(client: httpx.AsyncClient) -> Any:
    return client._transport.app.state  # type: ignore[attr-defined]


async def _store_key(
    client: httpx.AsyncClient, pool: asyncpg.Pool, name: str = "gemini_api_key"
) -> None:
    ia = await pool.fetchval("SELECT id FROM secret_categories WHERE slug = 'ia'")
    await vault.resolve_add_secret(pool, _state(client).secret_cipher, ia, name, KEY)


def _form(**over: str) -> dict[str, str]:
    base = {"name": "openai_bis", "kind": "openai", "label": "OpenAI — second compte",
            "secret_ref": "ia/openai_bis_key", "text_model": "gpt-5-mini",
            "image_model": "gpt-image-1.5"}  # fmt: skip
    return {**base, **over}


async def test_list_shows_seeded_rows_missing_key_and_add_button(
    app_client: httpx.AsyncClient, migrated_pool: asyncpg.Pool
) -> None:
    page = (await app_client.get("/providers")).text
    assert 'hx-get="/providers/new"' in page
    assert "Google Gemini" in page and "OpenAI (ChatGPT)" in page
    assert page.count("absente du coffre") == 2
    await _store_key(app_client, migrated_pool)
    assert (await app_client.get("/providers")).text.count("absente du coffre") == 1


async def test_add_form_offers_kinds_and_vault_refs_in_selects(
    app_client: httpx.AsyncClient, migrated_pool: asyncpg.Pool
) -> None:
    await _store_key(app_client, migrated_pool)
    form = (await app_client.get("/providers/new")).text
    assert (
        '<select id="p-kind" name="kind"' in form and '<select id="p-ref" name="secret_ref"' in form
    )
    assert '<option value="ia/gemini_api_key"' in form
    assert 'value="gemini-3.6-flash"' in form  # modèles par défaut du type sélectionné


async def test_add_then_edit_then_delete(
    app_client: httpx.AsyncClient, migrated_pool: asyncpg.Pool
) -> None:
    added = await app_client.post("/providers", data=_form())
    assert added.status_code == 204 and added.headers["hx-redirect"] == "/providers"
    assert "OpenAI — second compte" in (await app_client.get("/providers")).text
    form = (await app_client.get("/providers/openai_bis/edit")).text
    assert 'value="gpt-5-mini"' in form and "ia/openai_bis_key (absente du coffre)" in form
    edited = await app_client.post(
        "/providers/openai_bis", data=_form(label="Renommé", text_model="gpt-x")
    )
    assert edited.status_code == 204
    row = await migrated_pool.fetchrow(
        "SELECT label, text_model FROM ai_providers WHERE name='openai_bis'"
    )
    assert tuple(row) == ("Renommé", "gpt-x")
    assert (await app_client.post("/providers/openai_bis/delete")).status_code == 204
    assert (await app_client.post("/providers/openai_bis/delete")).status_code == 404


@pytest.mark.parametrize(
    ("over", "message"),
    [({"name": "Bad"}, "Identifiant invalide"), ({"name": "gemini"}, "déjà pris"),
     ({"kind": "mistral"}, "type de fournisseur inconnu"),
     ({"secret_ref": ""}, "choisissez la clef"),
     ({"text_model": "a b"}, "nom de modèle invalide"), ({"label": " "}, "libellé")],
)  # fmt: skip
async def test_add_errors_keep_form_open(
    app_client: httpx.AsyncClient, over: dict[str, str], message: str
) -> None:
    reply = await app_client.post("/providers", data=_form(**over))
    assert reply.status_code == 200 and message.lower() in reply.text.lower()


async def test_edit_error_and_unknown(app_client: httpx.AsyncClient) -> None:
    bad = await app_client.post("/providers/gemini", data=_form(kind="gemini", image_model=""))
    assert "nom de modèle invalide" in bad.text.lower()
    assert (await app_client.get("/providers/inconnu/edit")).status_code == 404
    assert (await app_client.post("/providers/inconnu", data=_form())).status_code == 404
    assert (await app_client.post("/providers/inconnu/check")).status_code == 404


async def test_check_without_key_names_ref_and_records_failure(
    app_client: httpx.AsyncClient, migrated_pool: asyncpg.Pool
) -> None:
    reply = await app_client.post("/providers/gemini/check")
    assert "Connexion en échec" in reply.text and "ia/gemini_api_key" in reply.text
    ok = await migrated_pool.fetchval("SELECT last_check_ok FROM ai_providers WHERE name='gemini'")
    assert ok is False


async def test_check_with_valid_key_records_success(
    app_client: httpx.AsyncClient, migrated_pool: asyncpg.Pool
) -> None:
    await _store_key(app_client, migrated_pool)
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, json={"name": request.url.path})

    _state(app_client).provider_transport = httpx.MockTransport(handler)
    with capture_logs() as logs:
        reply = await app_client.post("/providers/gemini/check")
    assert "Connexion réussie" in reply.text
    assert paths == ["/v1beta/models/gemini-3.6-flash", "/v1beta/models/gemini-nano-banana-2.1"]
    assert all(KEY not in str(entry) for entry in logs)
    assert (
        await migrated_pool.fetchval("SELECT last_check_ok FROM ai_providers WHERE name='gemini'")
        is True
    )
    # Modifier la configuration efface le résultat, qui portait sur l'ancienne.
    await app_client.post(
        "/providers/gemini", data=_form(kind="gemini", secret_ref="ia/gemini_api_key")
    )
    assert (
        await migrated_pool.fetchval("SELECT last_check_ok FROM ai_providers WHERE name='gemini'")
        is None
    )


async def test_second_openai_config_uses_openai_client(
    app_client: httpx.AsyncClient, migrated_pool: asyncpg.Pool
) -> None:
    await _store_key(app_client, migrated_pool, "openai_bis_key")
    await app_client.post("/providers", data=_form())
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        return httpx.Response(401, json={"error": {"message": "bad"}})

    _state(app_client).provider_transport = httpx.MockTransport(handler)
    reply = await app_client.post("/providers/openai_bis/check")
    assert hosts == ["api.openai.com"] and "authentification" in reply.text
    assert KEY not in reply.text
