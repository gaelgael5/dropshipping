"""Fabrication et empreinte des jetons (clefs d'API, jetons OAuth, codes)."""

from __future__ import annotations

import hashlib
import secrets

# Préfixes lisibles : une clef retrouvée dans un fichier ou un message se reconnaît, et
# les outils de détection de fuites peuvent la repérer.
API_KEY_PREFIX = "atl_key_"
ACCESS_TOKEN_PREFIX = "atl_at_"  # noqa: S105 — préfixe public, pas un secret
REFRESH_TOKEN_PREFIX = "atl_rt_"  # noqa: S105 — préfixe public, pas un secret
AUTH_CODE_PREFIX = "atl_code_"
_TOKEN_BYTES = 32  # 256 bits : impossible à deviner, d'où une simple empreinte en base


def new_token(prefix: str) -> str:
    return prefix + secrets.token_urlsafe(_TOKEN_BYTES)


def token_hash(raw: str) -> bytes:
    return hashlib.sha256(raw.encode()).digest()
