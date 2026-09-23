"""WebSocket transport selection for :func:`client.factory_connect`.

Only the address-family plumbing lives here; the actual WebSocket
transport is not part of this build, so dialling a ``ws://`` URL fails
loudly instead of silently speaking TCP to a WebSocket server.
"""

from __future__ import annotations

import socket
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from twisted.internet.interfaces import IReactorCore

    from .client import VNCDoToolFactory

#: The ``family`` values :func:`client.factory_connect` accepts.
AddressFamily = socket.AddressFamily

#: Sentinel family selecting a WebSocket connection; ``host`` then carries
#: the whole ``ws://`` URL, path and query included.
WEBSOCKET = "websocket"


def connect(reactor: "IReactorCore", factory: "VNCDoToolFactory", url: str) -> None:
    raise NotImplementedError("WebSocket transport is not available in this build")
