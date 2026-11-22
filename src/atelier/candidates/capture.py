from __future__ import annotations

from dataclasses import dataclass

import asyncpg
import structlog

from atelier.candidates import repository as repo
from atelier.candidates.link_key import resolve_link_key

log = structlog.get_logger(__name__)

_MAX_ATTEMPTS = 2


@dataclass(frozen=True)
class CaptureResult:
    candidate_id: int
    title: str | None
    created: bool


class _AlreadyKnown(Exception):
    """Interne : annule la transaction quand le lien existe déjà."""


async def resolve_capture_url(pool: asyncpg.Pool, raw_url: str) -> CaptureResult:
    """Crée une candidate et son premier lien, ou rend celle qui porte déjà ce lien.

    Lève InvalidUrlError si la chaîne n'est pas une URL. Seuls l'URL et le site
    sont renseignés : rien d'autre ne peut faire échouer une capture.
    """
    async with pool.acquire() as conn:
        key = await resolve_link_key(conn, raw_url)
        url, site_id = key.url, key.site_id
        # Deux essais : le second ne sert que si le lien existant a été supprimé
        # entre l'échec de l'insertion et sa relecture.
        for _ in range(_MAX_ATTEMPTS):
            try:
                # Idempotence jouée à l'écriture : on crée, et la contrainte
                # d'unicité tranche. Une lecture préalable laisserait passer deux
                # captures simultanées de la même URL.
                async with conn.transaction():
                    candidate_id = await repo.db_insert_candidate(conn)
                    if not await repo.db_insert_link_if_new(
                        conn, candidate_id, url.original, url.normalized, site_id
                    ):
                        raise _AlreadyKnown
            except _AlreadyKnown:
                existing = await repo.db_find_candidate_by_link(conn, url.normalized)
                if existing is not None:
                    log.info("capture_deja_connue", candidate_id=existing.id)
                    return CaptureResult(existing.id, existing.title, created=False)
                continue
            log.info("capture_creee", candidate_id=candidate_id, site_connu=site_id is not None)
            return CaptureResult(candidate_id, None, created=True)
    raise RuntimeError(f"capture instable après {_MAX_ATTEMPTS} essais : {url.normalized}")
