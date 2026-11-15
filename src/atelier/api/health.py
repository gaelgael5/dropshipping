from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from atelier.db import db_ping

router = APIRouter(prefix="/api", tags=["api"])


@router.get("/health")
async def read_health(request: Request) -> JSONResponse:
    # 503 et non 200 quand la base ne répond pas : un healthcheck de conteneur
    # ou un superviseur ne lit que le code, pas le corps.
    reachable = await db_ping(request.app.state.pool)
    return JSONResponse(
        {
            "status": "ok" if reachable else "degraded",
            "database": "reachable" if reachable else "unreachable",
        },
        status_code=200 if reachable else 503,
    )
