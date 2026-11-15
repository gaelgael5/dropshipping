from __future__ import annotations

import asyncio
import logging
import sys

import structlog
import uvicorn

from atelier.app import create_app
from atelier.config import Settings, load_settings
from atelier.db import db_open_pool
from atelier.errors import StartupError
from atelier.migrations_runner import MIGRATIONS_DIR, resolve_apply_migrations
from atelier.storage import file_check_images_root_writable

log = structlog.get_logger(__name__)


def _configure_logging() -> None:
    # Journaux JSON sur la sortie standard : c'est le flux du conteneur qui est collecté.
    logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
    )


async def _serve(settings: Settings) -> None:
    await asyncio.to_thread(file_check_images_root_writable, settings.images_root)
    # Le secret n'est déballé qu'ici, au point où la connexion l'exige.
    pool = await db_open_pool(
        settings.database_url.get_secret_value(), settings.db_connect_timeout_s
    )
    try:
        applied = await resolve_apply_migrations(pool, MIGRATIONS_DIR)
        log.info("demarrage", migrations_appliquees=applied)
        app = create_app(settings, pool)
        config = uvicorn.Config(app, host=settings.http_host, port=settings.http_port)
        await uvicorn.Server(config).serve()
    finally:
        await pool.close()


def main() -> int:
    _configure_logging()
    try:
        asyncio.run(_serve(load_settings()))
    except StartupError as exc:
        log.error("demarrage_impossible", raison=str(exc))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
