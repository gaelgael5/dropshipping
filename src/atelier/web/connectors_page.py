"""Page Connecteurs : code d'appairage pour Claude web, clefs d'API des agents."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from atelier.api.public_url import resolve_public_base
from atelier.connectors import api_keys, oauth_clients
from atelier.web.templating import templates

router = APIRouter(tags=["connectors"], default_response_class=HTMLResponse)


async def _page(request: Request, status: int = 200, **extra: Any) -> HTMLResponse:
    async with request.app.state.pool.acquire() as conn:
        keys = await api_keys.db_list_api_keys(conn)
        clients = await oauth_clients.db_list_clients(conn)
    context = {"keys": keys, "clients": clients,
               "mcp_url": f"{resolve_public_base(request)}/mcp", **extra}  # fmt: skip
    response = templates.TemplateResponse(request, "connectors.html", context, status_code=status)
    # Une clef ou un code fraîchement créés ne sont affichés qu'ici, une fois : aucun
    # cache (navigateur, relais) ne doit en garder une copie rejouable.
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/connectors")
async def show_connectors(request: Request) -> HTMLResponse:
    return await _page(request)


@router.post("/connectors/pairing-code")
async def new_pairing_code(request: Request) -> HTMLResponse:
    code = await oauth_clients.resolve_create_pairing_code(request.app.state.pool)
    return await _page(request, pairing_code=code, pairing_ttl=oauth_clients.PAIRING_TTL_MINUTES)


@router.post("/connectors/api-keys")
async def new_api_key(request: Request, name: Annotated[str, Form()] = "") -> HTMLResponse:
    try:
        _, raw = await api_keys.resolve_create_api_key(request.app.state.pool, name)
    except api_keys.ApiKeyError as exc:
        return await _page(request, 422, error=str(exc))
    # Affichage direct (pas de redirection) : la clef en clair ne passe jamais par une
    # URL ni par la base, elle n'existe que dans cette réponse.
    return await _page(request, new_key=raw, new_key_name=name.strip())


@router.post("/connectors/api-keys/{key_id}/revoke", response_model=None)
async def revoke_api_key(request: Request, key_id: int) -> Response:
    async with request.app.state.pool.acquire() as conn:
        if not await api_keys.db_revoke_api_key(conn, key_id):
            raise HTTPException(status_code=404)
    return RedirectResponse("/connectors", status_code=303)


@router.post("/connectors/clients/{client_id}/revoke", response_model=None)
async def revoke_client(request: Request, client_id: str) -> Response:
    if not await oauth_clients.resolve_revoke_client(request.app.state.pool, client_id):
        raise HTTPException(status_code=404)
    return RedirectResponse("/connectors", status_code=303)
