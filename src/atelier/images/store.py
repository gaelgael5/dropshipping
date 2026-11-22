"""Stockage des fichiers image sur le volume monté.

Seul module qui connaît le volume : enregistrer, ouvrir, donner l'URL de service.
Si le stockage change un jour (stockage objet...), c'est le seul fichier touché.
"""

from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path, PurePosixPath

MEDIA_URL_PREFIX = "/media/"


class UnsafePathError(ValueError):
    """Chemin relatif qui sortirait de la racine du volume."""


class ImageStore:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def file_save(self, directory: str, data: bytes, suffix: str) -> str:
        """Écrit les octets sous `directory` et rend le chemin RELATIF enregistré en base."""
        # Nom aléatoire : deux dépôts, même du même fichier, ne s'écrasent jamais.
        relative = str(PurePosixPath(directory) / f"{uuid.uuid4().hex}{suffix}")
        target = self.resolve(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Écriture dans un temporaire du même répertoire puis renommage atomique :
        # un dépôt interrompu laisse au pire un « .part » orphelin, jamais un
        # fichier tronqué sous le nom qui sera référencé en base.
        fd, tmp_name = tempfile.mkstemp(dir=target.parent, suffix=".part")
        try:
            with os.fdopen(fd, "wb") as tmp:
                tmp.write(data)
                tmp.flush()
                os.fsync(tmp.fileno())
            os.replace(tmp_name, target)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
        return relative

    def file_delete(self, relative: str) -> None:
        self.resolve(relative).unlink(missing_ok=True)

    def file_exists(self, relative: str) -> bool:
        return self.resolve(relative).is_file()

    def resolve(self, relative: str) -> Path:
        """Ramène un chemin relatif sous la racine, ou lève UnsafePathError.

        Appliqué à TOUT chemin, y compris ceux lus en base : une valeur venue de la
        requête (ou d'une ligne altérée) ne doit jamais désigner un fichier hors du
        volume. La vérification porte sur le chemin résolu, liens symboliques compris.
        """
        if not relative or PurePosixPath(relative).is_absolute() or "\\" in relative:
            raise UnsafePathError(relative)
        candidate = (self._root / relative).resolve()
        if not candidate.is_relative_to(self._root) or candidate == self._root:
            raise UnsafePathError(relative)
        return candidate

    @staticmethod
    def url_for(relative: str) -> str:
        return MEDIA_URL_PREFIX + relative
