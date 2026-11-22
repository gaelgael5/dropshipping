from __future__ import annotations

import asyncpg
import pytest
from structlog.testing import capture_logs

from atelier.vault import service as vault
from atelier.vault.cipher import SecretCipher, SecretDecryptError

CIPHER = SecretCipher(bytes(range(32)))
VALUE = "sk-live-Zx81QpLm"


async def _ia(pool: asyncpg.Pool) -> int:
    return int(await pool.fetchval("SELECT id FROM secret_categories WHERE slug = 'ia'"))


async def test_add_secret_then_resolve_by_ref_returns_value(migrated_pool: asyncpg.Pool) -> None:
    await vault.resolve_add_secret(
        migrated_pool, CIPHER, await _ia(migrated_pool), "openai", f" {VALUE}\n"
    )
    value = await vault.resolve_secret_value(migrated_pool, CIPHER, "ia/openai")
    assert value.get_secret_value() == VALUE
    assert VALUE not in repr(value)
    used = await migrated_pool.fetchval("SELECT last_used_at FROM secrets")
    assert used is not None


async def test_stored_value_is_encrypted(migrated_pool: asyncpg.Pool) -> None:
    await vault.resolve_add_secret(migrated_pool, CIPHER, await _ia(migrated_pool), "openai", VALUE)
    blob = await migrated_pool.fetchval("SELECT ciphertext FROM secrets")
    assert VALUE.encode() not in bytes(blob)


async def test_row_copied_under_other_name_does_not_decrypt(migrated_pool: asyncpg.Pool) -> None:
    ia = await _ia(migrated_pool)
    await vault.resolve_add_secret(migrated_pool, CIPHER, ia, "openai", VALUE)
    await migrated_pool.execute(
        "INSERT INTO secrets (id, category_id, name, ciphertext)"
        " SELECT nextval('secrets_id_seq'), category_id, 'copie', ciphertext FROM secrets"
    )
    with pytest.raises(SecretDecryptError):
        await vault.resolve_secret_value(migrated_pool, CIPHER, "ia/copie")


async def test_unknown_or_malformed_ref_is_explicit_error(migrated_pool: asyncpg.Pool) -> None:
    with pytest.raises(vault.VaultNotFoundError, match="ia/absent"):
        await vault.resolve_secret_value(migrated_pool, CIPHER, "ia/absent")
    with pytest.raises(vault.VaultError, match="référence"):
        await vault.resolve_secret_value(migrated_pool, CIPHER, "../x")


async def test_duplicate_name_in_category_refused(migrated_pool: asyncpg.Pool) -> None:
    ia = await _ia(migrated_pool)
    await vault.resolve_add_secret(migrated_pool, CIPHER, ia, "openai", VALUE)
    with pytest.raises(vault.VaultError, match="existe déjà"):
        await vault.resolve_add_secret(migrated_pool, CIPHER, ia, "openai", "autre")


@pytest.mark.parametrize("name", ["", "Majuscule", "1chiffre", "a/b", "x" * 65])
async def test_invalid_name_refused(migrated_pool: asyncpg.Pool, name: str) -> None:
    with pytest.raises(vault.VaultError, match="nom du secret"):
        await vault.resolve_add_secret(migrated_pool, CIPHER, await _ia(migrated_pool), name, VALUE)


async def test_empty_value_and_unknown_category_refused(migrated_pool: asyncpg.Pool) -> None:
    with pytest.raises(vault.VaultError, match="vide"):
        await vault.resolve_add_secret(migrated_pool, CIPHER, await _ia(migrated_pool), "k", "  ")
    with pytest.raises(vault.VaultNotFoundError):
        await vault.resolve_add_secret(migrated_pool, CIPHER, 99_999, "k", VALUE)
    assert await migrated_pool.fetchval("SELECT count(*) FROM secrets") == 0


async def test_update_with_empty_value_keeps_current_value(migrated_pool: asyncpg.Pool) -> None:
    ia = await _ia(migrated_pool)
    sid = await vault.resolve_add_secret(migrated_pool, CIPHER, ia, "k", VALUE)
    await vault.resolve_update_secret(migrated_pool, CIPHER, sid, ia, "k", "décrit", "  ")
    assert (
        await vault.resolve_secret_value(migrated_pool, CIPHER, "ia/k")
    ).get_secret_value() == VALUE
    async with migrated_pool.acquire() as conn:
        info = await vault.db_get_secret(conn, sid)
    assert info is not None and info.description == "décrit"


async def test_update_value_rename_and_move_category(migrated_pool: asyncpg.Pool) -> None:
    ia = await _ia(migrated_pool)
    other = int(
        await migrated_pool.fetchval("SELECT id FROM secret_categories WHERE slug='divers'")
    )
    sid = await vault.resolve_add_secret(migrated_pool, CIPHER, ia, "k", VALUE)
    await vault.resolve_update_secret(migrated_pool, CIPHER, sid, other, "k2", "", "nouvelle")
    # Déplacée et renommée sans rechiffrement : la valeur se lit sous la nouvelle référence.
    assert (
        await vault.resolve_secret_value(migrated_pool, CIPHER, "divers/k2")
    ).get_secret_value() == "nouvelle"
    with pytest.raises(vault.VaultNotFoundError):
        await vault.resolve_secret_value(migrated_pool, CIPHER, "ia/k")


async def test_update_refusals(migrated_pool: asyncpg.Pool) -> None:
    ia = await _ia(migrated_pool)
    sid = await vault.resolve_add_secret(migrated_pool, CIPHER, ia, "k", VALUE)
    await vault.resolve_add_secret(migrated_pool, CIPHER, ia, "autre", VALUE)
    with pytest.raises(vault.VaultError, match="existe déjà"):
        await vault.resolve_update_secret(migrated_pool, CIPHER, sid, ia, "autre", "", "")
    with pytest.raises(vault.VaultError, match="nom du secret"):
        await vault.resolve_update_secret(migrated_pool, CIPHER, sid, ia, "Bad", "", "")
    with pytest.raises(vault.VaultNotFoundError):
        await vault.resolve_update_secret(migrated_pool, CIPHER, sid, 99_999, "k", "", "")
    with pytest.raises(vault.VaultNotFoundError):
        await vault.resolve_update_secret(migrated_pool, CIPHER, 99_999, ia, "k", "", "")


async def test_delete_secret(migrated_pool: asyncpg.Pool) -> None:
    sid = await vault.resolve_add_secret(
        migrated_pool, CIPHER, await _ia(migrated_pool), "k", VALUE
    )
    await vault.resolve_delete_secret(migrated_pool, sid)
    with pytest.raises(vault.VaultNotFoundError):
        await vault.resolve_delete_secret(migrated_pool, sid)


async def test_category_lifecycle_and_non_empty_refusal(migrated_pool: asyncpg.Pool) -> None:
    cid = await vault.resolve_create_category(migrated_pool, "transport", "Transporteurs")
    with pytest.raises(vault.VaultError, match="existe déjà"):
        await vault.resolve_create_category(migrated_pool, "transport", "Autre")
    with pytest.raises(vault.VaultError, match="identifiant"):
        await vault.resolve_create_category(migrated_pool, "Bad Slug", "x")
    await vault.resolve_rename_category(migrated_pool, cid, "  Livreurs ")
    await vault.resolve_add_secret(migrated_pool, CIPHER, cid, "colissimo", VALUE)
    with pytest.raises(vault.VaultError, match="1 secret"):
        await vault.resolve_delete_category(migrated_pool, cid)
    async with migrated_pool.acquire() as conn:
        cats = {c.slug: c for c in await vault.db_list_categories(conn)}
        secrets = await vault.db_list_secrets(conn)
    assert cats["transport"].label == "Livreurs" and cats["transport"].secret_count == 1
    assert [(s.ref, s.category_label) for s in secrets] == [("transport/colissimo", "Livreurs")]
    await vault.resolve_delete_secret(migrated_pool, secrets[0].id)
    await vault.resolve_delete_category(migrated_pool, cid)
    with pytest.raises(vault.VaultNotFoundError):
        await vault.resolve_delete_category(migrated_pool, cid)
    with pytest.raises(vault.VaultNotFoundError):
        await vault.resolve_rename_category(migrated_pool, cid, "x")


async def test_logs_carry_ref_never_value(migrated_pool: asyncpg.Pool) -> None:
    with capture_logs() as logs:
        sid = await vault.resolve_add_secret(
            migrated_pool, CIPHER, await _ia(migrated_pool), "k", VALUE
        )
        ia = await _ia(migrated_pool)
        await vault.resolve_update_secret(migrated_pool, CIPHER, sid, ia, "k", "", VALUE + "2")
        await vault.resolve_secret_value(migrated_pool, CIPHER, "ia/k")
    assert logs and all(VALUE not in str(entry) for entry in logs)
    assert logs[0]["ref"] == "ia/k"
