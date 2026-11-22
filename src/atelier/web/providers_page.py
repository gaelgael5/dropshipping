"""Page Fournisseurs d'IA : modèles réglables et test de connexion."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from atelier.providers import registry
from atelier.web.templating import templates

router = APIRouter(tags=["providers"], default_response_class=HTMLResponse)


async def _page(request: Request, error: str | None = None, status: int = 200) -> HTMLResponse:
    async with request.app.state.pool.acquire() as conn:
        providers = await registry.db_list_providers(conn)
        refs = {
            f"{r['slug']}/{r['name']}"
            for r in await conn.fetch(
                "SELECT c.slug, s.name FROM secrets s JOIN secret_categories c"
                " ON c.id = s.category_id"
            )
        }
    return templates.TemplateResponse(
        request, "providers.html",
        {"providers": providers, "present_refs": refs, "error": error}, status_code=status,
    )  # fmt: skip


@router.get("/providers")
async def show_providers(request: Request) -> HTMLResponse:
    return await _page(request)


@router.post("/providers/{name}/models", response_model=None)
async def update_models(
    request: Request, name: str,
    text_model: Annotated[str, Form()] = "", image_model: Annotated[str, Form()] = "",
) -> Response:  # fmt: skip
    try:
        found = await registry.resolve_update_models(
            request.app.state.pool, name, text_model, image_model
        )
    except ValueError as exc:
        return await _page(request, f"{str(exc)[:1].upper()}{str(exc)[1:]}.", 422)
    if not found:
        raise HTTPException(status_code=404)
    return RedirectResponse("/providers", status_code=303)


@router.post("/providers/{name}/check")
async def check_provider(request: Request, name: str) -> HTMLResponse:
    state = request.app.state
    async with state.pool.acquire() as conn:
        settings = await registry.db_get_provider(conn, name)
    if settings is None:
        raise HTTPException(status_code=404)
    # Transport injectable (doublure en test) ; None = réseau réel.
    transport = getattr(state, "provider_transport", None)
    result = await registry.resolve_check_connection(
        state.pool, state.secret_cipher, settings, transport
    )
    return templates.TemplateResponse(
        request, "_provider_check.html", {"name": name, "result": result}
    )
