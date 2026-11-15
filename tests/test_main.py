from __future__ import annotations

import socket
from pathlib import Path

import pytest

from atelier.__main__ import main


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _env(monkeypatch: pytest.MonkeyPatch, **values: str) -> None:
    for key in ("DATABASE_URL", "IMAGES_ROOT", "CAPTURE_TOKEN"):
        monkeypatch.delenv(f"ATELIER_{key}", raising=False)
    for key, value in values.items():
        monkeypatch.setenv(f"ATELIER_{key}", value)


def test_main_unreachable_database_exits_1_with_message_naming_host(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    port = _free_port()
    _env(
        monkeypatch,
        DATABASE_URL=f"postgresql://u:Pw-77x@127.0.0.1:{port}/atelier",
        IMAGES_ROOT=str(tmp_path),
        CAPTURE_TOKEN="tok",
    )
    assert main() == 1
    assert f"127.0.0.1:{port}/atelier" in caplog.text
    assert "Traceback" not in caplog.text
    assert "Pw-77x" not in caplog.text


def test_main_missing_images_volume_exits_1_before_touching_database(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _env(
        monkeypatch,
        DATABASE_URL="postgresql://u:p@127.0.0.1:1/atelier",
        IMAGES_ROOT=str(tmp_path / "non-monte"),
        CAPTURE_TOKEN="tok",
    )
    assert main() == 1
    assert "non-monte" in caplog.text


def test_main_missing_configuration_exits_1(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _env(monkeypatch)
    assert main() == 1
    assert "ATELIER_DATABASE_URL" in caplog.text
