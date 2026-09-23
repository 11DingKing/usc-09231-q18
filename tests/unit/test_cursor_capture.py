"""The cursor is captured apart from the framebuffer.

The framebuffer holds exactly what the server sent; the cursor shape is
tracked next to it and only composited onto a frame when one is captured.
Compositing used to happen inside the framebuffer decoding path, which
burnt the pointer into every capture and left ghosts behind when it
moved.
"""

from __future__ import annotations

import io
import struct
import unittest

from PIL import Image

from vncdotool.const import Encoding
from vncdotool.cursor import CursorMode

from tests.unit.utils import (
    _pixel,
    framebuffer_update,
    handshake,
    make_client,
    rect,
)

BACKGROUND = (10, 20, 30)
IMAGE_2X2 = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]
MASK_2X2 = bytes([0b11000000, 0b11000000])
TRANSPARENT_MASK_2X2 = bytes([0x00, 0x00])


def cursor_rect(hotspot_x: int = 0, hotspot_y: int = 0, mask: bytes = MASK_2X2) -> bytes:
    body = b"".join(_pixel(*p) for p in IMAGE_2X2) + mask
    return rect(hotspot_x, hotspot_y, 2, 2, Encoding.PSEUDO_CURSOR, body)


def hidden_cursor_rect() -> bytes:
    # RFC 6143 7.6.1: width or height 0 means hide the pointer.
    return rect(0, 0, 0, 0, Encoding.PSEUDO_CURSOR, b"")


def pointer_pos(x: int, y: int) -> bytes:
    return rect(x, y, 0, 0, Encoding.PSEUDO_POINTER_POS, b"")


def raw(x: int, y: int, w: int, h: int, color: tuple[int, int, int] = BACKGROUND) -> bytes:
    return rect(x, y, w, h, Encoding.RAW, _pixel(*color) * (w * h))


def copyrect(srcx: int, srcy: int, x: int, y: int, w: int, h: int) -> bytes:
    return rect(x, y, w, h, Encoding.COPY_RECTANGLE, struct.pack("!HH", srcx, srcy))


def images_equal(a: Image.Image, b: Image.Image) -> bool:
    return a.size == b.size and a.mode == b.mode and a.tobytes() == b.tobytes()


class TestFramebufferStaysBare(unittest.TestCase):
    """Cursor traffic must not leak into the framebuffer decoding path."""

    def setUp(self) -> None:
        self.cli = make_client()
        handshake(self.cli, 100, 100)
        self.cli.factory.cursor = CursorMode.LOCAL
        self.cli.dataReceived(framebuffer_update([raw(0, 0, 100, 100)]))

    def test_a_cursor_update_does_not_touch_the_framebuffer(self) -> None:
        self.cli.mouseMove(10, 20)
        self.cli.dataReceived(framebuffer_update([cursor_rect()]))

        self.assertEqual(self.cli.screen.getpixel((10, 20)), BACKGROUND)

    def test_a_pointer_move_does_not_touch_the_framebuffer(self) -> None:
        self.cli.dataReceived(framebuffer_update([cursor_rect()]))
        self.cli.dataReceived(framebuffer_update([pointer_pos(50, 60)]))

        self.assertEqual(self.cli.screen.getpixel((50, 60)), BACKGROUND)

    def test_a_framebuffer_update_does_not_composite_the_cursor(self) -> None:
        self.cli.dataReceived(framebuffer_update([cursor_rect()]))
        self.cli.mouseMove(10, 20)

        self.cli.dataReceived(framebuffer_update([raw(30, 30, 10, 10)]))

        self.assertEqual(self.cli.screen.getpixel((10, 20)), BACKGROUND)

    def test_the_cursor_is_still_available_separately(self) -> None:
        """Asking for the cursor keeps returning it as its own data."""
        self.cli.dataReceived(framebuffer_update([cursor_rect(3, 4)]))
        self.cli.dataReceived(framebuffer_update([pointer_pos(50, 60)]))

        assert self.cli.cursor is not None
        self.assertEqual(self.cli.cursor.size, (2, 2))
        self.assertEqual(self.cli.cursor.getpixel((0, 0)), IMAGE_2X2[0])
        self.assertIsNotNone(self.cli.cmask)
        self.assertEqual(self.cli.cfocus, (3, 4))
        self.assertEqual(self.cli.cursor_pos, (50, 60))


class TestCaptureCompositesTheCursor(unittest.TestCase):
    def setUp(self) -> None:
        self.cli = make_client()
        handshake(self.cli, 100, 100)
        self.cli.factory.cursor = CursorMode.LOCAL
        self.cli.dataReceived(framebuffer_update([raw(0, 0, 100, 100)]))

    def capture(self) -> Image.Image:
        buffer = io.BytesIO()
        self.cli._captureSave(None, buffer, format="png")
        buffer.seek(0)
        image = Image.open(buffer)
        image.load()
        return image

    def test_the_cursor_is_composited_at_the_script_position(self) -> None:
        self.cli.mouseMove(10, 20)
        self.cli.dataReceived(framebuffer_update([cursor_rect(1, 1)]))

        captured = self.capture()

        self.assertEqual(captured.getpixel((9, 19)), IMAGE_2X2[0])
        self.assertEqual(captured.getpixel((10, 20)), IMAGE_2X2[3])
        self.assertEqual(captured.getpixel((8, 19)), BACKGROUND)

    def test_the_cursor_is_composited_at_the_server_position(self) -> None:
        self.cli.mouseMove(10, 20)
        self.cli.dataReceived(framebuffer_update([cursor_rect()]))
        self.cli.dataReceived(framebuffer_update([pointer_pos(50, 60)]))

        captured = self.capture()

        self.assertEqual(captured.getpixel((50, 60)), IMAGE_2X2[0])
        self.assertEqual(captured.getpixel((10, 20)), BACKGROUND)

    def test_a_transparent_cursor_captures_the_bare_framebuffer(self) -> None:
        self.cli.mouseMove(10, 20)
        self.cli.dataReceived(
            framebuffer_update([cursor_rect(mask=TRANSPARENT_MASK_2X2)])
        )

        self.assertTrue(images_equal(self.capture(), self.cli.screen))

    def test_a_hidden_cursor_captures_the_bare_framebuffer(self) -> None:
        self.cli.dataReceived(framebuffer_update([cursor_rect()]))
        self.cli.dataReceived(framebuffer_update([hidden_cursor_rect()]))

        self.assertTrue(images_equal(self.capture(), self.cli.screen))

    def test_omit_mode_captures_the_bare_framebuffer(self) -> None:
        self.cli.factory.cursor = CursorMode.OMIT
        self.cli.dataReceived(framebuffer_update([cursor_rect()]))

        self.assertTrue(images_equal(self.capture(), self.cli.screen))

    def test_consecutive_captures_are_identical(self) -> None:
        self.cli.mouseMove(10, 20)
        self.cli.dataReceived(framebuffer_update([cursor_rect()]))

        first = self.capture()
        second = self.capture()

        self.assertTrue(images_equal(first, second))

    def test_moving_the_cursor_leaves_no_ghost(self) -> None:
        """The defect: the old cursor position kept the composited pixels."""
        self.cli.mouseMove(10, 20)
        self.cli.dataReceived(framebuffer_update([cursor_rect()]))
        self.capture()

        self.cli.mouseMove(40, 50)
        captured = self.capture()

        self.assertEqual(captured.getpixel((10, 20)), BACKGROUND)
        self.assertEqual(captured.getpixel((40, 50)), IMAGE_2X2[0])

    def test_the_server_moving_the_cursor_leaves_no_ghost(self) -> None:
        self.cli.dataReceived(framebuffer_update([cursor_rect()]))
        self.cli.dataReceived(framebuffer_update([pointer_pos(10, 20)]))
        self.capture()

        self.cli.dataReceived(framebuffer_update([pointer_pos(40, 50)]))
        captured = self.capture()

        self.assertEqual(captured.getpixel((10, 20)), BACKGROUND)
        self.assertEqual(captured.getpixel((40, 50)), IMAGE_2X2[0])


class TestFrameCacheLifecycle(unittest.TestCase):
    """frame() caches the composited frame and drops the cache when the
    framebuffer, the cursor shape or the cursor position changes."""

    def setUp(self) -> None:
        self.cli = make_client()
        handshake(self.cli, 100, 100)
        self.cli.factory.cursor = CursorMode.LOCAL
        self.cli.dataReceived(framebuffer_update([raw(0, 0, 100, 100)]))
        self.cli.dataReceived(framebuffer_update([cursor_rect()]))

    def test_the_bare_screen_is_not_copied_without_a_cursor(self) -> None:
        cli = make_client()
        handshake(cli, 100, 100)
        cli.dataReceived(framebuffer_update([raw(0, 0, 100, 100)]))

        self.assertIs(cli.frame(), cli.screen)

    def test_the_composite_is_cached_between_calls(self) -> None:
        first = self.cli.frame()
        second = self.cli.frame()

        self.assertIsNot(first, self.cli.screen)
        self.assertIs(first, second)

    def test_a_framebuffer_update_invalidates_the_cache(self) -> None:
        cached = self.cli.frame()

        self.cli.dataReceived(framebuffer_update([raw(4, 4, 10, 10, (200, 100, 50))]))

        frame = self.cli.frame()
        self.assertIsNot(frame, cached)
        self.assertEqual(frame.getpixel((5, 5)), (200, 100, 50))

    def test_a_copy_rectangle_invalidates_the_cache(self) -> None:
        cached = self.cli.frame()

        self.cli.dataReceived(framebuffer_update([copyrect(0, 0, 50, 50, 10, 10)]))

        self.assertIsNot(self.cli.frame(), cached)

    def test_a_new_cursor_shape_invalidates_the_cache(self) -> None:
        cached = self.cli.frame()

        self.cli.dataReceived(framebuffer_update([cursor_rect(1, 1)]))

        self.assertIsNot(self.cli.frame(), cached)

    def test_a_pointer_move_invalidates_the_cache(self) -> None:
        cached = self.cli.frame()

        self.cli.dataReceived(framebuffer_update([pointer_pos(30, 40)]))

        frame = self.cli.frame()
        self.assertIsNot(frame, cached)
        self.assertEqual(frame.getpixel((30, 40)), IMAGE_2X2[0])

    def test_a_script_move_invalidates_the_cache(self) -> None:
        cached = self.cli.frame()

        self.cli.mouseMove(30, 40)

        self.assertIsNot(self.cli.frame(), cached)

    def test_a_desktop_resize_invalidates_the_cache(self) -> None:
        cached = self.cli.frame()

        self.cli.dataReceived(
            framebuffer_update(
                [rect(0, 0, 200, 200, Encoding.PSEUDO_DESKTOP_SIZE, b"")]
            )
        )

        self.assertIsNot(self.cli.frame(), cached)

    def test_hiding_the_cursor_drops_the_composite(self) -> None:
        self.cli.frame()

        self.cli.dataReceived(framebuffer_update([hidden_cursor_rect()]))

        self.assertIs(self.cli.frame(), self.cli.screen)


if __name__ == "__main__":
    unittest.main()
