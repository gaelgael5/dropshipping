"""Écran de l'arbre de catégories, et classement d'une candidate depuis sa fiche."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from atelier.candidates import categories as cats
from atelier.candidates import category_admin as admin
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


_OPEN = "document.getElementById('dialog').showModal()"


def _form_values(form: Any) -> dict[str, str]:
    return {name: str(form.get(name, "")) for name in admin.FORM_FIELDS}


async def _dialog(
    request: Request, category_id: int | None, parent_id: int | None,
    values: dict[str, str] | None = None, error: str | None = None,
) -> HTMLResponse:  # fmt: skip
    """Fenêtre d'ajout (category_id None) ou de modification d'une catégorie."""
    async with request.app.state.pool.acquire() as conn:
        rows = await admin.db_list_category_rows(conn)
        current = await cats.db_get_category(conn, category_id) if category_id else None
        if category_id and current is None:
            raise HTTPException(status_code=404)
        mappings = await cats.db_list_mappings(conn, category_id) if category_id else []
        # Ce qu'une valeur laissée vide hériterait : les défauts effectifs du parent.
        inherited = await cats.db_effective_defaults(conn, parent_id) if parent_id else {}
    if values is None:
        values = {k: "" if current is None or current[k] is None else str(current[k])
                  for k in admin.FORM_FIELDS}  # fmt: skip
    excluded = admin.descendants_of(rows, category_id) if category_id else set()
    context = {
        "category_id": category_id, "parent_id": parent_id, "values": values, "error": error,
        "parents": [r for r in rows if r.id not in excluded], "mappings": mappings,
        "inherited": inherited, "fields": {n: CATEGORY_FIELDS[n] for n in admin.FORM_FIELDS},
    }  # fmt: skip
    # 200 même en erreur : HTMX n'insère pas une 4xx, la fenêtre doit garder son message.
    return templates.TemplateResponse(request, "_category_form.html", context)


def _done() -> Response:
    return Response(status_code=204, headers={"HX-Redirect": "/categories"})


@router.get("/categories")
async def show_categories(request: Request) -> HTMLResponse:
    async with request.app.state.pool.acquire() as conn:
        rows = await admin.db_list_category_rows(conn)
    return templates.TemplateResponse(
        request, "categories.html", {"rows": rows, "default_fields": admin.DEFAULT_FIELDS,
                                     "fields": CATEGORY_FIELDS, "open": _OPEN},
    )  # fmt: skip


@router.get("/categories/new")
async def new_category_form(request: Request, parent_id: str = "") -> HTMLResponse:
    return await _dialog(request, None, _optional_id(parent_id))


@router.get("/categories/{category_id:int}")
async def old_category_link(category_id: int) -> Response:
    # Ancienne adresse d'une catégorie (avant la liste en lignes) : on ramène à la liste.
    return RedirectResponse("/categories", status_code=303)


@router.get("/categories/{category_id:int}/edit")
async def edit_category_form(request: Request, category_id: int) -> HTMLResponse:
    async with request.app.state.pool.acquire() as conn:
        current = await cats.db_get_category(conn, category_id)
    if current is None:
        raise HTTPException(status_code=404)
    return await _dialog(request, category_id, current["parent_id"])


@router.post("/categories", response_model=None)
async def create_category(request: Request) -> Response:
    form = await request.form()
    parent_id, values = _optional_id(str(form.get("parent_id", ""))), _form_values(form)
    try:
        await admin.resolve_create_category(request.app.state.pool, parent_id, values)
    except FieldValueError as exc:
        return await _dialog(request, None, parent_id, values, str(exc))
    except cats.CategoryNotFoundError:
        return await _dialog(request, None, None, values, "Catégorie parente introuvable.")
    return _done()


@router.post("/categories/{category_id:int}", response_model=None)
async def update_category(request: Request, category_id: int) -> Response:
    form = await request.form()
    parent_id, values = _optional_id(str(form.get("parent_id", ""))), _form_values(form)
    try:
        await admin.resolve_update_category(request.app.state.pool, category_id, parent_id, values)
    except (FieldValueError, cats.CategoryCycleError) as exc:
        return await _dialog(request, category_id, parent_id, values, str(exc))
    except cats.CategoryNotFoundError:
        raise HTTPException(status_code=404) from None
    return _done()


@router.post("/categories/{category_id:int}/delete", response_model=None)
async def delete_category(request: Request, category_id: int) -> Response:
    try:
        await cats.resolve_delete_category(request.app.state.pool, category_id)
    except cats.CategoryInUseError as exc:
        # Refus montré dans la fenêtre (la liste n'a pas changé, rien à recharger).
        return templates.TemplateResponse(request, "_category_refused.html", {"message": str(exc)})
    except cats.CategoryNotFoundError:
        raise HTTPException(status_code=404) from None
    return _done()


@router.post("/categories/{category_id:int}/mappings", response_model=None)
async def set_mapping(
    request: Request,
    category_id: int,
    platform: Annotated[str, Form()] = "",
    platform_category_id: Annotated[str, Form()] = "",
) -> Response:
    error = None
    try:
        await cats.resolve_set_mapping(
            request.app.state.pool, category_id, platform, platform_category_id
        )
    except ValueError as exc:
        error = str(exc)
    except cats.CategoryNotFoundError:
        raise HTTPException(status_code=404) from None
    return await _reopen(request, category_id, error)


@router.post("/categories/{category_id:int}/mappings/{platform}/delete", response_model=None)
async def delete_mapping(request: Request, category_id: int, platform: str) -> Response:
    async with request.app.state.pool.acquire() as conn:
        if not await cats.db_delete_mapping(conn, category_id, platform):
            raise HTTPException(status_code=404)
    return await _reopen(request, category_id, None)


async def _reopen(request: Request, category_id: int, error: str | None) -> HTMLResponse:
    """Rend la fenêtre de modification à jour après un changement de correspondance."""
    async with request.app.state.pool.acquire() as conn:
        current = await cats.db_get_category(conn, category_id)
    if current is None:
        raise HTTPException(status_code=404)
    return await _dialog(request, category_id, current["parent_id"], error=error)


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
