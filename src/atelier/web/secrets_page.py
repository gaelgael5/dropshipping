"""Écran du coffre de secrets : écriture seule, aucune valeur n'est jamais réaffichée."""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from atelier.vault import service as vault
from atelier.web.templating import templates

router = APIRouter(tags=["secrets"], default_response_class=HTMLResponse)


async def _page(request: Request, error: str | None = None, status: int = 200) -> HTMLResponse:
    async with request.app.state.pool.acquire() as conn:
        categories = await vault.db_list_vault(conn)
    response = templates.TemplateResponse(
        request, "secrets.html", {"categories": categories, "error": error}, status_code=status
    )
    # La page ne contient aucune valeur, mais les formulaires en reçoivent : on évite
    # qu'un navigateur ou un proxy garde une copie d'une réponse à une saisie.
    response.headers["Cache-Control"] = "no-store"
    return response


def _back() -> RedirectResponse:
    return RedirectResponse("/secrets", status_code=303)


async def _run(request: Request, action: Awaitable[object]) -> Response:
    """Exécute une écriture du coffre et traduit ses erreurs en réponse lisible."""
    try:
        await action
    except vault.VaultError as exc:
        msg = str(exc)
        return await _page(request, f"{msg[:1].upper()}{msg[1:]}.", 422)
    except vault.VaultNotFoundError:
        raise HTTPException(status_code=404) from None
    return _back()


@router.get("/secrets")
async def show_secrets(request: Request) -> HTMLResponse:
    return await _page(request)


@router.post("/secrets/categories", response_model=None)
async def create_category(
    request: Request, slug: Annotated[str, Form()] = "", label: Annotated[str, Form()] = ""
) -> Response:
    pool = request.app.state.pool
    return await _run(request, vault.resolve_create_category(pool, slug, label))


@router.post("/secrets/categories/{category_id}/label", response_model=None)
async def rename_category(
    request: Request, category_id: int, label: Annotated[str, Form()] = ""
) -> Response:
    pool = request.app.state.pool
    return await _run(request, vault.resolve_rename_category(pool, category_id, label))


@router.post("/secrets/categories/{category_id}/delete", response_model=None)
async def delete_category(request: Request, category_id: int) -> Response:
    return await _run(request, vault.resolve_delete_category(request.app.state.pool, category_id))


@router.post("/secrets", response_model=None)
async def add_secret(
    request: Request,
    category_id: Annotated[int, Form()],
    name: Annotated[str, Form()] = "",
    value: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
) -> Response:
    state = request.app.state
    return await _run(
        request,
        vault.resolve_add_secret(
            state.pool, state.secret_cipher, category_id, name, value, description
        ),
    )


@router.post("/secrets/{secret_id}/value", response_model=None)
async def replace_secret(
    request: Request, secret_id: int, value: Annotated[str, Form()] = ""
) -> Response:
    state = request.app.state
    return await _run(
        request,
        vault.resolve_replace_secret_value(state.pool, state.secret_cipher, secret_id, value),
    )


@router.post("/secrets/{secret_id}/delete", response_model=None)
async def delete_secret(request: Request, secret_id: int) -> Response:
    return await _run(request, vault.resolve_delete_secret(request.app.state.pool, secret_id))
