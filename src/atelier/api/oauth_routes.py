"""Routes OAuth 2.1 : découverte, inscription, consentement, jetons."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from atelier.api.public_url import resolve_public_base
from atelier.connectors import oauth_flow as flow
from atelier.connectors.oauth_clients import OAuthError, resolve_register_client
from atelier.web.templating import templates

router = APIRouter(tags=["oauth"])

_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
_AUTHORIZE_FIELDS = (
    "response_type", "client_id", "redirect_uri", "code_challenge", "code_challenge_method",
    "state",
)  # fmt: skip


@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/mcp")
async def protected_resource(request: Request) -> JSONResponse:
    base = resolve_public_base(request)
    return JSONResponse({"resource": f"{base}/mcp", "authorization_servers": [base],
                         "bearer_methods_supported": ["header"]})  # fmt: skip


@router.get("/.well-known/oauth-authorization-server")
async def authorization_server(request: Request) -> JSONResponse:
    base = resolve_public_base(request)
    return JSONResponse({
        "issuer": base,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "registration_endpoint": f"{base}/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
    })  # fmt: skip


@router.post("/oauth/register")
async def register(request: Request) -> JSONResponse:
    try:
        body = json.loads(await request.body())
        if not isinstance(body, dict):
            raise ValueError
    except ValueError:
        err = OAuthError("invalid_client_metadata", "corps JSON objet attendu")
        return JSONResponse(err.as_dict(), status_code=400)
    try:
        client = await resolve_register_client(request.app.state.pool, body)
    except OAuthError as exc:
        return JSONResponse(exc.as_dict(), status_code=400)
    return JSONResponse(client, status_code=201, headers=_NO_STORE)


def _consent(
    request: Request, params: dict[str, str], req: flow.AuthorizeRequest | None,
    error: str | None, status: int,
) -> HTMLResponse:  # fmt: skip
    context: dict[str, Any] = {"params": params, "req": req, "error": error}
    if req is not None:
        context["redirect_host"] = urlsplit(req.redirect_uri).netloc
    response = templates.TemplateResponse(
        request, "oauth_consent.html", context, status_code=status
    )
    # Page de consentement jamais encadrable : sinon un site tiers pourrait la cacher
    # sous un faux bouton (détournement de clic).
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
    response.headers.update(_NO_STORE)
    return response


@router.get("/oauth/authorize", response_class=HTMLResponse)
async def authorize_page(request: Request) -> HTMLResponse:
    params = {k: request.query_params.get(k, "") for k in _AUTHORIZE_FIELDS}
    try:
        req = await flow.resolve_check_authorize(request.app.state.pool, params)
    except flow.AuthorizeRejected as exc:
        return _consent(request, params, None, str(exc), 400)
    return _consent(request, params, req, None, 200)


def _back_to_client(req: flow.AuthorizeRequest, values: dict[str, str]) -> RedirectResponse:
    if req.state:
        values["state"] = req.state
    sep = "&" if "?" in req.redirect_uri else "?"
    return RedirectResponse(f"{req.redirect_uri}{sep}{urlencode(values)}", status_code=302)


@router.post("/oauth/authorize", response_model=None)
async def authorize_submit(request: Request) -> Response:
    form = await request.form()
    params = {k: str(form.get(k, "")) for k in _AUTHORIZE_FIELDS}
    try:
        req = await flow.resolve_check_authorize(request.app.state.pool, params)
    except flow.AuthorizeRejected as exc:
        return _consent(request, params, None, str(exc), 400)
    if form.get("decision") != "approve":
        return _back_to_client(req, {"error": "access_denied"})
    code = await flow.resolve_approve(request.app.state.pool, req, str(form.get("pairing", "")))
    if code is None:
        msg = "Code d'appairage incorrect, expiré ou déjà utilisé. Générez-en un nouveau."
        return _consent(request, params, req, msg, 400)
    return _back_to_client(req, {"code": code})


@router.post("/oauth/token")
async def token(request: Request) -> JSONResponse:
    form = {k: str(v) for k, v in (await request.form()).items()}
    try:
        tokens = await flow.resolve_token(request.app.state.pool, form)
    except OAuthError as exc:
        status = 401 if exc.error == "invalid_client" else 400
        return JSONResponse(exc.as_dict(), status_code=status, headers=_NO_STORE)
    return JSONResponse(tokens, headers=_NO_STORE)
