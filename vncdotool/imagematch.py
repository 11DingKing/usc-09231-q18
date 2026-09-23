"""Compare images the way someone comparing two screens would.

The comparison has two knobs: ``fuzz``, how far any one channel of any
one pixel may sit from the target on a 0-255 scale, and ``blur``, a
smoothing radius applied to both images first, which is what carries a
match through a lossy encoding.
"""

from __future__ import annotations

from PIL import Image, ImageChops, ImageFilter

from . import pixelformat
from .rfb import PixelFormat


def matches(a: Image.Image, b: Image.Image, fuzz: int, blur: int = 0) -> bool:
    """Whether two images show the same thing, give or take ``fuzz``."""
    if a.size != b.size:
        return False
    if a.mode != b.mode:
        a, b = a.convert("RGB"), b.convert("RGB")
    if blur:
        a = a.filter(ImageFilter.GaussianBlur(blur))
        b = b.filter(ImageFilter.GaussianBlur(blur))
    diff = ImageChops.difference(a, b)
    return max(high for _, high in diff.getextrema()) <= fuzz


def fuzz_for_format(pixel_format: PixelFormat) -> int:
    """The comparison slack a pixel format needs.

    A channel with ``maxval`` distinct values cannot reproduce most 8-bit
    colours, so an exact comparison against one never comes true; the
    widest step such a channel jumps in is the least slack that covers
    for it.
    """
    if not pixel_format.truecolor:
        raise pixelformat.UnsupportedPixelFormat(f"{pixel_format}")
    return max(
        255 // (maxval + 1)
        for maxval in (
            pixel_format.redmax,
            pixel_format.greenmax,
            pixel_format.bluemax,
        )
    )
