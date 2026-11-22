"""Normalisation des images à l'entrée : un format, une taille maximale."""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

# JPEG : accepté partout, y compris par les plateformes de vente où les photos
# finiront ; le WebP, plus compact, n'y est pas toujours admis.
OUTPUT_FORMAT = "JPEG"
OUTPUT_SUFFIX = ".jpg"
JPEG_QUALITY = 90
# 2048 px sur le grand côté : assez pour la génération et les plateformes, sans
# stocker des visuels de 8000 px venus de certains sites.
MAX_SIDE_PX = 2048
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
# Garde contre les « bombes de décompression » : un petit fichier qui se déploie
# en une image gigantesque en mémoire.
MAX_PIXELS = 60_000_000


class InvalidImageError(ValueError):
    """Le fichier n'est pas une image exploitable."""


@dataclass(frozen=True)
class NormalizedImage:
    data: bytes
    width: int
    height: int


def normalize_image(raw: bytes) -> NormalizedImage:
    if not raw:
        raise InvalidImageError("Fichier vide : ce n'est pas une image.")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise InvalidImageError("Fichier trop volumineux (20 Mo maximum).")
    image = _open_verified(raw)
    # Orientation EXIF appliquée AVANT de jeter les métadonnées : sinon une photo
    # de téléphone prise en portrait ressortirait couchée.
    image = ImageOps.exif_transpose(image)
    image = _flatten_to_rgb(image)
    image.thumbnail((MAX_SIDE_PX, MAX_SIDE_PX), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    # Aucune métadonnée réécrite : l'EXIF d'origine peut porter une localisation.
    image.save(out, OUTPUT_FORMAT, quality=JPEG_QUALITY, optimize=True)
    return NormalizedImage(out.getvalue(), image.width, image.height)


def _open_verified(raw: bytes) -> Image.Image:
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        # verify() détecte une image tronquée ou corrompue mais rend l'objet
        # inutilisable : on rouvre ensuite pour décoder réellement.
        with Image.open(io.BytesIO(raw)) as probe:
            probe.verify()
        image = Image.open(io.BytesIO(raw))
        image.load()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise InvalidImageError("Ce fichier n'est pas une image, ou elle est corrompue.") from exc
    except Image.DecompressionBombError as exc:
        raise InvalidImageError("Image trop grande en pixels.") from exc
    return image


def _flatten_to_rgb(image: Image.Image) -> Image.Image:
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        # Fond blanc : le JPEG n'a pas de transparence, et un fond noir par défaut
        # rendrait inutilisables les visuels détourés des sites d'achat.
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    return image.convert("RGB")
