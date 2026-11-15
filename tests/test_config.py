from __future__ import annotations

from pathlib import Path

import pytest

from atelier.config import load_settings
from atelier.errors import StartupError

SECRET_PASSWORD = "s3cr3t-Zq9"


def _set_env(monkeypatch: pytest.MonkeyPatch, **values: str) -> None:
    for key in ("DATABASE_URL", "IMAGES_ROOT", "CAPTURE_TOKEN", "HTTP_PORT"):
        monkeypatch.delenv(f"ATELIER_{key}", raising=False)
    for key, value in values.items():
        monkeypatch.setenv(f"ATELIER_{key}", value)


def test_load_settings_all_present_reads_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set_env(
        monkeypatch,
        DATABASE_URL=f"postgresql://u:{SECRET_PASSWORD}@db.example:6543/atelier",
        IMAGES_ROOT=str(tmp_path),
        CAPTURE_TOKEN="tok-123",
    )
    settings = load_settings()
    assert settings.images_root == tmp_path
    assert settings.capture_token.get_secret_value() == "tok-123"


def test_load_settings_secret_repr_is_masked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set_env(
        monkeypatch,
        DATABASE_URL=f"postgresql://u:{SECRET_PASSWORD}@h/atelier",
        IMAGES_ROOT=str(tmp_path),
        CAPTURE_TOKEN="tok-123",
    )
    settings = load_settings()
    assert SECRET_PASSWORD not in repr(settings)
    assert "tok-123" not in repr(settings)


def test_load_settings_missing_variables_names_each_one(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch, IMAGES_ROOT="/data")
    with pytest.raises(StartupError) as exc:
        load_settings()
    assert "ATELIER_DATABASE_URL" in str(exc.value)
    assert "ATELIER_CAPTURE_TOKEN" in str(exc.value)


def test_load_settings_invalid_value_does_not_echo_secrets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set_env(
        monkeypatch,
        DATABASE_URL=f"postgresql://u:{SECRET_PASSWORD}@h/atelier",
        IMAGES_ROOT=str(tmp_path),
        CAPTURE_TOKEN=SECRET_PASSWORD,
        HTTP_PORT="pas-un-nombre",
    )
    with pytest.raises(StartupError) as exc:
        load_settings()
    assert "ATELIER_HTTP_PORT" in str(exc.value)
    assert SECRET_PASSWORD not in str(exc.value)
    assert "pas-un-nombre" not in str(exc.value)
