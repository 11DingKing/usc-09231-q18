"""Map RFB pixel formats onto the Pillow raw decoder modes that unpack them.

Only formats Pillow has a raw decoder for can be unpacked straight into an
image; anything else raises :class:`UnsupportedPixelFormat` so the client can
ask the server for a format it does understand.
"""

from __future__ import annotations

from .rfb import PixelFormat


class UnsupportedPixelFormat(ValueError):
    """No Pillow raw mode exists for this pixel format."""


# Formats a caller may ask the server for by name.  The values double as
# documentation of what raw_mode() can unpack.
PIXEL_FORMATS = {
    "rgbx8888": PixelFormat(32, 24, False, True, 255, 255, 255, 0, 8, 16),
    "xrgb8888": PixelFormat(32, 24, False, True, 255, 255, 255, 8, 16, 24),
    "bgrx8888": PixelFormat(32, 24, False, True, 255, 255, 255, 16, 8, 0),
    "xbgr8888": PixelFormat(32, 24, False, True, 255, 255, 255, 24, 16, 8),
    "rgb888": PixelFormat(24, 24, False, True, 255, 255, 255, 0, 8, 16),
    "bgr888": PixelFormat(24, 24, False, True, 255, 255, 255, 16, 8, 0),
    "rgb565": PixelFormat(16, 16, False, True, 31, 63, 31, 11, 5, 0),
    "rgb555": PixelFormat(16, 15, False, True, 31, 31, 31, 10, 5, 0),
}


def raw_mode(pixel_format: PixelFormat) -> str:
    """The Pillow raw decoder mode that unpacks ``pixel_format``.

    :raises UnsupportedPixelFormat: for colour-mapped formats and layouts
        Pillow has no raw decoder for.
    """
    if not pixel_format.truecolor:
        raise UnsupportedPixelFormat(
            f"colour-mapped pixel format {pixel_format} needs a palette, "
            "not a raw mode"
        )

    maxes = (
        pixel_format.redmax,
        pixel_format.greenmax,
        pixel_format.bluemax,
    )
    shifts = (
        pixel_format.redshift,
        pixel_format.greenshift,
        pixel_format.blueshift,
    )

    if maxes == (255, 255, 255) and pixel_format.bpp in (24, 32):
        return _byte_order_mode(pixel_format, shifts)

    if pixel_format.bpp == 16 and not pixel_format.bigendian:
        if maxes == (31, 63, 31) and shifts == (11, 5, 0):
            return "BGR;16"
        if maxes == (31, 31, 31) and shifts == (10, 5, 0):
            return "BGR;15"

    raise UnsupportedPixelFormat(f"no raw mode for pixel format {pixel_format}")


def _byte_order_mode(pixel_format: PixelFormat, shifts: tuple[int, int, int]) -> str:
    """Name the channel order of an 8-bit-per-channel format, e.g. ``RGBX``."""
    if any(shift % 8 for shift in shifts):
        raise UnsupportedPixelFormat(
            f"8-bit channels not byte-aligned in {pixel_format}"
        )
    nbytes = pixel_format.bypp
    # Pillow names channels in memory order.  Little-endian puts the lowest
    # shift in the first byte; big-endian reverses it.
    positions = [shift // 8 for shift in shifts]
    if pixel_format.bigendian:
        positions = [nbytes - 1 - position for position in positions]
    if len(set(positions)) != len(shifts) or any(p >= nbytes for p in positions):
        raise UnsupportedPixelFormat(f"overlapping channels in {pixel_format}")
    mode = ["X"] * nbytes
    for position, channel in zip(positions, "RGB"):
        mode[position] = channel
    return "".join(mode)
