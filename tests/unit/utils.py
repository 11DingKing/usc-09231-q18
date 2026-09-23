"""Shared helpers for the unit tests: wire, well, unit tests that speak RFB."""

from __future__ import annotations

import struct
from unittest import mock

from vncdotool import client, rfb

#: The server-init pixel format used throughout the tests: 32bpp RGBX.
PIXEL_FORMAT = bytes(
    [
        0x20, 0x18, 0x00, 0x01,  # bpp, depth, bigendian, truecolor
        0x00, 0xFF, 0x00, 0xFF, 0x00, 0xFF,  # max red, green, blue
        0x00, 0x08, 0x10,  # shifts red, green, blue
        0x00, 0x00, 0x00,  # padding
    ]
)


def _pixel(red: int, green: int, blue: int) -> bytes:
    """One pixel in the test framebuffer's RGBX layout."""
    return bytes((red, green, blue, 0))


def rect(x: int, y: int, width: int, height: int, encoding: int, body: bytes) -> bytes:
    """One rectangle of a FramebufferUpdate, header and payload."""
    return struct.pack("!HHHHi", x, y, width, height, encoding) + body


def framebuffer_update(rects: list[bytes]) -> bytes:
    """A whole FramebufferUpdate message around its rectangles."""
    return struct.pack("!BxH", rfb.MsgS2C.FRAMEBUFFER_UPDATE, len(rects)) + b"".join(rects)


def handshake(cli: client.VNCDoToolClient, width: int, height: int) -> None:
    """Run the client through the handshake up to vncConnectionMade()."""
    cli._packet = bytearray(b"RFB 003.003\n")
    cli._handleInitial()
    cli._handleServerInit(
        struct.pack("!HH", width, height) + PIXEL_FORMAT + struct.pack("!I", 0)
    )


def make_client() -> client.VNCDoToolClient:
    """A client with its transport, factory and outgoing messages mocked."""
    cli = client.VNCDoToolClient()
    cli.transport = mock.Mock()
    cli.factory = mock.Mock()
    cli.framebufferUpdateRequest = mock.Mock()  # type: ignore[method-assign]
    cli.pointerEvent = mock.Mock()  # type: ignore[method-assign]
    cli.keyEvent = mock.Mock()  # type: ignore[method-assign]
    cli.setEncodings = mock.Mock()  # type: ignore[method-assign]
    return cli
