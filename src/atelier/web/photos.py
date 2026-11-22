from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

from atelier.candidates.repository import db_get_candidate
from atelier.images import repository as repo
from atelier.images.normalize import MAX_UPLOAD_BYTES, InvalidImageError
from atelier.images.photos import (
    CandidateNotFoundError,
    resolve_add_candidate_photo,
    resolve_add_reference,
    resolve_delete_candidate_photo,
)
from atelier.images.store import ImageStore, UnsafePathError
from atelier.web.templating import templates

router = APIRouter(tags=["photos"], default_response_class=HTMLResponse)


def _store(request: Request) -> ImageStore:
    store: ImageStore = request.app.state.image_store
    return store


async def _read_capped(upload: UploadFile) -> bytes:
    # Lecture plafonnée : on ne charge pas en mémoire un fichier de plusieurs Go
    # pour découvrir ensuite qu'il dépasse la limite.
    data = await upload.read(MAX_UPLOAD_BYTES + 1)
    return data


@router.get("/media/{relative:path}", response_model=None)
async def serve_media(request: Request, relative: str) -> FileResponse:
    try:
        # Le chemin vient de la requête : il est TOUJOURS ramené sous la racine.
        path = _store(request).resolve(relative)
    except UnsafePathError:
        raise HTTPException(status_code=404) from None
    if not path.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(path)


async def _photos_view(request: Request, candidate_id: int) -> list[dict[str, object]]:
    async with request.app.state.pool.acquire() as conn:
        photos = await repo.db_list_candidate_photos(conn, candidate_id)
    store = _store(request)
    # Un fichier absent du volume s'affiche « manquant » au lieu de faire tomber
    # l'écran : la ligne en base et le volume peuvent diverger (restauration partielle).
    return [
        {"photo": p, "url": store.url_for(p.file_path), "missing": not _exists(store, p.file_path)}
        for p in photos
    ]


def _exists(store: ImageStore, relative: str) -> bool:
    try:
        return store.file_exists(relative)
    except UnsafePathError:
        return False


@router.get("/candidates/{candidate_id}")
async def show_candidate(request: Request, candidate_id: int) -> HTMLResponse:
    async with request.app.state.pool.acquire() as conn:
        candidate = await db_get_candidate(conn, candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request,
        "candidate.html",
        {
            "candidate": candidate,
            "candidate_id": candidate_id,
            "photos": await _photos_view(request, candidate_id),
        },
    )


@router.get("/candidates/{candidate_id}/photos")
async def show_candidate_photos(request: Request, candidate_id: int) -> HTMLResponse:
    async with request.app.state.pool.acquire() as conn:
        if not await repo.db_candidate_exists(conn, candidate_id):
            raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request,
        "_candidate_photos.html",
        {"candidate_id": candidate_id, "photos": await _photos_view(request, candidate_id)},
    )


@router.post("/candidates/{candidate_id}/photos", response_model=None)
async def upload_candidate_photo(
    request: Request, candidate_id: int, file: Annotated[UploadFile, File()]
) -> Response:
    error = None
    try:
        await resolve_add_candidate_photo(
            request.app.state.pool, _store(request), candidate_id, await _read_capped(file)
        )
    except InvalidImageError as exc:
        error = str(exc)
    except CandidateNotFoundError:
        raise HTTPException(status_code=404) from None
    return templates.TemplateResponse(
        request,
        "_candidate_photos.html",
        {
            "candidate_id": candidate_id,
            "photos": await _photos_view(request, candidate_id),
            "error": error,
        },
        status_code=422 if error else 200,
    )


@router.post("/photos/{photo_id}/delete", response_model=None)
async def delete_candidate_photo(request: Request, photo_id: int) -> Response:
    photo = await resolve_delete_candidate_photo(request.app.state.pool, _store(request), photo_id)
    if photo is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request,
        "_candidate_photos.html",
        {
            "candidate_id": photo.candidate_id,
            "photos": await _photos_view(request, photo.candidate_id),
        },
    )


async def _references_page(
    request: Request, error: str | None = None, status_code: int = 200
) -> HTMLResponse:
    async with request.app.state.pool.acquire() as conn:
        references = await repo.db_list_references(conn)
    store = _store(request)
    items = [
        {"ref": r, "url": store.url_for(r.file_path), "missing": not _exists(store, r.file_path)}
        for r in references
    ]
    return templates.TemplateResponse(
        request, "references.html", {"items": items, "error": error}, status_code=status_code
    )


@router.get("/references")
async def show_references(request: Request) -> HTMLResponse:
    return await _references_page(request)


@router.post("/references", response_model=None)
async def upload_reference(
    request: Request,
    file: Annotated[UploadFile, File()],
    label: Annotated[str, Form()] = "",
) -> Response:
    try:
        await resolve_add_reference(
            request.app.state.pool, _store(request), label, await _read_capped(file)
        )
    except InvalidImageError as exc:
        return await _references_page(request, error=str(exc), status_code=422)
    return RedirectResponse("/references", status_code=303)


@router.post("/references/{reference_id}/default", response_model=None)
async def set_default_reference(request: Request, reference_id: int) -> Response:
    async with request.app.state.pool.acquire() as conn:
        if not await repo.db_set_default_reference(conn, reference_id):
            raise HTTPException(status_code=404)
    return RedirectResponse("/references", status_code=303)
