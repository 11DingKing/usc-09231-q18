"""How the client handles the remote pointer's cursor.

The Cursor pseudo-encoding is offered in every mode but
:attr:`CursorMode.SERVER`: a server that paints the pointer into the
framebuffer stops doing so only once a client asks for the cursor shape,
and a stale pointer burnt into the framebuffer is worse than none at all.
"""

from __future__ import annotations

from enum import Enum, auto


class CursorMode(Enum):
    """Where the cursor the user sees comes from."""

    OMIT = auto()
    """Track nothing; captures show the bare framebuffer."""

    LOCAL = auto()
    """Track the cursor shape the server sends and composite it onto
    captured frames, leaving the framebuffer itself untouched."""

    SERVER = auto()
    """The server paints the pointer into the framebuffer, so the cursor
    pseudo-encoding is not offered at all."""
