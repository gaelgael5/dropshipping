from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from atelier.candidates.capture import resolve_capture_url
from atelier.candidates.repository import CandidateRow, db_list_candidates
from atelier.candidates.url_normalizer import InvalidUrlError
from atelier.web.templating import templates

router = APIRouter(tags=["pages"], default_response_class=HTMLResponse)

PAGE_SIZE = 50
_CAPTURE_MESSAGES = {
    "created": "Candidate créée.",
    "existing": "Cet article était déjà capturé : voici sa candidate.",
}


async def _load_page(request: Request, before: int | None) -> tuple[list[CandidateRow], int | None]:
    # Une ligne de plus que la page : sa présence dit s'il reste une suite, sans
    # requête de comptage sur toute la table.
    async with request.app.state.pool.acquire() as conn:
        rows = await db_list_candidates(conn, before, PAGE_SIZE + 1)
    page = rows[:PAGE_SIZE]
    next_before = page[-1].id if len(rows) > PAGE_SIZE else None
    return page, next_before


@router.get("/")
async def show_queue(
    request: Request,
    capture: Annotated[str | None, Query()] = None,
    highlight: Annotated[int | None, Query()] = None,
) -> HTMLResponse:
    rows, next_before = await _load_page(request, None)
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "rows": rows,
            "next_before": next_before,
            "flash": _CAPTURE_MESSAGES.get(capture or ""),
            "highlight": highlight,
        },
    )


@router.get("/candidates/rows")
async def show_more_rows(request: Request, before: Annotated[int, Query(gt=0)]) -> HTMLResponse:
    rows, next_before = await _load_page(request, before)
    return templates.TemplateResponse(
        request, "_candidate_rows.html", {"rows": rows, "next_before": next_before}
    )


@router.post("/capture", response_model=None)
async def submit_capture(request: Request, url: Annotated[str, Form()] = "") -> Response:
    try:
        result = await resolve_capture_url(request.app.state.pool, url)
    except InvalidUrlError as exc:
        rows, next_before = await _load_page(request, None)
        return templates.TemplateResponse(
            request,
            "index.html",
            {"rows": rows, "next_before": next_before, "error": str(exc), "submitted": url},
            status_code=422,
        )
    # Redirection après POST : recharger la page ne renvoie pas le formulaire.
    outcome = "created" if result.created else "existing"
    return RedirectResponse(f"/?capture={outcome}&highlight={result.candidate_id}", status_code=303)
