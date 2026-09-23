"""Shared fixtures for driving a client through the RFB wire protocol."""
from __future__ import annotations

from struct import pack
from unittest import mock

from vncdotool import client, rfb
from vncdotool.const import AuthTypes, Encoding, MsgS2C
from vncdotool.cursor import CursorMode

# The default PixelFormat() maps to image_mode="RGBX", so every fixture
# below is written in that wire byte order: R, G, B, pad.
PIXEL_FORMAT = rfb.PixelFormat()
BYPP = PIXEL_FORMAT.bypp
if BYPP != 4:  # every fixture below hard-codes 4-byte pixels
    raise RuntimeError(f"default PixelFormat is no longer 32bpp (bypp={BYPP}); rewrite the fixtures")


def _pixel(r: int, g: int, b: int) -> bytes:
    """One RGBX pixel on the wire, in the client's negotiated format."""
    return bytes((r, g, b, 0))


def make_client() -> client.VNCDoToolClient:
    cli = client.VNCDoToolClient()
    cli.transport = mock.Mock()
    cli.factory = mock.Mock()
    cli.factory.shared = 0
    cli.factory.password = None
    # A bare Mock's attributes are all truthy, so the pseudo-encoding flags
    # need pinning to real values.
    cli.factory.cursor = CursorMode.OMIT
    cli.factory.pseudodesktop = False
    cli.factory.last_rect = False
    cli.factory.qemu_extended_key = False
    cli.factory.fence = False
    cli.setEncodings = mock.Mock()  # not under test here
    return cli


def handshake(cli: client.VNCDoToolClient, width: int, height: int) -> None:
    """Drive an RFB 3.3 / AuthTypes.NONE handshake + ServerInit through dataReceived."""
    cli.dataReceived(b"RFB 003.003\n")
    cli.dataReceived(pack("!I", AuthTypes.NONE))
    server_init = pack("!HH16sI", width, height, PIXEL_FORMAT.to_bytes(), 0)
    cli.dataReceived(server_init)


def rect(x: int, y: int, w: int, h: int, encoding: Encoding, body: bytes = b"") -> bytes:
    """One FramebufferUpdate rectangle: 12-byte header + encoding-specific body."""
    return pack("!HHHHi", x, y, w, h, int(encoding)) + body


def framebuffer_update(rects: list[bytes]) -> bytes:
    """A full FramebufferUpdate server message, msgid included."""
    header = pack("!BxH", MsgS2C.FRAMEBUFFER_UPDATE, len(rects))
    return header + b"".join(rects)
