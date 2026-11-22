from __future__ import annotations

from pathlib import Path

import asyncpg
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from atelier.api import capture, health
from atelier.config import Settings
from atelier.images.store import ImageStore
from atelier.web import pages, photos

_PACKAGE_DIR = Path(__file__).parent


def create_app(settings: Settings, pool: asyncpg.Pool) -> FastAPI:
    """Assemble l'application à partir de ressources déjà ouvertes.

    La base et le volume sont vérifiés AVANT (voir __main__) plutôt que dans un
    lifespan FastAPI : un échec dans le lifespan est rapporté par uvicorn sous
    forme de trace brute, alors que le socle exige un message explicite.
    """
    app = FastAPI(title="Atelier d'annonces candidates")
    app.state.settings = settings
    app.state.pool = pool
    app.state.image_store = ImageStore(settings.images_root)
    app.mount("/static", StaticFiles(directory=_PACKAGE_DIR / "static"), name="static")
    # Une surface = un routeur : pages HTML, API HTTP, et plus tard outils MCP,
    # pour qu'elles évoluent (auth, préfixe, format d'erreur) indépendamment.
    app.include_router(pages.router)
    app.include_router(photos.router)
    app.include_router(health.router)
    app.include_router(capture.router)
    return app
