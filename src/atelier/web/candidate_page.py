"""Écran de la fiche d'une candidate."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from atelier.candidates.fiche import (
    LinkAlreadyAttachedError,
    NotFoundError,
    db_get_fiche,
    resolve_add_link,
    resolve_delete_candidate,
    resolve_remove_link,
    resolve_update_field,
)
from atelier.candidates.fields import CANDIDATE_FIELDS, LINK_FIELDS, FieldValueError
from atelier.candidates.url_normalizer import InvalidUrlError
from atelier.web.photos import photos_view
from atelier.web.templating import templates

router = APIRouter(tags=["fiche"], default_response_class=HTMLResponse)

_OWNERS = {"candidates": CANDIDATE_FIELDS, "links": LINK_FIELDS}
_TABLES = {"candidates": "candidates", "links": "purchase_links"}


async def _fiche_or_404(request: Request, candidate_id: int) -> dict[str, object]:
    async with request.app.state.pool.acquire() as conn:
        fiche = await db_get_fiche(conn, candidate_id)
    if fiche is None:
        raise HTTPException(status_code=404)
    return {
        "candidate": fiche.candidate,
        "links": fiche.links,
        "candidate_fields": CANDIDATE_FIELDS,
        "link_fields": LINK_FIELDS,
    }


@router.get("/candidates/{candidate_id}")
async def show_candidate(request: Request, candidate_id: int) -> HTMLResponse:
    context = await _fiche_or_404(request, candidate_id)
    context["candidate_id"] = candidate_id
    context["photos"] = await photos_view(request, candidate_id)
    return templates.TemplateResponse(request, "candidate.html", context)


@router.post("/{owner}/{row_id}/fields/{name}", response_model=None)
async def save_field(
    request: Request, owner: str, row_id: int, name: str, value: Annotated[str, Form()] = ""
) -> Response:
    specs = _OWNERS.get(owner)
    if specs is None or name not in specs:
        raise HTTPException(status_code=404)
    error = None
    try:
        stored = await resolve_update_field(
            request.app.state.pool, _TABLES[owner], row_id, name, value
        )
    except FieldValueError as exc:
        error, stored = str(exc), None
    except NotFoundError:
        raise HTTPException(status_code=404) from None
    spec = specs[name]
    return templates.TemplateResponse(
        request,
        "_field.html",
        {
            "owner": owner,
            "row_id": row_id,
            "name": name,
            "spec": spec,
            # En erreur, on ré-affiche la saisie pour qu'elle ne soit pas perdue.
            "shown": value if error else spec.display(stored),
            "error": error,
            "saved": error is None,
        },
        status_code=422 if error else 200,
    )


async def _links_fragment(
    request: Request, candidate_id: int, error: str | None = None, status_code: int = 200
) -> HTMLResponse:
    context = await _fiche_or_404(request, candidate_id)
    context["link_error"] = error
    return templates.TemplateResponse(request, "_links.html", context, status_code=status_code)


@router.post("/candidates/{candidate_id}/links", response_model=None)
async def add_link(
    request: Request, candidate_id: int, url: Annotated[str, Form()] = ""
) -> Response:
    try:
        await resolve_add_link(request.app.state.pool, candidate_id, url)
    except (InvalidUrlError, LinkAlreadyAttachedError) as exc:
        return await _links_fragment(request, candidate_id, str(exc), status_code=422)
    except NotFoundError:
        raise HTTPException(status_code=404) from None
    return await _links_fragment(request, candidate_id)


@router.post("/links/{link_id}/delete", response_model=None)
async def remove_link(request: Request, link_id: int) -> Response:
    try:
        candidate_id = await resolve_remove_link(request.app.state.pool, link_id)
    except NotFoundError:
        raise HTTPException(status_code=404) from None
    return await _links_fragment(request, candidate_id)


@router.post("/candidates/{candidate_id}/delete", response_model=None)
async def delete_candidate(request: Request, candidate_id: int) -> Response:
    try:
        await resolve_delete_candidate(
            request.app.state.pool, request.app.state.image_store, candidate_id
        )
    except NotFoundError:
        raise HTTPException(status_code=404) from None
    # HX-Redirect : le bouton est un appel HTMX, une redirection HTTP ordinaire
    # remplacerait seulement le fragment ciblé par la page d'accueil entière.
    return Response(status_code=204, headers={"HX-Redirect": "/"})
