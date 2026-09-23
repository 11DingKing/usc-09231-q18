"""Decoders for the rectangle encodings of a FramebufferUpdate.

Each decoder consumes the payload of one rectangle and reports it back
through the client's callbacks.  Rectangles that carry cursor information
rather than framebuffer pixels are decoded here too, but on their own
path: they go to the client's cursor store and never touch the
framebuffer decoding path.
"""

from __future__ import annotations

from struct import unpack
from typing import TYPE_CHECKING

from .const import Encoding, HextileEncoding

if TYPE_CHECKING:
    from .rfb import RFBClient

#: Encodings offered to the server when the caller asks for nothing
#: specific, most preferred first.
DEFAULT_ENCODINGS = [Encoding.TIGHT, Encoding.HEXTILE, Encoding.RAW]


class Decoder:
    """One rectangle decoder, bound to its client connection.

    Multi-stage encodings pull their payload through the client's
    :meth:`~rfb.RFBClient.expect`, so a decoder never has to block on
    bytes that have not arrived yet.
    """

    encoding: Encoding
    #: Whether a rectangle of this kind carries image data the client
    #: stores.  Pseudo-encodings that only carry metadata (a new desktop
    #: size, the pointer position, ...) must not count as a screen change.
    carries_image = True

    def __init__(self, client: "RFBClient") -> None:
        self.client = client

    def decode(self, x: int, y: int, width: int, height: int) -> None:
        raise NotImplementedError


class RawDecoder(Decoder):
    encoding = Encoding.RAW

    def decode(self, x: int, y: int, width: int, height: int) -> None:
        self.client.expect(
            self._payload, width * height * self.client.bypp, x, y, width, height
        )

    def _payload(self, block: bytes, x: int, y: int, width: int, height: int) -> None:
        client = self.client
        client.updateRectangle(x, y, width, height, block, client.pixel_format)
        client._doConnection()


class CopyRectangleDecoder(Decoder):
    encoding = Encoding.COPY_RECTANGLE

    def decode(self, x: int, y: int, width: int, height: int) -> None:
        self.client.expect(self._payload, 4, x, y, width, height)

    def _payload(self, block: bytes, x: int, y: int, width: int, height: int) -> None:
        (srcx, srcy) = unpack("!HH", block)
        client = self.client
        client.copyRectangle(srcx, srcy, x, y, width, height)
        client._doConnection()


class RreDecoder(Decoder):
    encoding = Encoding.RRE

    def decode(self, x: int, y: int, width: int, height: int) -> None:
        self.client.expect(self._header, 4 + self.client.bypp, x, y, width, height)

    def _header(self, block: bytes, x: int, y: int, width: int, height: int) -> None:
        client = self.client
        (subrects,) = unpack("!I", block[:4])
        client.fillRectangle(x, y, width, height, block[4:])
        if subrects:
            client.expect(self._subrects, (8 + client.bypp) * subrects, x, y)
        else:
            client._doConnection()

    def _subrects(self, block: bytes, topx: int, topy: int) -> None:
        client = self.client
        bypp = client.bypp
        size = bypp + 8
        for pos in range(0, len(block), size):
            (color, x, y, width, height) = unpack(
                f"!{bypp}sHHHH", block[pos : pos + size]
            )
            client.fillRectangle(topx + x, topy + y, width, height, color)
        client._doConnection()


class CorreDecoder(Decoder):
    encoding = Encoding.CORRE

    def decode(self, x: int, y: int, width: int, height: int) -> None:
        self.client.expect(self._header, 4 + self.client.bypp, x, y, width, height)

    def _header(self, block: bytes, x: int, y: int, width: int, height: int) -> None:
        client = self.client
        (subrects,) = unpack("!I", block[:4])
        client.fillRectangle(x, y, width, height, block[4:])
        if subrects:
            client.expect(self._subrects, (4 + client.bypp) * subrects, x, y)
        else:
            client._doConnection()

    def _subrects(self, block: bytes, topx: int, topy: int) -> None:
        client = self.client
        bypp = client.bypp
        size = bypp + 4
        for pos in range(0, len(block), size):
            (color, x, y, width, height) = unpack(
                f"!{bypp}sBBBB", block[pos : pos + size]
            )
            client.fillRectangle(topx + x, topy + y, width, height, color)
        client._doConnection()


class HextileDecoder(Decoder):
    encoding = Encoding.HEXTILE

    def decode(self, x: int, y: int, width: int, height: int) -> None:
        self._next_tile(None, None, x, y, width, height, None, None)

    def _next_tile(
        self,
        bg: bytes | None,
        color: bytes | None,
        x: int,
        y: int,
        width: int,
        height: int,
        tx: int | None,
        ty: int | None,
    ) -> None:
        # coords of next tile: tiles run line by line, and the rectangle is
        # finished when the last line is completely received.
        if tx is not None:
            assert ty is not None
            tx += 16
            if tx >= x + width:
                tx = x
                ty += 16
        else:
            tx = x
            ty = y
        if ty >= y + height:
            self.client._doConnection()
        else:
            self.client.expect(
                self._tile, 1, bg, color, x, y, width, height, tx, ty
            )

    def _tile(
        self,
        block: bytes,
        bg: bytes | None,
        color: bytes | None,
        x: int,
        y: int,
        width: int,
        height: int,
        tx: int,
        ty: int,
    ) -> None:
        client = self.client
        subencoding = HextileEncoding(block[0])
        tw = min(16, x + width - tx)
        th = min(16, y + height - ty)
        if subencoding & HextileEncoding.RAW:
            client.expect(
                self._raw,
                tw * th * client.bypp,
                bg,
                color,
                x,
                y,
                width,
                height,
                tx,
                ty,
                tw,
                th,
            )
            return

        numbytes = 0
        if subencoding & HextileEncoding.BACKGROUND_SPECIFIED:
            numbytes += client.bypp
        if subencoding & HextileEncoding.FOREGROUND_SPECIFIED:
            numbytes += client.bypp
        if subencoding & HextileEncoding.ANY_SUBRECTS:
            numbytes += 1
        if numbytes:
            client.expect(
                self._subrect,
                numbytes,
                subencoding,
                bg,
                color,
                x,
                y,
                width,
                height,
                tx,
                ty,
                tw,
                th,
            )
        else:
            client.fillRectangle(tx, ty, tw, th, bg)
            self._next_tile(bg, color, x, y, width, height, tx, ty)

    def _subrect(
        self,
        block: bytes,
        subencoding: HextileEncoding,
        bg: bytes | None,
        color: bytes | None,
        x: int,
        y: int,
        width: int,
        height: int,
        tx: int,
        ty: int,
        tw: int,
        th: int,
    ) -> None:
        client = self.client
        subrects = 0
        pos = 0
        if subencoding & HextileEncoding.BACKGROUND_SPECIFIED:
            bg = block[: client.bypp]
            pos += client.bypp
        client.fillRectangle(tx, ty, tw, th, bg)
        if subencoding & HextileEncoding.FOREGROUND_SPECIFIED:
            color = block[pos : pos + client.bypp]
            pos += client.bypp
        if subencoding & HextileEncoding.ANY_SUBRECTS:
            subrects = block[pos]
        if subrects:
            if subencoding & HextileEncoding.SUBRECTS_COLORED:
                client.expect(
                    self._subrects_coloured,
                    (client.bypp + 2) * subrects,
                    bg,
                    color,
                    x,
                    y,
                    width,
                    height,
                    tx,
                    ty,
                    tw,
                    th,
                )
            else:
                client.expect(
                    self._subrects_fg,
                    2 * subrects,
                    bg,
                    color,
                    x,
                    y,
                    width,
                    height,
                    tx,
                    ty,
                    tw,
                    th,
                )
        else:
            self._next_tile(bg, color, x, y, width, height, tx, ty)

    def _raw(
        self,
        block: bytes,
        bg: bytes | None,
        color: bytes | None,
        x: int,
        y: int,
        width: int,
        height: int,
        tx: int,
        ty: int,
        tw: int,
        th: int,
    ) -> None:
        """the tile is in raw encoding"""
        client = self.client
        client.updateRectangle(tx, ty, tw, th, block, client.pixel_format)
        self._next_tile(bg, color, x, y, width, height, tx, ty)

    def _subrects_coloured(
        self,
        block: bytes,
        bg: bytes | None,
        color: bytes | None,
        x: int,
        y: int,
        width: int,
        height: int,
        tx: int,
        ty: int,
        tw: int,
        th: int,
    ) -> None:
        """subrects with their own color"""
        client = self.client
        size = client.bypp + 2
        for pos in range(0, len(block), size):
            color = block[pos : pos + client.bypp]
            xy, wh = block[pos + client.bypp : pos + size]
            client.fillRectangle(
                tx + (xy >> 4),
                ty + (xy & 0xF),
                (wh >> 4) + 1,
                (wh & 0xF) + 1,
                color,
            )
        self._next_tile(bg, color, x, y, width, height, tx, ty)

    def _subrects_fg(
        self,
        block: bytes,
        bg: bytes | None,
        color: bytes | None,
        x: int,
        y: int,
        width: int,
        height: int,
        tx: int,
        ty: int,
        tw: int,
        th: int,
    ) -> None:
        """all subrects with the same color"""
        client = self.client
        for pos in range(0, len(block), 2):
            xy, wh = block[pos : pos + 2]
            client.fillRectangle(
                tx + (xy >> 4),
                ty + (xy & 0xF),
                (wh >> 4) + 1,
                (wh & 0xF) + 1,
                color,
            )
        self._next_tile(bg, color, x, y, width, height, tx, ty)


class TightDecoder(Decoder):
    """Tight is offered for its ubiquity, but its rectangles are not
    decoded yet; receiving one fails the connection explicitly rather
    than desynchronising the stream."""

    encoding = Encoding.TIGHT

    def decode(self, x: int, y: int, width: int, height: int) -> None:
        client = self.client
        client.vncProtocolError("tight rectangles are not supported by this client")
        client.transport.loseConnection()


class CursorDecoder(Decoder):
    """The Cursor pseudo-encoding: the pointer's shape and hotspot, in the
    rectangle's payload and x/y.  Nothing here touches the framebuffer;
    the shape goes to the client's cursor store instead."""

    encoding = Encoding.PSEUDO_CURSOR

    def decode(self, x: int, y: int, width: int, height: int) -> None:
        client = self.client
        length = width * height * client.bypp
        length += ((width + 7) // 8) * height
        client.expect(self._payload, length, x, y, width, height)

    def _payload(self, block: bytes, x: int, y: int, width: int, height: int) -> None:
        client = self.client
        split = width * height * client.bypp
        client.updateCursor(x, y, width, height, block[:split], block[split:])
        client._doConnection()


class PointerPosDecoder(Decoder):
    """The PointerPos pseudo-encoding: where the server moved the pointer.
    Pure metadata — it carries no payload and is not a screen change."""

    encoding = Encoding.PSEUDO_POINTER_POS
    carries_image = False

    def decode(self, x: int, y: int, width: int, height: int) -> None:
        client = self.client
        client.updatePointerPos(x, y)
        client._doConnection()


class DesktopSizeDecoder(Decoder):
    """The DesktopSize pseudo-encoding: a new desktop size, no pixels."""

    encoding = Encoding.PSEUDO_DESKTOP_SIZE
    carries_image = False

    def decode(self, x: int, y: int, width: int, height: int) -> None:
        client = self.client
        client.updateDesktopSize(width, height)
        client._doConnection()


class LastRectDecoder(Decoder):
    """Marks the last rectangle of an update.  RFBClient ends the update
    when it sees one, so :meth:`decode` is never actually reached."""

    encoding = Encoding.PSEUDO_LAST_RECT
    carries_image = False

    def decode(self, x: int, y: int, width: int, height: int) -> None:
        self.client._doConnection()


class QemuExtendedKeyEventDecoder(Decoder):
    """The QEMU extended key event pseudo-encoding: an acknowledgement,
    not a screen change."""

    encoding = Encoding.PSEUDO_QEMU_EXTENDED_KEY_EVENT
    carries_image = False

    def decode(self, x: int, y: int, width: int, height: int) -> None:
        client = self.client
        client.negotiated_encodings.add(self.encoding)
        client._doConnection()


DECODERS: dict[Encoding, type[Decoder]] = {
    cls.encoding: cls
    for cls in (
        RawDecoder,
        CopyRectangleDecoder,
        RreDecoder,
        CorreDecoder,
        HextileDecoder,
        TightDecoder,
        CursorDecoder,
        PointerPosDecoder,
        DesktopSizeDecoder,
        LastRectDecoder,
        QemuExtendedKeyEventDecoder,
    )
}


def decoder_for(client: "RFBClient", encoding: int) -> Decoder | None:
    """The decoder for ``encoding`` on ``client``, or None when the
    encoding is unknown.  Decoders are cached on the client so one
    instance serves every rectangle of its kind on that connection.
    """
    try:
        known = Encoding(encoding)
    except ValueError:
        return None
    if known not in client._decoder_cache:
        cls = DECODERS.get(known)
        client._decoder_cache[known] = cls(client) if cls is not None else None
    return client._decoder_cache[known]
