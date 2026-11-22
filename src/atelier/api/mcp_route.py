"""Point d'entrée MCP : authentification, contrôle d'origine, puis protocole."""

from __future__ import annotations

import json
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse, Response

from atelier.api.public_url import resolve_public_base
from atelier.connectors.auth import resolve_authenticate
from atelier.mcp.server import handle_message
from atelier.mcp.tools import ToolContext

router = APIRouter(tags=["mcp"])

# Second en-tête accepté pour la clef d'API : le portail qui relaie l'atelier retire
# « Authorization », un agent qui passe par lui ne pourrait pas s'authentifier.
API_KEY_HEADER = "X-Api-Key"
# Origines de navigateur admises en plus de l'atelier lui-même. Le contrôle d'origine
# est exigé par la spécification MCP contre le rebond DNS ; les clients serveur
# (claude.ai côté serveur, agents) n'envoient pas d'Origin et ne sont pas concernés.
_ALLOWED_ORIGINS = {"https://claude.ai", "https://claude.com"}


def _unauthorized(request: Request) -> JSONResponse:
    meta = f"{resolve_public_base(request)}/.well-known/oauth-protected-resource"
    return JSONResponse(
        {"error": "invalid_token", "error_description": "authentification requise"},
        status_code=401,
        headers={"WWW-Authenticate": f'Bearer resource_metadata="{meta}"'},
    )


def _origin_allowed(request: Request, origin: str | None) -> bool:
    if origin is None:
        return True
    own = urlsplit(resolve_public_base(request))
    return origin in _ALLOWED_ORIGINS or origin == f"{own.scheme}://{own.netloc}"


@router.post("/mcp", response_model=None)
async def mcp_endpoint(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header(alias=API_KEY_HEADER)] = None,
    origin: Annotated[str | None, Header()] = None,
) -> Response:
    if not _origin_allowed(request, origin):
        return JSONResponse({"error": "origine refusée"}, status_code=403)
    scheme, _, bearer = (authorization or "").partition(" ")
    raw = bearer.strip() if scheme.lower() == "bearer" else (x_api_key or "").strip()
    # Authentifié AVANT de lire le corps : un inconnu n'obtient aucune information.
    who = await resolve_authenticate(request.app.state.pool, raw) if raw else None
    if who is None:
        return _unauthorized(request)
    try:
        message = json.loads(await request.body())
    except (ValueError, UnicodeDecodeError):
        return JSONResponse(
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "JSON invalide"}},
            status_code=400,
        )
    if isinstance(message, list):
        # Les lots JSON-RPC ont été retirés du protocole (2025-06-18) : refus explicite.
        return JSONResponse(
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "lots refusés"}},
            status_code=400,
        )
    ctx = ToolContext(request.app.state.pool, request.app.state.image_store)
    reply = await handle_message(ctx, who, message)
    if reply is None:
        return Response(status_code=202)
    return JSONResponse(reply)


@router.api_route("/mcp", methods=["GET", "DELETE"], response_model=None)
async def mcp_no_stream() -> Response:
    # Serveur sans état et sans flux SSE : la spécification prévoit 405 dans ce cas.
    return Response(status_code=405, headers={"Allow": "POST"})
