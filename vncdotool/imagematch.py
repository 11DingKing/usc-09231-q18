"""Perceptual image comparison for ``expect`` and ``stable``.

Two screens rarely need to be byte-identical to count as the same: lossy
encodings and reduced pixel formats shift pixel values by amounts a human
cannot see.  :func:`matches` compares two same-sized images pixel by pixel
using a perceived colour difference, and :func:`fuzz_for_format` works out
how much difference a negotiated pixel format cannot even express.
"""

from __future__ import annotations

import math

from PIL import Image, ImageChops, ImageFilter

from . import pixelformat
from .rfb import PixelFormat

# The perceived-difference metric peaks at sqrt(9) * 255 for black versus
# white, so dividing by 3 maps it onto the 0-255 fuzz scale.
_SCALE = 3.0


def matches(
    actual: Image.Image, expected: Image.Image, fuzz: int, blur: int = 0
) -> bool:
    """Whether two images match, pixel by pixel, within ``fuzz``.

    :param fuzz: how far any one pixel may sit from the target, as a
        perceived colour difference from 0 (exact) to 255.
    :param blur: blur both images by this radius before comparing.
    """
    if actual.size != expected.size:
        return False

    actual = actual.convert("RGB")
    expected = expected.convert("RGB")
    if blur:
        actual = actual.filter(ImageFilter.GaussianBlur(blur))
        expected = expected.filter(ImageFilter.GaussianBlur(blur))

    limit = (fuzz * _SCALE) ** 2
    diff = ImageChops.difference(actual, expected)
    # The redmean weighting needs the mean red of each pixel pair, which a
    # straight difference does not carry.
    rmean = ImageChops.add(actual.getchannel("R"), expected.getchannel("R"), scale=2)
    deltas = diff.tobytes()
    rmeans = rmean.tobytes()
    for i in range(0, len(deltas), 3):
        rm = rmeans[i // 3]
        distance = (
            (2 + rm / 256) * deltas[i] ** 2
            + 4 * deltas[i + 1] ** 2
            + (2 + (255 - rm) / 256) * deltas[i + 2] ** 2
        )
        if distance > limit:
            return False
    return True


def fuzz_for_format(pixel_format: PixelFormat) -> int:
    """The fuzz that hides what ``pixel_format`` cannot express.

    A server sending 5-bit red cannot reproduce most 8-bit values, so an
    exact comparison can never succeed; the returned fuzz is the worst
    perceived error a round trip through the format can cause.
    """
    if not pixel_format.truecolor:
        raise pixelformat.UnsupportedPixelFormat(
            f"cannot measure colour-mapped format {pixel_format}"
        )
    errors = [
        _roundtrip_error(maximum.bit_length())
        for maximum in (
            pixel_format.redmax,
            pixel_format.greenmax,
            pixel_format.bluemax,
        )
    ]
    if not any(errors):
        return 0
    # The redmean weights vary with the mean red of the pair; take the
    # worst end of the range.
    worst = max(
        (2 + rm / 256) * errors[0] ** 2
        + 4 * errors[1] ** 2
        + (2 + (255 - rm) / 256) * errors[2] ** 2
        for rm in (0, 255)
    )
    return math.ceil(math.sqrt(worst) / _SCALE)


def _roundtrip_error(bits: int) -> int:
    """The largest error from quantizing an 8-bit channel to ``bits`` and back."""
    if bits >= 8:
        return 0
    shift = 8 - bits
    worst = 0
    for value in range(256):
        quantized = value >> shift
        # Bit replication, e.g. 5-bit 0b abcde -> 0b abcdeabc, is the
        # expansion that keeps 0 and 255 exact.
        restored = (quantized << shift) | (quantized >> (bits - shift))
        worst = max(worst, value - restored)
    return worst
