"""Chiffrement des valeurs du coffre (AES-256-GCM)."""

from __future__ import annotations

import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE_BYTES = 12  # taille recommandée pour GCM ; un nonce neuf par chiffrement


class SecretDecryptError(ValueError):
    pass


class SecretCipher:
    """Porte la clef maîtresse ; ne l'expose ni en repr, ni en attribut public."""

    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError("la clef maîtresse doit faire 32 octets")
        self._aead = AESGCM(key)

    def __repr__(self) -> str:
        return "SecretCipher(<clef masquée>)"

    def encrypt(self, plaintext: str, secret_id: int) -> bytes:
        nonce = os.urandom(_NONCE_BYTES)
        return nonce + self._aead.encrypt(nonce, plaintext.encode(), _aad(secret_id))

    def decrypt(self, blob: bytes, secret_id: int) -> str:
        nonce, body = blob[:_NONCE_BYTES], blob[_NONCE_BYTES:]
        try:
            return self._aead.decrypt(nonce, body, _aad(secret_id)).decode()
        except InvalidTag:
            # Deux causes, indiscernables par construction : clef maîtresse changée, ou
            # ligne recopiée sous un autre identifiant. Dans les deux cas, on refuse.
            raise SecretDecryptError(
                "valeur illisible avec la clef maîtresse actuelle (clef changée, ou ligne recopiée)"
            ) from None


def _aad(secret_id: int) -> bytes:
    # L'identifiant de la ligne est authentifié avec la valeur : un chiffré copié sur
    # une autre ligne (pour lire une clef sous un nom moins surveillé) ne se déchiffre
    # pas. On n'y met PAS le nom, pour que renommer ne demande pas de re-chiffrer.
    return f"atelier-secret:{secret_id}".encode()
