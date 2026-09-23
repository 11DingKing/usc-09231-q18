"""
RFB protocol implementattion, client side.

Override :class:`RFBClient` and :class:`RFBFactory` in your application.
See vncviewer.py for an example.

Reference:
https://www.rfc-editor.org/rfc/rfc6143
https://github.com/rfbproto/rfbproto/blob/master/rfbproto.rst
"""
# (C) 2003 cliechti@gmx.net
#
# MIT License

from __future__ import annotations

import getpass
from dataclasses import astuple, dataclass
from struct import Struct, pack, unpack, unpack_from
from typing import Any, Callable, ClassVar, Collection, List, Tuple, cast

from twisted.internet import protocol
from twisted.internet.protocol import Protocol
from twisted.python import log
from twisted.python.failure import Failure

from . import decoders
from .const import AuthTypes, Encoding, MsgC2S, MsgS2C
from .keys import Key

Rect = Tuple[int, int, int, int]
Ver = Tuple[int, int]


@dataclass(frozen=True)
class PixelFormat:
    """:rfc:`6143` §7.4. Pixel Format Data Structure."""

    bpp: int = 32  # u8: bits-per-pixel
    depth: int = 24  # u8
    bigendian: bool = False  # u8
    truecolor: bool = True  # u8
    redmax: int = 255  # u16
    greenmax: int = 255  # u16
    bluemax: int = 255  # u16
    redshift: int = 0  # u8
    greenshift: int = 8  # u8
    blueshift: int = 16  # u8

    STRUCT: ClassVar = Struct("!BB??HHHBBBxxx")
    VALIDATE: ClassVar = False

    def __post_init__(self) -> None:
        if not self.VALIDATE:
            return
        assert self.bpp in {8, 16, 24, 32}, f"bpp={self.bpp}"
        assert 1 <= self.depth <= self.bpp, f"depth={self.depth} <= bpp={self.bpp}"
        if self.truecolor:
            for max, shift in zip(
                (self.redmax, self.greenmax, self.bluemax),
                (self.redshift, self.greenshift, self.blueshift),
            ):
                assert 1 <= max <= 0xFFFF, f"1 <= max={max} <= 0xffff"
                assert max & (max + 1) == 0, f"max={max} not a 2**n-1"
                assert (
                    0 <= shift <= self.bpp - max.bit_length()
                ), f"shift={shift} not in bpp={self.bpp}"

    @property
    def bypp(self) -> int:  # bytes-per-pixel
        return (7 + self.bpp) // 8

    @classmethod
    def from_bytes(cls, block: bytes) -> "PixelFormat":
        return cls(*cls.STRUCT.unpack(block))

    def to_bytes(self) -> bytes:
        return cast(bytes, self.STRUCT.pack(*astuple(self)))


class RFBClient(Protocol):  # type: ignore[misc]
    # https://www.rfc-editor.org/rfc/rfc6143#section-7.1.1
    SUPPORTED_SERVER_VERSIONS = {
        (3, 3),
        # (3, 5),
        (3, 7),
        (3, 8),
        (3, 889),  # Apple Remote Desktop
        (4, 0),  # Intel AMT KVM
        (4, 1),  # RealVNC 4.6
        (5, 0),  # RealVNC 5.3
    }
    MAX_CLIENT_VERSION = (3, 8)
    SUPPORTED_AUTHS = {
        AuthTypes.NONE,
        AuthTypes.VNC_AUTHENTICATION,
        AuthTypes.DIFFIE_HELLMAN,
    }

    _HEADER = b"RFB 000.000\n"
    _HEADER_TRANSLATE = bytes.maketrans(b"0123456789", b"0" * 10)

    #: Negotiated desktop size; zeroed until the server init arrives.
    width = 0
    height = 0

    def __init__(self) -> None:
        self._packet = bytearray()
        self._handler = self._handleInitial
        self._expected_len = 12
        self._expected_args: tuple[Any, ...] = ()
        self._expected_kwargs: dict[str, Any] = {}
        self._already_expecting = False
        self._version: Ver = (0, 0)
        self._version_server: Ver = (0, 0)
        self._decoder_cache: dict[Encoding, decoders.Decoder | None] = {}
        self.negotiated_encodings = {
            Encoding.RAW,
        }
        self.pixel_format = PixelFormat()

    @property
    def bypp(self) -> int:
        return self.pixel_format.bypp

    # ------------------------------------------------------
    # states used on connection startup
    # ------------------------------------------------------

    def _handleInitial(self) -> None:
        head = self._packet[:12]
        norm = head.translate(self._HEADER_TRANSLATE)
        if norm == self._HEADER:
            version_server = (int(head[4:7]), int(head[8:11]))
            if version_server not in self.SUPPORTED_SERVER_VERSIONS:
                log.msg("Protocol version %d.%d not supported" % version_server)

            version = max(
                v for v in self.SUPPORTED_SERVER_VERSIONS if v <= version_server
            )
            if version > self.MAX_CLIENT_VERSION:
                version = self.MAX_CLIENT_VERSION

            del self._packet[0:12]
            log.msg("Using protocol version %d.%d" % version)
            self.transport.write(b"RFB %03d.%03d\n" % version)
            self._handler = self._handleExpected
            self._version = version
            self._version_server = version_server
            if version < (3, 7):
                self.expect(self._handleAuth, 4)
            else:
                self.expect(self._handleNumberSecurityTypes, 1)
        elif not self._HEADER.startswith(norm):
            self.vncProtocolError(f"invalid initial server response {head!r}")
            self.transport.loseConnection()

    def _handleNumberSecurityTypes(self, block: bytes) -> None:
        (num_types,) = unpack("!B", block)
        if num_types:
            self.expect(self._handleSecurityTypes, num_types)
        else:
            self.expect(self._handleConnFailed, 4)

    def _handleSecurityTypes(self, block: bytes) -> None:
        types = unpack(f"!{len(block)}B", block)
        for sec_type in types:
            log.msg(f"Offered {AuthTypes.lookup(sec_type)!r}")
        valid_types = set(types) & self.SUPPORTED_AUTHS
        if valid_types:
            sec_type = max(valid_types)
            self.transport.write(pack("!B", sec_type))
            if sec_type == AuthTypes.NONE:
                if self._version < (3, 8):
                    self._doClientInitialization()
                else:
                    self.expect(self._handleVNCAuthResult, 4)
            elif sec_type == AuthTypes.VNC_AUTHENTICATION:
                self.expect(self._handleVNCAuth, 16)
            elif sec_type == AuthTypes.DIFFIE_HELLMAN:
                self.expect(self._handleDHAuth, 4)
        else:
            self.vncProtocolError(f"unknown security types: {types!r}")
            self.transport.loseConnection()

    def _handleAuth(self, block: bytes) -> None:
        (auth,) = unpack("!I", block)
        if auth == AuthTypes.INVALID:
            self.expect(self._handleConnFailed, 4)
        elif auth == AuthTypes.NONE:
            self._doClientInitialization()
        elif auth == AuthTypes.VNC_AUTHENTICATION:
            self.expect(self._handleVNCAuth, 16)
        else:
            self.vncProtocolError(f"unknown auth response {AuthTypes.lookup(auth)!r}")
            self.transport.loseConnection()

    def _handleConnFailed(self, block: bytes) -> None:
        (waitfor,) = unpack("!I", block)
        self.expect(self._handleConnMessage, waitfor)

    def _handleConnMessage(self, block: bytes) -> None:
        self.vncProtocolError(f"Connection refused: {block!r}")
        self.transport.loseConnection()

    def _handleVNCAuth(self, block: bytes) -> None:
        self._challenge = block
        self.vncRequestPassword()
        self.expect(self._handleVNCAuthResult, 4)

    def _handleDHAuth(self, block: bytes) -> None:
        self.generator, self.keyLen = unpack("!HH", block)
        self.expect(self._handleDHAuthKey, self.keyLen)

    def _handleDHAuthKey(self, block: bytes) -> None:
        self.modulus = block
        self.expect(self._handleDHAuthCert, self.keyLen)

    def _handleDHAuthCert(self, block: bytes) -> None:
        self.serverKey = block

        self.ardRequestCredentials()

        self._encryptArd()
        self.expect(self._handleVNCAuthResult, 4)

    def _encryptArd(self) -> None:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import dh

        userStruct = f"{self.factory.username:\0<64}{self.factory.password:\0<64}"

        p = int.from_bytes(self.modulus, "big")
        sk = int.from_bytes(self.serverKey, "big")
        param_nums = dh.DHParameterNumbers(p=p, g=self.generator)
        server_key = dh.DHPublicNumbers(sk, param_nums).public_key()

        params = param_nums.parameters()
        private_key = params.generate_private_key()
        shared_key = private_key.exchange(server_key)

        h = hashes.Hash(hashes.MD5())
        h.update(shared_key)
        key_digest = h.finalize()

        ciphertext = des_encrypt_aes(key_digest, userStruct.encode("utf-8"))

        public_key = private_key.public_key()
        y = public_key.public_numbers().y.to_bytes(self.keyLen, "big")

        self.transport.write(ciphertext + y)

    def ardRequestCredentials(self) -> None:
        if self.factory.username is None:
            self.factory.username = input("username: ")
        if self.factory.password is None:
            self.factory.password = getpass.getpass("password:")

    def sendPassword(self, password: str) -> None:
        """send password"""
        self.transport.write(des_encrypt(_vnc_des(password), self._challenge))

    def _handleVNCAuthResult(self, block: bytes) -> None:
        (result,) = unpack("!I", block)
        if result == 0:  # OK
            self._doClientInitialization()
            return
        elif result == 1:  # failed
            if self._version < (3, 8):
                self.vncAuthFailed("authentication failed")
                self.transport.loseConnection()
            else:
                self.expect(self._handleAuthFailed, 4)
        elif result == 2:  # too many
            if self._version < (3, 8):
                self.vncAuthFailed("too many tries to log in")
                self.transport.loseConnection()
            else:
                self.expect(self._handleAuthFailed, 4)
        else:
            log.msg(f"unknown auth response ({result})")
            self.transport.loseConnection()

    def _handleAuthFailed(self, block: bytes) -> None:
        (waitfor,) = unpack("!I", block)
        self.expect(self._handleAuthFailedMessage, waitfor)

    def _handleAuthFailedMessage(self, block: bytes) -> None:
        self.vncAuthFailed(block)
        self.transport.loseConnection()

    def _doClientInitialization(self) -> None:
        self.transport.write(pack("!B", self.factory.shared))
        self.expect(self._handleServerInit, 24)

    def _handleServerInit(self, block: bytes) -> None:
        (self.width, self.height, pixformat, namelen) = unpack("!HH16sI", block)
        self.pixel_format = PixelFormat.from_bytes(pixformat)
        log.msg(f"Native {self.pixel_format} bytes={self.pixel_format.bypp}")
        self.expect(self._handleServerName, namelen)

    def _handleServerName(self, block: bytes) -> None:
        self.name = block
        # callback:
        self.vncConnectionMade()
        self.expect(self._handleConnection, 1)

    # ------------------------------------------------------
    # Server to client messages
    # ------------------------------------------------------
    def _handleConnection(self, block: bytes) -> None:
        (msgid,) = unpack("!B", block)
        if msgid == MsgS2C.FRAMEBUFFER_UPDATE:
            self.expect(self._handleFramebufferUpdate, 3)
        elif msgid == MsgS2C.SET_COLOUR_MAP_ENTRIES:
            self.expect(self._handleColourMapEntries, 5)
        elif msgid == MsgS2C.BELL:
            self.bell()
            self.expect(self._handleConnection, 1)
        elif msgid == MsgS2C.SERVER_CUT_TEXT:
            self.expect(self._handleServerCutText, 7)
        else:
            self.vncProtocolError(f"unknown message received {MsgS2C.lookup(msgid)!r}")
            self.transport.loseConnection()

    def _handleFramebufferUpdate(self, block: bytes) -> None:
        (self.rectangles,) = unpack("!xH", block)
        self.rectanglePos: list[Rect] = []
        self.beginUpdate()
        self._doConnection()

    def _doConnection(self) -> None:
        if self.rectangles:
            self.expect(self._handleRectangle, 12)
        else:
            self.commitUpdate(self.rectanglePos)
            self.expect(self._handleConnection, 1)

    def _handleRectangle(self, block: bytes) -> None:
        (x, y, width, height, encoding) = unpack("!HHHHi", block)
        decoder = decoders.decoder_for(self, encoding)
        if decoder is None:
            self.vncProtocolError(
                f"unknown encoding received {Encoding.lookup(encoding)!r}"
            )
            self.transport.loseConnection()
            return
        log.msg(f"x={x} y={y} w={width} h={height} {Encoding.lookup(encoding)!r}")
        if encoding == Encoding.PSEUDO_LAST_RECT:
            self.rectangles = 0

        if self.rectangles:
            self.rectangles -= 1
            if decoder.carries_image:
                self.rectanglePos.append((x, y, width, height))
            decoder.decode(x, y, width, height)
        else:
            self._doConnection()

    # ---  other server messages

    def _handleColourMapEntries(self, block: bytes) -> None:
        (first_color, number_of_colors) = unpack("!xHH", block)
        self.expect(
            self._handleColourMapEntriesValue, 6 * number_of_colors, first_color
        )

    def _handleColourMapEntriesValue(self, block: bytes, first_color: int) -> None:
        colors = [
            unpack_from("!HHH", block, offset) for offset in range(0, len(block), 6)
        ]
        self.set_color_map(first_color, cast(List[Tuple[int, int, int]], colors))
        self.expect(self._handleConnection, 1)

    def _handleServerCutText(self, block: bytes) -> None:
        (length,) = unpack("!xxxI", block)
        self.expect(self._handleServerCutTextValue, length)

    def _handleServerCutTextValue(self, block: bytes) -> None:
        self.copy_text(block.decode("iso-8859-1"))
        self.expect(self._handleConnection, 1)

    # ------------------------------------------------------
    # incomming data redirector
    # ------------------------------------------------------
    def dataReceived(self, data: bytes) -> None:
        self._packet.extend(data)
        self._handler()

    def _handleExpected(self) -> None:
        if len(self._packet) >= self._expected_len:
            while len(self._packet) >= self._expected_len:
                self._already_expecting = True
                block = bytes(self._packet[: self._expected_len])
                del self._packet[: self._expected_len]
                self._expected_handler(
                    block, *self._expected_args, **self._expected_kwargs
                )
            self._already_expecting = False

    def expect(
        self, handler: Callable[..., None], size: int, *args: Any, **kwargs: Any
    ) -> None:
        self._expected_handler = handler
        self._expected_len = size
        self._expected_args = args
        self._expected_kwargs = kwargs
        if not self._already_expecting:
            self._handleExpected()  # just in case that there is already enough data

    # ------------------------------------------------------
    # client -> server messages
    # ------------------------------------------------------

    def setPixelFormat(self, pixel_format: PixelFormat) -> None:
        pixformat = pixel_format.to_bytes()
        self.transport.write(pack("!Bxxx16s", MsgC2S.SET_PIXEL_FORMAT, pixformat))
        self.pixel_format = pixel_format

    def setEncodings(self, list_of_encodings: Collection[Encoding]) -> None:
        self.transport.write(pack("!BxH", MsgC2S.SET_ENCODING, len(list_of_encodings)))
        for encoding in list_of_encodings:
            log.msg(f"Offering {encoding!r}")
            self.transport.write(pack("!i", encoding))

    def framebufferUpdateRequest(
        self,
        x: int = 0,
        y: int = 0,
        width: int | None = None,
        height: int | None = None,
        incremental: bool = False,
    ) -> None:
        if width is None:
            width = self.width - x
        if height is None:
            height = self.height - y
        self.transport.write(
            pack("!BBHHHH", MsgC2S.FRAMEBUFFER_UPDATE_REQUEST, incremental, x, y, width, height)
        )

    def keyEvent(self, key: Key | int, down: bool = True) -> None:
        """For most ordinary keys, the "keysym" is the same as the corresponding ASCII value.
        Other common keys are shown in the ``Key`` constants."""
        self.transport.write(pack("!BBxxI", MsgC2S.KEY_EVENT, down, key))

    def pointerEvent(self, x: int, y: int, buttonmask: int = 0) -> None:
        """Indicates either pointer movement or a pointer button press or release. The pointer is
        now at (x-position, y-position), and the current state of buttons 1 to 8 are represented
        by bits 0 to 7 of button-mask respectively, 0 meaning up, 1 meaning down (pressed).
        """
        self.transport.write(pack("!BBHH", MsgC2S.POINTER_EVENT, buttonmask, x, y))

    def clientCutText(self, message: str) -> None:
        """The client has new ISO 8859-1 (Latin-1) text in its cut buffer.
        (aka clipboard)
        """
        data = message.encode("iso-8859-1")
        self.transport.write(pack("!BxxxI", MsgC2S.CLIENT_CUT_TEXT, len(data)) + data)

    # ------------------------------------------------------
    # callbacks
    # override these in your application
    # ------------------------------------------------------
    def vncConnectionMade(self) -> None:
        """connection is initialized and ready.
        typicaly, the pixel format is set here."""

    def vncRequestPassword(self) -> None:
        """a password is needed to log on, use :meth:`sendPassword` to
        send one."""
        if self.factory.password is None:
            log.msg("need a password")
            self.transport.loseConnection()
            return
        self.sendPassword(self.factory.password)

    def vncAuthFailed(self, reason: "Failure | bytes | str") -> None:
        """called when the authentication failed.
        the connection is closed."""
        log.msg(f"Cannot connect {reason}")

    def vncProtocolError(self, reason: str) -> None:
        """called when the server sends something we cannot handle.
        the connection is closed."""
        log.msg(reason)

    def beginUpdate(self) -> None:
        """called before a series of :meth:`updateRectangle`,
        :meth:`copyRectangle` or :meth:`fillRectangle`."""

    def commitUpdate(self, rectangles: list[Rect] | None = None) -> None:
        """called after a series of :meth:`updateRectangle`, :meth:`copyRectangle`
        or :meth:`fillRectangle` are finished.

        Typicaly, here is the place to request the next screen
        update with :meth:`framebufferUpdateRequest` with ``incremental=True``.

        :param rectangles: a list of tuples (x,y,w,h) with the updated rectangles.
        """

    def updateRectangle(
        self,
        x: int,
        y: int,
        width: int,
        height: int,
        data: bytes,
        pixel_format: PixelFormat,
    ) -> None:
        """new bitmap data.

        :param data: bytes in the pixel format set up earlier.
        :param pixel_format: the format ``data`` is in, so the client can
            unpack it without guessing which format was negotiated when it
            arrived.
        """

    def copyRectangle(
        self, srcx: int, srcy: int, x: int, y: int, width: int, height: int
    ) -> None:
        """used for copyrect encoding. copy the given rectangle
        (src, srxy, width, height) to the target coords (x,y)"""

    def fillRectangle(
        self, x: int, y: int, width: int, height: int, color: bytes
    ) -> None:
        """fill the area with the color.

        :param color: bytes in the pixel format set up earlier.
        """
        # fallback variant, use update recatngle
        # override with specialized function for better performance
        self.updateRectangle(
            x, y, width, height, color * (width * height), self.pixel_format
        )

    def updateCursor(
        self, x: int, y: int, width: int, height: int, image: bytes, mask: bytes
    ) -> None:
        """New cursor, focuses at (x, y)"""

    def updatePointerPos(self, x: int, y: int) -> None:
        """The server moved the pointer to (x, y)."""

    def updateDesktopSize(self, width: int, height: int) -> None:
        """New desktop size of width*height."""

    def set_color_map(self, first: int, colors: list[tuple[int, int, int]]) -> None:
        """The server is using a new color map."""

    def bell(self) -> None:
        """bell"""

    def copy_text(self, text: str) -> None:
        """The server has new ISO 8859-1 (Latin-1) text in its cut buffer.
        (aka clipboard)"""


class RFBFactory(protocol.ClientFactory):  # type: ignore[misc]
    """A factory for remote frame buffer connections."""

    # the class of the protocol to build
    # should be overriden by application to use a derrived class
    protocol = RFBClient

    def __init__(self, password: str | None = None, shared: bool = False) -> None:
        self.password = password
        self.shared = shared


def des_encrypt(key: bytes, data: bytes) -> bytes:
    """Encrypt with single DES, as the VNC family's password handling uses.

    Single DES in ECB is weak, and is what RFB specifies: both the
    authentication challenge response (RFC 6143 section 7.2.2) and the
    password file format are defined in terms of it, so a stronger
    algorithm here would simply fail to talk to any VNC server."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    # Triple-DES with the same 56-bit key repeated three times is
    # equivalent to single-DES
    encryptor = Cipher(algorithms.TripleDES(key * 3), modes.ECB()).encryptor()
    return encryptor.update(data) + encryptor.finalize()


def des_encrypt_aes(key: bytes, data: bytes) -> bytes:
    """Encrypt the Apple Remote Desktop credentials block."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return encryptor.update(data) + encryptor.finalize()


def reverse_bits(data: bytes) -> bytes:
    """The bit-reversal the VNC family applies to a DES key before using it,
    both for the authentication challenge response here and for the
    password obfuscation in ~/.vnc/passwd files."""
    return bytes(
        sum((128 >> i) if (k & (1 << i)) else 0 for i in range(8)) for k in data
    )


def _vnc_des(password: str) -> bytes:
    """Custom DES variant for RFB protocol.

    RFB protocol for authentication requires client to encrypt
    challenge sent by server with password using DES method. However,
    bits in each byte of the password are put in reverse order before
    using it as encryption key."""
    pw = f"{password:\0<8.8}"  # make sure its 8 chars long, zero padded
    key = pw.encode(
        "ASCII"
    )  # unspecified https://www.rfc-editor.org/rfc/rfc6143#section-7.2.2
    return reverse_bits(key)
