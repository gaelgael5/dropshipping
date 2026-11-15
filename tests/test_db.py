from __future__ import annotations

import socket

import asyncpg
import pytest

from atelier.db import db_open_pool, db_ping, describe_db_target
from atelier.errors import StartupError


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_describe_db_target_strips_credentials() -> None:
    target = describe_db_target("postgresql://atelier:Pw-77x@db.internal:6543/atelier")
    assert target == "db.internal:6543/atelier"


def test_describe_db_target_without_port_uses_default() -> None:
    assert describe_db_target("postgresql://u@db/base") == "db:5432/base"


async def test_open_pool_unreachable_host_raises_naming_host_without_password() -> None:
    port = _free_port()
    dsn = f"postgresql://u:Pw-77x@127.0.0.1:{port}/atelier"
    with pytest.raises(StartupError) as exc:
        await db_open_pool(dsn, timeout_s=2.0)
    message = str(exc.value)
    assert f"127.0.0.1:{port}/atelier" in message
    assert "Pw-77x" not in message


async def test_open_pool_reachable_database_returns_working_pool(database_dsn: str) -> None:
    pool = await db_open_pool(database_dsn, timeout_s=5.0)
    try:
        assert await pool.fetchval("SELECT 41 + 1") == 42
    finally:
        await pool.close()


async def test_ping_reachable_database_returns_true(pool: asyncpg.Pool) -> None:
    assert await db_ping(pool) is True


async def test_ping_closed_pool_returns_false(database_dsn: str) -> None:
    closed = await asyncpg.create_pool(database_dsn, min_size=1, max_size=1)
    await closed.close()
    assert await db_ping(closed) is False
