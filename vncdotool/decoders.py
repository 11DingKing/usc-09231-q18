"""Framebuffer decoders and the encodings the client offers by default."""

from __future__ import annotations

from .const import Encoding

# Offered best-first: a server picks from what the client lists, so the
# cheapest to send that still decodes losslessly comes first.
DEFAULT_ENCODINGS = [
    Encoding.TIGHT,
    Encoding.HEXTILE,
    Encoding.RAW,
]
