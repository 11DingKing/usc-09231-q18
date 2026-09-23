"""WebSocket transport selection for :func:`vncdotool.client.factory_connect`.

Only the address-family plumbing lives in this snapshot; dialing a WebSocket
URL requires a WebSocket client transport, which is not part of it.
"""

from __future__ import annotations

from typing import Union

#: Address families accepted by ``factory_connect``: the ``socket.AF_*``
#: values, or :data:`WEBSOCKET` when the host is a WebSocket URL.
AddressFamily = Union[int, str]

#: Selects a WebSocket connection; the host argument then carries the URL.
WEBSOCKET = "websocket"


def connect(reactor, factory, url: str):  # pragma: no cover
    """Connect ``factory`` to a VNC server over WebSocket."""
    raise NotImplementedError(
        "WebSocket transport is not included in this snapshot; "
        "connect over TCP or a UNIX socket instead"
    )
