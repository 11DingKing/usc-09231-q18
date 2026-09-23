"""Map RFB pixel formats to the Pillow raw modes that unpack them.

Only true-colour formats whose channel layout Pillow can express are
supported; anything else raises :exc:`UnsupportedPixelFormat` so the
client can ask the server for a different format instead of decoding
garbage.
"""

from __future__ import annotations

from .rfb import PixelFormat


class UnsupportedPixelFormat(Exception):
    """The pixel format cannot be unpacked into an image."""


#: Named formats a caller can ask the server for, by short mnemonic.
#: The names read as channel order and width, ``x`` being padding.
PIXEL_FORMATS: dict[str, PixelFormat] = {
    "rgbx8888": PixelFormat(32, 24, False, True, 255, 255, 255, 0, 8, 16),
    "bgrx8888": PixelFormat(32, 24, False, True, 255, 255, 255, 16, 8, 0),
    "xrgb8888": PixelFormat(32, 24, False, True, 255, 255, 255, 8, 16, 24),
    "xbgr8888": PixelFormat(32, 24, False, True, 255, 255, 255, 24, 16, 8),
    "rgb888": PixelFormat(24, 24, False, True, 255, 255, 255, 0, 8, 16),
    "bgr888": PixelFormat(24, 24, False, True, 255, 255, 255, 16, 8, 0),
    "rgb565": PixelFormat(16, 16, False, True, 31, 63, 31, 11, 5, 0),
    "bgr565": PixelFormat(16, 16, False, True, 31, 63, 31, 0, 5, 11),
}

_RAW_MODES = {
    PIXEL_FORMATS["rgbx8888"]: "RGBX",
    PIXEL_FORMATS["bgrx8888"]: "BGRX",
    PIXEL_FORMATS["xrgb8888"]: "XRGB",
    PIXEL_FORMATS["xbgr8888"]: "XBGR",
    PIXEL_FORMATS["rgb888"]: "RGB",
    PIXEL_FORMATS["bgr888"]: "BGR",
    PIXEL_FORMATS["rgb565"]: "BGR;16",
    PIXEL_FORMATS["bgr565"]: "RGB;16",
}


def raw_mode(pixel_format: PixelFormat) -> str:
    """The Pillow raw mode that unpacks pixels in ``pixel_format``."""
    try:
        return _RAW_MODES[pixel_format]
    except KeyError:
        raise UnsupportedPixelFormat(f"{pixel_format}") from None
