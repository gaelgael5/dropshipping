from __future__ import annotations

import io

from PIL import Image


def make_image_bytes(
    size: tuple[int, int] = (64, 48), mode: str = "RGB", fmt: str = "PNG", color: object = "red"
) -> bytes:
    out = io.BytesIO()
    Image.new(mode, size, color).save(out, fmt)  # type: ignore[arg-type]
    return out.getvalue()
