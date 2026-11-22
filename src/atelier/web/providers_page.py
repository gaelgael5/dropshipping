"""Page Fournisseurs d'IA : liste en lignes, ajout et modification en fenêtre, test.

Même modèle que la page Secrets : la liste se recharge après chaque écriture
(HX-Redirect), les formulaires vivent dans une fenêtre modale.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from atelier.providers import registry
from atelier.vault import service as vault
from atelier.web.templating import templates

router = APIRouter(tags=["providers"], default_response_class=HTMLResponse)

FormStr = Annotated[str, Form()]


async def _secret_refs(request: Request) -> list[str]:
    async with request.app.state.pool.acquire() as conn:
        return [s.ref for s in await vault.db_list_secrets(conn)]


async def _get_or_404(request: Request, name: str) -> registry.ProviderSettings:
    async with request.app.state.pool.acquire() as conn:
        provider = await registry.db_get_provider(conn, name)
    if provider is None:
        raise HTTPException(status_code=404)
    return provider


async def _form(
    request: Request, provider: registry.ProviderSettings | None,
    error: str | None = None, values: dict[str, Any] | None = None,
) -> HTMLResponse:  # fmt: skip
    context = {
        "provider": provider, "error": error, "values": values or {},
        "kinds": registry.KINDS, "defaults": registry.DEFAULT_MODELS,
        "refs": await _secret_refs(request),
    }  # fmt: skip
    # 200 même en erreur : HTMX n'insère pas une 4xx, la fenêtre doit garder son message.
    return templates.TemplateResponse(request, "_provider_form.html", context)


def _done() -> Response:
    return Response(status_code=204, headers={"HX-Redirect": "/providers"})


def _sentence(exc: Exception) -> str:
    msg = str(exc)
    return f"{msg[:1].upper()}{msg[1:]}."


@router.get("/providers")
async def show_providers(request: Request) -> HTMLResponse:
    async with request.app.state.pool.acquire() as conn:
        providers = await registry.db_list_providers(conn)
    refs = set(await _secret_refs(request))
    return templates.TemplateResponse(
        request, "providers.html",
        {"providers": providers, "present_refs": refs, "kinds": registry.KINDS},
    )  # fmt: skip


@router.get("/providers/new")
async def new_provider_form(request: Request) -> HTMLResponse:
    return await _form(request, None)


@router.get("/providers/{name}/edit")
async def edit_provider_form(request: Request, name: str) -> HTMLResponse:
    return await _form(request, await _get_or_404(request, name))


@router.post("/providers", response_model=None)
async def add_provider(
    request: Request, name: FormStr = "", kind: FormStr = "", label: FormStr = "",
    secret_ref: FormStr = "", text_model: FormStr = "", image_model: FormStr = "",
) -> Response:  # fmt: skip
    try:
        await registry.resolve_create_provider(
            request.app.state.pool, name, kind, label, secret_ref, text_model, image_model
        )
    except registry.ProviderInputError as exc:
        values = {"name": name, "kind": kind, "label": label, "secret_ref": secret_ref,
                  "text_model": text_model, "image_model": image_model}  # fmt: skip
        return await _form(request, None, _sentence(exc), values)
    return _done()


@router.post("/providers/{name}", response_model=None)
async def update_provider(
    request: Request, name: str, kind: FormStr = "", label: FormStr = "",
    secret_ref: FormStr = "", text_model: FormStr = "", image_model: FormStr = "",
) -> Response:  # fmt: skip
    provider = await _get_or_404(request, name)
    try:
        await registry.resolve_update_provider(
            request.app.state.pool, name, kind, label, secret_ref, text_model, image_model
        )
    except registry.ProviderInputError as exc:
        values = {"kind": kind, "label": label, "secret_ref": secret_ref,
                  "text_model": text_model, "image_model": image_model}  # fmt: skip
        return await _form(request, provider, _sentence(exc), values)
    return _done()


@router.post("/providers/{name}/delete", response_model=None)
async def delete_provider(request: Request, name: str) -> Response:
    if not await registry.resolve_delete_provider(request.app.state.pool, name):
        raise HTTPException(status_code=404)
    return _done()


@router.post("/providers/{name}/check")
async def check_provider(request: Request, name: str) -> HTMLResponse:
    state = request.app.state
    provider = await _get_or_404(request, name)
    # Transport injectable (doublure en test) ; None = réseau réel.
    transport = getattr(state, "provider_transport", None)
    result = await registry.resolve_check_connection(
        state.pool, state.secret_cipher, provider, transport
    )
    return templates.TemplateResponse(
        request, "_provider_check.html", {"provider": provider, "result": result}
    )
