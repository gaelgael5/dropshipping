from __future__ import annotations

from pathlib import Path

import pytest

from atelier.images.store import ImageStore, UnsafePathError


def test_save_writes_under_root_and_returns_relative_path(tmp_path: Path) -> None:
    store = ImageStore(tmp_path)
    relative = store.file_save("candidates/7", b"abc", ".jpg")
    assert relative.startswith("candidates/7/") and relative.endswith(".jpg")
    assert not Path(relative).is_absolute()
    assert (tmp_path / relative).read_bytes() == b"abc"


def test_save_same_content_twice_creates_two_files(tmp_path: Path) -> None:
    store = ImageStore(tmp_path)
    first = store.file_save("candidates/7", b"same", ".jpg")
    second = store.file_save("candidates/7", b"same", ".jpg")
    assert first != second
    assert len(list((tmp_path / "candidates/7").iterdir())) == 2


def test_save_leaves_no_partial_file_on_write_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ImageStore(tmp_path)

    def boom(*_args: object) -> None:
        raise OSError("disque plein")

    monkeypatch.setattr("atelier.images.store.os.replace", boom)
    with pytest.raises(OSError, match="disque plein"):
        store.file_save("candidates/1", b"x" * 100, ".jpg")
    assert list((tmp_path / "candidates/1").iterdir()) == []


def test_relative_paths_survive_remounting_root(tmp_path: Path) -> None:
    relative = ImageStore(tmp_path / "a").file_save("refs", b"img", ".jpg")
    (tmp_path / "a").rename(tmp_path / "b")
    assert ImageStore(tmp_path / "b").file_exists(relative)


@pytest.mark.parametrize(
    "relative",
    ["../etc/passwd", "candidates/../../x", "/etc/passwd", "", ".", "a\\..\\b", "candidates/.."],
)
def test_resolve_path_escaping_root_is_refused(tmp_path: Path, relative: str) -> None:
    with pytest.raises(UnsafePathError):
        ImageStore(tmp_path).resolve(relative)


def test_resolve_symlink_pointing_outside_root_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "lien").symlink_to(tmp_path)
    with pytest.raises(UnsafePathError):
        ImageStore(root).resolve("lien/secret.txt")


def test_delete_removes_file_and_tolerates_absent(tmp_path: Path) -> None:
    store = ImageStore(tmp_path)
    relative = store.file_save("c", b"x", ".jpg")
    store.file_delete(relative)
    assert not store.file_exists(relative)
    store.file_delete(relative)


def test_url_for_prefixes_media() -> None:
    assert ImageStore.url_for("candidates/1/a.jpg") == "/media/candidates/1/a.jpg"
