"""Écran de l'arbre de catégories, et classement d'une candidate depuis sa fiche."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from atelier.candidates import categories as cats
from atelier.candidates.classify import (
    CandidateNotFoundError,
    resolve_apply_category_defaults,
    resolve_classify_candidate,
)
from atelier.candidates.fields import CANDIDATE_PREP_FIELDS, CATEGORY_FIELDS, FieldValueError
from atelier.web.templating import templates

router = APIRouter(tags=["categories"], default_response_class=HTMLResponse)


def _optional_id(raw: str) -> int | None:
    value = raw.strip()
    if not value:
        return None
    if not value.isdigit():
        raise HTTPException(status_code=422)
    return int(value)


async def tree_options(request: Request) -> list[cats.CategoryNode]:
    async with request.app.state.pool.acquire() as conn:
        rows = await cats.db_list_categories(conn)
    return cats.flatten(cats.build_tree(rows))


async def _page(
    request: Request, category_id: int | None, error: str | None = None, status: int = 200
) -> HTMLResponse:
    async with request.app.state.pool.acquire() as conn:
        rows = await cats.db_list_categories(conn)
        selected = await cats.db_get_category(conn, category_id) if category_id else None
        if category_id and selected is None:
            raise HTTPException(status_code=404)
        context: dict[str, Any] = {"selected": selected, "error": error}
        if selected is not None:
            context["mappings"] = await cats.db_list_mappings(conn, selected["id"])
            context["effective"] = await cats.db_effective_defaults(conn, selected["id"])
    roots = cats.build_tree(rows)
    context.update(roots=roots, options=cats.flatten(roots), category_fields=CATEGORY_FIELDS)
    return templates.TemplateResponse(request, "categories.html", context, status_code=status)


@router.get("/categories")
async def show_categories(request: Request) -> HTMLResponse:
    return await _page(request, None)


@router.get("/categories/{category_id}")
async def show_category(request: Request, category_id: int) -> HTMLResponse:
    return await _page(request, category_id)


@router.post("/categories", response_model=None)
async def create_category(
    request: Request,
    label: Annotated[str, Form()] = "",
    parent_id: Annotated[str, Form()] = "",
) -> Response:
    try:
        new_id = await cats.resolve_create_category(
            request.app.state.pool, label, _optional_id(parent_id)
        )
    except FieldValueError as exc:
        return await _page(request, None, f"Libellé : {exc}", 422)
    except cats.CategoryNotFoundError:
        raise HTTPException(status_code=404) from None
    return RedirectResponse(f"/categories/{new_id}", status_code=303)


@router.post("/categories/{category_id}/move", response_model=None)
async def move_category(
    request: Request, category_id: int, parent_id: Annotated[str, Form()] = ""
) -> Response:
    try:
        await cats.resolve_move_category(
            request.app.state.pool, category_id, _optional_id(parent_id)
        )
    except cats.CategoryCycleError as exc:
        return await _page(request, category_id, str(exc), 422)
    except cats.CategoryNotFoundError:
        raise HTTPException(status_code=404) from None
    return RedirectResponse(f"/categories/{category_id}", status_code=303)


@router.post("/categories/{category_id}/delete", response_model=None)
async def delete_category(request: Request, category_id: int) -> Response:
    try:
        await cats.resolve_delete_category(request.app.state.pool, category_id)
    except cats.CategoryInUseError as exc:
        return await _page(request, category_id, str(exc), 422)
    except cats.CategoryNotFoundError:
        raise HTTPException(status_code=404) from None
    return RedirectResponse("/categories", status_code=303)


@router.post("/categories/{category_id}/mappings", response_model=None)
async def set_mapping(
    request: Request,
    category_id: int,
    platform: Annotated[str, Form()] = "",
    platform_category_id: Annotated[str, Form()] = "",
) -> Response:
    try:
        await cats.resolve_set_mapping(
            request.app.state.pool, category_id, platform, platform_category_id
        )
    except ValueError as exc:
        return await _page(request, category_id, str(exc), 422)
    except cats.CategoryNotFoundError:
        raise HTTPException(status_code=404) from None
    return RedirectResponse(f"/categories/{category_id}", status_code=303)


@router.post("/categories/{category_id}/mappings/{platform}/delete", response_model=None)
async def delete_mapping(request: Request, category_id: int, platform: str) -> Response:
    async with request.app.state.pool.acquire() as conn:
        if not await cats.db_delete_mapping(conn, category_id, platform):
            raise HTTPException(status_code=404)
    return RedirectResponse(f"/categories/{category_id}", status_code=303)


@router.post("/candidates/{candidate_id}/category", response_model=None)
async def classify_candidate(
    request: Request, candidate_id: int, category_id: Annotated[str, Form()] = ""
) -> Response:
    try:
        result = await resolve_classify_candidate(
            request.app.state.pool, candidate_id, _optional_id(category_id)
        )
    except (CandidateNotFoundError, cats.CategoryNotFoundError):
        raise HTTPException(status_code=404) from None
    if result.applied:
        # Des champs ailleurs sur la page viennent d'être remplis : on la recharge.
        return Response(status_code=204, headers={"HX-Refresh": "true"})
    labels = {"description": "Description"} | {
        name: spec.label for name, spec in CANDIDATE_PREP_FIELDS.items()
    }
    return templates.TemplateResponse(
        request,
        "_candidate_category.html",
        {
            "candidate_id": candidate_id,
            "current_category_id": _optional_id(category_id),
            "options": await tree_options(request),
            "proposal": [(labels[col], value) for col, value in result.proposal.items()],
        },
    )


@router.get("/candidates/{candidate_id}/category")
async def show_candidate_category(request: Request, candidate_id: int) -> HTMLResponse:
    async with request.app.state.pool.acquire() as conn:
        row = await conn.fetchrow("SELECT category_id FROM candidates WHERE id = $1", candidate_id)
    if row is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request,
        "_candidate_category.html",
        {
            "candidate_id": candidate_id,
            "current_category_id": row["category_id"],
            "options": await tree_options(request),
            "proposal": [],
        },
    )


@router.post("/candidates/{candidate_id}/apply-category-defaults", response_model=None)
async def apply_defaults(request: Request, candidate_id: int) -> Response:
    try:
        await resolve_apply_category_defaults(request.app.state.pool, candidate_id)
    except CandidateNotFoundError:
        raise HTTPException(status_code=404) from None
    return Response(status_code=204, headers={"HX-Refresh": "true"})
