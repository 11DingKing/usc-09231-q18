"""Cursor handling, kept apart from the framebuffer decoding path.

The server-sent pointer shape is independent information: compositing it into
the persistent framebuffer bakes a copy of the cursor into every later
capture (cursor ghosting) and makes the captured screen disagree with what
the server actually shows.  The client therefore caches the shape here and
only composites it, onto a throwaway canvas, when a capture is explicitly
asked to include the cursor.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image


class CursorMode(enum.Enum):
    """How the client treats the pointer, set on the factory."""

    #: The server paints the pointer into the framebuffer; do not ask for it.
    SERVER = "server"
    #: Ask for the shape and composite it into captures, client-side.
    LOCAL = "local"
    #: Ask for the shape so the server stops painting it, then discard it.
    OMIT = "omit"


@dataclass(frozen=True)
class CursorInfo:
    """The server-sent pointer as standalone information.

    Nothing here references the framebuffer, so a caller can inspect or
    composite the cursor without touching the decoded screen.
    """

    image: "Image.Image"
    mask: "Image.Image"
    #: The hotspot: where in ``image`` the pointer logically points.
    hotspot: tuple[int, int]
    #: Where the pointer is, in framebuffer coordinates.
    position: tuple[int, int]

    def draw(
        self, canvas: "Image.Image", origin: tuple[int, int] = (0, 0)
    ) -> "Image.Image":
        """Composite onto ``canvas``, honouring the transparency mask.

        :param origin: canvas coordinates of the framebuffer's (0, 0), for
            compositing onto a cropped region; parts of the cursor that fall
            outside the canvas are clipped away.
        """
        x = self.position[0] - self.hotspot[0] - origin[0]
        y = self.position[1] - self.hotspot[1] - origin[1]
        canvas.paste(self.image, (x, y), self.mask)
        return canvas
