from __future__ import annotations

import tempfile
from pathlib import Path

from atelier.errors import StartupError


def file_check_images_root_writable(root: Path) -> None:
    if not root.exists():
        raise StartupError(f"Volume d'images absent : {root} n'existe pas (volume non monté ?)")
    if not root.is_dir():
        raise StartupError(f"Volume d'images invalide : {root} n'est pas un répertoire")
    # On écrit réellement un fichier plutôt que de consulter os.access : les
    # droits POSIX ne disent rien d'un volume monté en lecture seule ou plein.
    try:
        with tempfile.NamedTemporaryFile(dir=root, prefix=".probe-"):
            pass
    except OSError as exc:
        raise StartupError(
            f"Volume d'images non accessible en écriture : {root} ({exc.strerror})"
        ) from None
