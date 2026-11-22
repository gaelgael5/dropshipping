"""Dépôt, liste et suppression des photos de candidates et des photos de référence."""

from __future__ import annotations

import asyncio

import asyncpg
import structlog

from atelier.images import repository as repo
from atelier.images.normalize import OUTPUT_SUFFIX, normalize_image
from atelier.images.store import ImageStore

log = structlog.get_logger(__name__)

REFERENCES_DIR = "references"
MAX_LABEL_LENGTH = 120


class CandidateNotFoundError(LookupError):
    pass


def candidate_dir(candidate_id: int) -> str:
    return f"candidates/{candidate_id}"


async def resolve_add_candidate_photo(
    pool: asyncpg.Pool, store: ImageStore, candidate_id: int, raw: bytes, kind: str = "visuel"
) -> repo.PhotoRow:
    """Normalise et enregistre une photo ; lève InvalidImageError ou CandidateNotFoundError.

    Ordre voulu : tout ce qui peut refuser (image invalide, candidate absente) passe
    AVANT l'écriture sur le volume, pour qu'un refus n'y laisse rien.
    """
    image = await asyncio.to_thread(normalize_image, raw)
    async with pool.acquire() as conn:
        if not await repo.db_candidate_exists(conn, candidate_id):
            raise CandidateNotFoundError(candidate_id)
        relative = await asyncio.to_thread(
            store.file_save, candidate_dir(candidate_id), image.data, OUTPUT_SUFFIX
        )
        try:
            photo = await repo.db_insert_candidate_photo(
                conn, candidate_id, kind, relative, image.width, image.height
            )
        except asyncpg.ForeignKeyViolationError:
            # Candidate supprimée entre la vérification et l'insertion.
            await asyncio.to_thread(store.file_delete, relative)
            raise CandidateNotFoundError(candidate_id) from None
        except BaseException:
            # Le fichier n'est référencé nulle part : on ne le laisse pas sur le volume.
            await asyncio.to_thread(store.file_delete, relative)
            raise
    log.info("photo_deposee", candidate_id=candidate_id, photo_id=photo.id, kind=kind)
    return photo


async def resolve_delete_candidate_photo(
    pool: asyncpg.Pool, store: ImageStore, photo_id: int
) -> repo.PhotoRow | None:
    async with pool.acquire() as conn:
        photo = await repo.db_delete_candidate_photo(conn, photo_id)
    if photo is None:
        return None
    # Fichier supprimé APRÈS la ligne : dans l'ordre inverse, un échec en base
    # laisserait une ligne pointant vers un fichier disparu.
    await asyncio.to_thread(store.file_delete, photo.file_path)
    log.info("photo_supprimee", photo_id=photo_id, candidate_id=photo.candidate_id)
    return photo


async def resolve_add_reference(
    pool: asyncpg.Pool, store: ImageStore, label: str, raw: bytes
) -> repo.ReferenceRow:
    clean_label = label.strip()[:MAX_LABEL_LENGTH] or "Référence"
    image = await asyncio.to_thread(normalize_image, raw)
    # Une référence est immuable : la « remplacer », c'est en déposer une nouvelle
    # et la désigner par défaut. Les photos déjà générées gardent ainsi la version
    # avec laquelle elles l'ont été.
    relative = await asyncio.to_thread(store.file_save, REFERENCES_DIR, image.data, OUTPUT_SUFFIX)
    try:
        async with pool.acquire() as conn:
            reference = await repo.db_insert_reference(
                conn, clean_label, relative, image.width, image.height
            )
    except BaseException:
        await asyncio.to_thread(store.file_delete, relative)
        raise
    log.info("reference_deposee", reference_id=reference.id, par_defaut=reference.is_default)
    return reference
