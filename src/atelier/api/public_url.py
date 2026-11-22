from __future__ import annotations

from fastapi import Request


def resolve_public_base(request: Request) -> str:
    """Adresse publique de l'atelier, telle que les clients OAuth/MCP doivent la voir.

    ATELIER_PUBLIC_URL prime : derrière un relais, l'adresse vue par l'application
    (hôte interne) n'est pas celle que claude.ai appelle, et des métadonnées OAuth
    pointant vers l'hôte interne seraient inutilisables. À défaut, l'adresse de la
    requête, qui suffit en accès direct.
    """
    configured: str | None = request.app.state.settings.public_url
    return configured or str(request.base_url).rstrip("/")
