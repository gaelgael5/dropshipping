from __future__ import annotations

import os
from pathlib import Path

import pytest

from atelier.errors import StartupError
from atelier.storage import file_check_images_root_writable


def test_check_images_root_writable_dir_passes_and_leaves_no_probe(tmp_path: Path) -> None:
    file_check_images_root_writable(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_check_images_root_missing_dir_raises_naming_path(tmp_path: Path) -> None:
    missing = tmp_path / "absent"
    with pytest.raises(StartupError, match="absent"):
        file_check_images_root_writable(missing)


def test_check_images_root_regular_file_raises(tmp_path: Path) -> None:
    regular = tmp_path / "fichier"
    regular.write_text("x")
    with pytest.raises(StartupError, match="pas un répertoire"):
        file_check_images_root_writable(regular)


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignore les droits POSIX")
def test_check_images_root_read_only_dir_raises(tmp_path: Path) -> None:
    read_only = tmp_path / "ro"
    read_only.mkdir()
    read_only.chmod(0o500)
    try:
        with pytest.raises(StartupError, match="écriture"):
            file_check_images_root_writable(read_only)
    finally:
        read_only.chmod(0o700)
