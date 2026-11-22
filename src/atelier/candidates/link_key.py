from __future__ import annotations

from dataclasses import dataclass

from atelier.candidates import repository as repo
from atelier.candidates.url_normalizer import (
    NormalizedUrl,
    candidate_domains,
    normalize_purchase_url,
    with_site_domain,
)


@dataclass(frozen=True)
class LinkKey:
    url: NormalizedUrl
    site_id: int | None


async def resolve_link_key(conn: repo._Conn, raw_url: str) -> LinkKey:
    """Clé de dédoublonnage d'un lien, identique pour la capture et l'ajout sur la fiche.

    Un seul calcul pour les deux chemins : s'ils divergeaient, un lien refusé à la
    capture comme doublon pourrait être ajouté sur une fiche, ou l'inverse.
    Lève InvalidUrlError si la chaîne n'est pas une URL.
    """
    url = normalize_purchase_url(raw_url)
    site = await repo.db_find_site(conn, candidate_domains(url.host))
    if site is None:
        return LinkKey(url, None)
    return LinkKey(with_site_domain(url, site.domain), site.id)
