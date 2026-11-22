"""Écran du coffre de secrets : liste en lignes, ajout et modification dans une fenêtre.

Aucune valeur n'est jamais réaffichée : le formulaire de modification laisse le champ
valeur vide, et une valeur vide conserve l'actuelle.
"""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Annotated, Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from atelier.vault import service as vault
from atelier.web.templating import templates

router = APIRouter(tags=["secrets"], default_response_class=HTMLResponse)

_NO_STORE = {"Cache-Control": "no-store"}


def _sentence(exc: Exception) -> str:
    msg = str(exc)
    return f"{msg[:1].upper()}{msg[1:]}."


async def _page(request: Request, error: str | None = None, status: int = 200) -> HTMLResponse:
    async with request.app.state.pool.acquire() as conn:
        secrets = await vault.db_list_secrets(conn)
        categories = await vault.db_list_categories(conn)
    return templates.TemplateResponse(
        request, "secrets.html",
        {"secrets": secrets, "categories": categories, "error": error},
        status_code=status, headers=_NO_STORE,
    )  # fmt: skip


async def _form(
    request: Request, secret: vault.SecretInfo | None, error: str | None = None,
    values: dict[str, Any] | None = None,
) -> HTMLResponse:  # fmt: skip
    async with request.app.state.pool.acquire() as conn:
        categories = await vault.db_list_categories(conn)
    context = {"secret": secret, "categories": categories, "error": error, "values": values or {}}
    # Erreur de saisie rendue en 200 : HTMX n'insère pas une réponse 4xx par défaut, et
    # le formulaire doit rester ouvert avec son message plutôt que disparaître.
    return templates.TemplateResponse(request, "_secret_form.html", context, headers=_NO_STORE)


def _done() -> Response:
    # Formulaire envoyé par HTMX : HX-Redirect recharge la liste (une 303 serait suivie
    # par HTMX et insérée dans la fenêtre).
    return Response(status_code=204, headers={"HX-Redirect": "/secrets"})


async def _get_or_404(request: Request, secret_id: int) -> vault.SecretInfo:
    async with request.app.state.pool.acquire() as conn:
        secret = await vault.db_get_secret(conn, secret_id)
    if secret is None:
        raise HTTPException(status_code=404)
    return secret


def _kept(category_id: int, name: str, description: str) -> dict[str, Any]:
    # La valeur saisie n'est PAS renvoyée dans le formulaire en erreur : elle ne doit
    # transiter qu'une fois, vers le serveur.
    return {"category_id": category_id, "name": name, "description": description}


@router.get("/secrets")
async def show_secrets(request: Request) -> HTMLResponse:
    return await _page(request)


@router.get("/secrets/new")
async def new_secret_form(request: Request) -> HTMLResponse:
    return await _form(request, None)


@router.get("/secrets/{secret_id:int}/edit")
async def edit_secret_form(request: Request, secret_id: int) -> HTMLResponse:
    return await _form(request, await _get_or_404(request, secret_id))


@router.post("/secrets", response_model=None)
async def add_secret(
    request: Request,
    category_id: Annotated[int, Form()],
    name: Annotated[str, Form()] = "",
    value: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
) -> Response:
    state = request.app.state
    try:
        await vault.resolve_add_secret(
            state.pool, state.secret_cipher, category_id, name, value, description
        )
    except vault.VaultError as exc:
        return await _form(request, None, _sentence(exc), _kept(category_id, name, description))
    except vault.VaultNotFoundError:
        return await _form(
            request, None, "Catégorie inconnue.", _kept(category_id, name, description)
        )
    return _done()


@router.post("/secrets/{secret_id:int}", response_model=None)
async def update_secret(
    request: Request,
    secret_id: int,
    category_id: Annotated[int, Form()],
    name: Annotated[str, Form()] = "",
    value: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
) -> Response:
    state = request.app.state
    secret = await _get_or_404(request, secret_id)
    try:
        await vault.resolve_update_secret(
            state.pool, state.secret_cipher, secret_id, category_id, name, description, value
        )
    except vault.VaultError as exc:
        return await _form(request, secret, _sentence(exc), _kept(category_id, name, description))
    except vault.VaultNotFoundError:
        # La ligne existe (vérifiée ci-dessus) : c'est donc la catégorie qui manque.
        return await _form(
            request, secret, "Catégorie inconnue.", _kept(category_id, name, description)
        )
    return _done()


@router.post("/secrets/{secret_id:int}/delete", response_model=None)
async def delete_secret(request: Request, secret_id: int) -> Response:
    try:
        await vault.resolve_delete_secret(request.app.state.pool, secret_id)
    except vault.VaultNotFoundError:
        raise HTTPException(status_code=404) from None
    return _done()


# --- Catégories (fenêtre « Gérer les catégories ») ---------------------------------


@router.get("/secrets/categories")
async def categories_dialog(request: Request) -> HTMLResponse:
    async with request.app.state.pool.acquire() as conn:
        categories = await vault.db_list_categories(conn)
    return templates.TemplateResponse(
        request, "_secret_categories.html", {"categories": categories}
    )


async def _run(request: Request, action: Awaitable[object]) -> Response:
    try:
        await action
    except vault.VaultError as exc:
        return await _page(request, _sentence(exc), 422)
    except vault.VaultNotFoundError:
        raise HTTPException(status_code=404) from None
    return RedirectResponse("/secrets", status_code=303)


@router.post("/secrets/categories", response_model=None)
async def create_category(
    request: Request, slug: Annotated[str, Form()] = "", label: Annotated[str, Form()] = ""
) -> Response:
    return await _run(request, vault.resolve_create_category(request.app.state.pool, slug, label))


@router.post("/secrets/categories/{category_id}/label", response_model=None)
async def rename_category(
    request: Request, category_id: int, label: Annotated[str, Form()] = ""
) -> Response:
    pool = request.app.state.pool
    return await _run(request, vault.resolve_rename_category(pool, category_id, label))


@router.post("/secrets/categories/{category_id}/delete", response_model=None)
async def delete_category(request: Request, category_id: int) -> Response:
    return await _run(request, vault.resolve_delete_category(request.app.state.pool, category_id))
