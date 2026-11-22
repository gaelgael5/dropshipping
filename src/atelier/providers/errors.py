"""Erreurs classées des fournisseurs : la bascule vers le repli dépend de la classe."""

from __future__ import annotations

import re
from enum import StrEnum

import httpx


class ErrorKind(StrEnum):
    MISSING_KEY = "clef_absente"
    AUTH = "authentification"  # clef refusée : basculer ne réparera pas la clef
    QUOTA = "quota"  # quota épuisé ou débit dépassé : le repli a du sens
    CONTENT = "contenu"  # refus de contenu : le repli peut accepter, ou refuser aussi
    UNAVAILABLE = "indisponible"  # panne, délai : le repli a du sens
    REQUEST = "requete"  # requête invalide (modèle inconnu…) : erreur de réglage


class ProviderError(Exception):
    def __init__(self, provider: str, kind: ErrorKind, detail: str) -> None:
        super().__init__(f"{provider} — {kind.value} : {detail}")
        self.provider, self.kind, self.detail = provider, kind, detail


def _message(payload: object) -> tuple[str, str]:
    """(message, code) extraits d'une réponse d'erreur OpenAI ou Gemini."""
    err = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(err, dict):
        return "", ""
    reasons = [d.get("reason", "") for d in err.get("details", []) if isinstance(d, dict)]
    code = str(err.get("code") or "") + " " + str(err.get("status") or "") + " " + " ".join(reasons)
    return str(err.get("message") or "")[:300], code


# Fragment de clef masqué tel que les fournisseurs le recopient (« sk-ab12****wxyz »).
_MASKED_KEY_RE = re.compile(r"\S*\*{3,}\S*")


def _redact(message: str, secret: str) -> str:
    # Constaté sur l'API OpenAI : une clef refusée est recopiée dans le message, début
    # et fin en clair. On retire la clef entière ET ses fragments masqués : ce message
    # est montré à l'écran et ne doit rien livrer de la clef, même partiellement.
    if secret:
        message = message.replace(secret, "[clef masquée]")
    return _MASKED_KEY_RE.sub("[clef masquée]", message)


def classify_response(provider: str, response: httpx.Response, secret: str = "") -> ProviderError:
    try:
        message, code = _message(response.json())
    except ValueError:
        message, code = "", ""
    message = _redact(message, secret)
    status = response.status_code
    # Gemini répond 400 (et non 401) à une clef invalide : on lit la raison.
    if status in (401, 403) or "API_KEY_INVALID" in code:
        kind = ErrorKind.AUTH
    elif status == 429 or "insufficient_quota" in code or "RESOURCE_EXHAUSTED" in code:
        kind = ErrorKind.QUOTA
    elif "content_policy" in code or "moderation" in code or "safety" in message.lower():
        kind = ErrorKind.CONTENT
    elif status >= 500:
        kind = ErrorKind.UNAVAILABLE
    else:
        kind = ErrorKind.REQUEST
    # Message expurgé ci-dessus : il peut être montré et journalisé.
    return ProviderError(provider, kind, f"HTTP {status} {message}".strip())


def transport_error(provider: str, exc: httpx.HTTPError) -> ProviderError:
    # Seul le type d'exception est repris : son texte peut contenir l'URL appelée.
    return ProviderError(provider, ErrorKind.UNAVAILABLE, f"échec réseau ({type(exc).__name__})")
