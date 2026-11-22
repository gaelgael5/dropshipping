from __future__ import annotations

import json
import secrets
from typing import Annotated

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError

from atelier.candidates.capture import resolve_capture_url
from atelier.candidates.url_normalizer import InvalidUrlError

router = APIRouter(prefix="/api", tags=["api"])

# En-tête dédié plutôt qu'« Authorization: Bearer » : le portail qui relaie
# l'atelier retire l'en-tête Authorization du visiteur avant de le transmettre,
# le jeton n'arriverait jamais. Jamais en paramètre d'URL non plus : il finirait
# dans les journaux du proxy et l'historique du navigateur (interdit du ticket).
CAPTURE_TOKEN_HEADER = "X-Capture-Token"  # noqa: S105 — nom d'en-tête, pas un secret


class CaptureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str


def _is_authorized(request: Request, presented: str | None) -> bool:
    expected = request.app.state.settings.capture_token.get_secret_value()
    # Comparaison à temps constant : une comparaison ordinaire s'arrête au premier
    # caractère différent, et sa durée renseigne sur le préfixe correct du jeton.
    return presented is not None and secrets.compare_digest(presented.encode(), expected.encode())


@router.post("/capture")
async def create_capture(
    request: Request,
    x_capture_token: Annotated[str | None, Header(alias=CAPTURE_TOKEN_HEADER)] = None,
) -> JSONResponse:
    # Le jeton est vérifié AVANT de lire le corps : un appel non authentifié reçoit
    # la même réponse quelle que soit l'URL, sans rien révéler de ce qui est connu.
    if not _is_authorized(request, x_capture_token):
        return JSONResponse({"error": "jeton de capture absent ou invalide"}, status_code=401)
    try:
        body = CaptureRequest.model_validate(json.loads(await request.body()))
    except (json.JSONDecodeError, ValidationError):
        return JSONResponse({"error": 'corps attendu : {"url": "https://..."}'}, status_code=422)
    try:
        result = await resolve_capture_url(request.app.state.pool, body.url)
    except InvalidUrlError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    # Le raccourci du téléphone affiche cette réponse : sans « created » et le
    # message, on ne saurait pas si l'envoi a abouti ni s'il a créé quelque chose.
    return JSONResponse(
        {
            "candidate_id": result.candidate_id,
            "title": result.title,
            "created": result.created,
            "message": "Candidate créée" if result.created else "Déjà capturée",
        },
        status_code=201 if result.created else 200,
    )
