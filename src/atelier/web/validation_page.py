"""Validation d'une candidate (fiche, API) et gestion du jeu de règles."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from atelier.candidates.validation import (
    REQUIRABLE_FIELDS,
    RULE_KINDS,
    db_list_rules,
    db_set_rule_enabled,
    resolve_add_field_rule,
    resolve_validate_candidate,
)
from atelier.web.templating import templates

router = APIRouter(tags=["validation"], default_response_class=HTMLResponse)


@router.get("/api/candidates/{candidate_id}/validation", response_model=None)
async def read_validation(request: Request, candidate_id: int) -> JSONResponse:
    report = await resolve_validate_candidate(request.app.state.pool, candidate_id)
    if report is None:
        return JSONResponse({"error": f"candidate {candidate_id} introuvable"}, status_code=404)
    return JSONResponse(report.as_dict())


@router.post("/candidates/{candidate_id}/validate", response_model=None)
async def validate_candidate(request: Request, candidate_id: int) -> Response:
    # Même fonction que l'API (et, plus tard, que l'outil MCP) : l'interface et un
    # agent voient exactement la même liste de manques.
    report = await resolve_validate_candidate(request.app.state.pool, candidate_id)
    if report is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request, "_validation.html", {"candidate_id": candidate_id, "report": report}
    )


@router.get("/rules")
async def show_rules(request: Request) -> HTMLResponse:
    async with request.app.state.pool.acquire() as conn:
        rules = await db_list_rules(conn)
    return templates.TemplateResponse(
        request,
        "rules.html",
        {"rules": rules, "fields": REQUIRABLE_FIELDS, "known_kinds": RULE_KINDS},
    )


@router.post("/rules", response_model=None)
async def add_rule(
    request: Request,
    field: Annotated[str, Form()] = "",
    message: Annotated[str, Form()] = "",
) -> Response:
    if field not in REQUIRABLE_FIELDS:
        raise HTTPException(status_code=422)
    await resolve_add_field_rule(request.app.state.pool, field, message)
    return RedirectResponse("/rules", status_code=303)


@router.post("/rules/{rule_id}/enabled", response_model=None)
async def set_rule_enabled(
    request: Request, rule_id: int, enabled: Annotated[str, Form()] = ""
) -> Response:
    async with request.app.state.pool.acquire() as conn:
        if not await db_set_rule_enabled(conn, rule_id, enabled == "true"):
            raise HTTPException(status_code=404)
    return RedirectResponse("/rules", status_code=303)
