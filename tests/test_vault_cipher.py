from __future__ import annotations

import pytest

from atelier.vault.cipher import SecretCipher, SecretDecryptError

KEY = bytes(range(32))


def test_cipher_roundtrip_returns_plaintext() -> None:
    cipher = SecretCipher(KEY)
    assert cipher.decrypt(cipher.encrypt("sk-abc", 7), 7) == "sk-abc"


def test_cipher_ciphertext_does_not_contain_plaintext_and_varies() -> None:
    cipher = SecretCipher(KEY)
    first, second = cipher.encrypt("sk-abcdef", 1), cipher.encrypt("sk-abcdef", 1)
    assert b"sk-abcdef" not in first
    assert first != second  # nonce neuf à chaque chiffrement


def test_cipher_other_row_id_refuses_to_decrypt() -> None:
    cipher = SecretCipher(KEY)
    with pytest.raises(SecretDecryptError):
        cipher.decrypt(cipher.encrypt("sk-abc", 1), 2)


def test_cipher_other_key_refuses_to_decrypt() -> None:
    blob = SecretCipher(KEY).encrypt("sk-abc", 1)
    with pytest.raises(SecretDecryptError, match="clef maîtresse"):
        SecretCipher(bytes(32)).decrypt(blob, 1)


def test_cipher_repr_hides_key_and_short_key_refused() -> None:
    assert KEY.hex() not in repr(SecretCipher(KEY))
    with pytest.raises(ValueError, match="32 octets"):
        SecretCipher(b"short")
