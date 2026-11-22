from __future__ import annotations

import io

import pytest
from PIL import Image

from atelier.images.normalize import MAX_SIDE_PX, InvalidImageError, normalize_image
from tests.images_helpers import make_image_bytes


def _decode(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))


def test_normalize_png_becomes_jpeg_with_same_size() -> None:
    result = normalize_image(make_image_bytes((64, 48)))
    image = _decode(result.data)
    assert image.format == "JPEG"
    assert (result.width, result.height) == (64, 48) == image.size


def test_normalize_large_image_is_resized_keeping_ratio() -> None:
    result = normalize_image(make_image_bytes((4000, 2000), fmt="JPEG"))
    assert (result.width, result.height) == (MAX_SIDE_PX, MAX_SIDE_PX // 2)


def test_normalize_transparent_png_gets_white_background() -> None:
    result = normalize_image(make_image_bytes((10, 10), mode="RGBA", color=(0, 0, 0, 0)))
    r, g, b = _decode(result.data).convert("RGB").getpixel((5, 5))  # type: ignore[misc]
    assert min(r, g, b) > 240


def test_normalize_applies_exif_orientation() -> None:
    out = io.BytesIO()
    exif = Image.Exif()
    exif[0x0112] = 6  # rotation de 90° : une photo de téléphone tenue en portrait
    Image.new("RGB", (60, 20), "blue").save(out, "JPEG", exif=exif)
    result = normalize_image(out.getvalue())
    assert (result.width, result.height) == (20, 60)


def test_normalize_drops_exif_metadata() -> None:
    out = io.BytesIO()
    exif = Image.Exif()
    exif[0x010F] = "Fabricant"
    Image.new("RGB", (8, 8)).save(out, "JPEG", exif=exif)
    assert not _decode(normalize_image(out.getvalue()).data).getexif()


@pytest.mark.parametrize(
    "raw",
    [b"", b"ceci n'est pas une image", b"%PDF-1.4 faux pdf", make_image_bytes(fmt="PNG")[:60]],
)
def test_normalize_non_image_or_corrupted_is_refused(raw: bytes) -> None:
    with pytest.raises(InvalidImageError):
        normalize_image(raw)


def test_normalize_oversized_upload_is_refused() -> None:
    with pytest.raises(InvalidImageError, match="volumineux"):
        normalize_image(b"\x00" * (20 * 1024 * 1024 + 1))
