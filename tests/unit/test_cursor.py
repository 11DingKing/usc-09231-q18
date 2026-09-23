"""The cursor is captured apart from the framebuffer.

The framebuffer holds exactly what the server painted; the server-sent
cursor shape is cached as standalone information and only composited, onto a
throwaway canvas, when a capture is asked to include it.  These tests pin
that separation: image comparisons for what captures and the framebuffer
look like, and lifecycle tests for when the cursor cache is invalidated.
"""
from __future__ import annotations

import io
import os
import tempfile
import unittest

from PIL import Image

from vncdotool import imagematch
from vncdotool.const import Encoding
from vncdotool.cursor import CursorMode

from tests.unit.utils import (
    _pixel,
    framebuffer_update,
    handshake,
    make_client,
    rect,
)

BACKGROUND = (40, 80, 120)
IMAGE_2X2 = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]
# Left column opaque, right column transparent.
MASK_LEFT_COLUMN = bytes([0b10000000, 0b10000000])
MASK_OPAQUE = bytes([0b11000000, 0b11000000])
MASK_TRANSPARENT = bytes([0b00000000, 0b00000000])

POINTER_AT = rect(30, 20, 0, 0, Encoding.PSEUDO_POINTER_POS, b"")


def cursor_rect(hotspot_x=0, hotspot_y=0, pixels=IMAGE_2X2, mask=MASK_OPAQUE):
    body = b"".join(_pixel(*p) for p in pixels) + mask
    return rect(hotspot_x, hotspot_y, 2, 2, Encoding.PSEUDO_CURSOR, body)


def hide_cursor_rect():
    return rect(0, 0, 0, 0, Encoding.PSEUDO_CURSOR, b"")


def full_screen(width=64, height=48, pixel=BACKGROUND):
    return rect(0, 0, width, height, Encoding.RAW, _pixel(*pixel) * (width * height))


def pointer_at(x, y):
    return rect(x, y, 0, 0, Encoding.PSEUDO_POINTER_POS, b"")


class CursorTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.cli = make_client()
        handshake(self.cli, 64, 48)
        self.cli.factory.cursor = CursorMode.LOCAL

    def receive(self, *rects: bytes) -> None:
        self.cli.dataReceived(framebuffer_update(list(rects)))

    def capture(self, fp=None, **kwargs):
        """captureScreen, answering the refresh with a full-screen frame."""
        fp = fp or io.BytesIO()
        kwargs.setdefault("format", "png")
        d = self.cli.captureScreen(fp, **kwargs)
        fired: list = []
        d.addCallback(fired.append)
        self.receive(full_screen())
        self.assertEqual(fired, [self.cli], "capture did not complete")
        fp.seek(0)
        return Image.open(fp).convert("RGB")

    def capture_region(self, x, y, w, h):
        """captureRegion, answering the refresh with a full-screen frame."""
        fd, path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        self.addCleanup(os.unlink, path)
        d = self.cli.captureRegion(path, x, y, w, h)
        fired: list = []
        d.addCallback(fired.append)
        self.receive(full_screen())
        self.assertEqual(fired, [self.cli], "capture did not complete")
        with Image.open(path) as image:
            return image.convert("RGB")


class TestFramebufferSeparation(CursorTestCase):
    def test_the_framebuffer_holds_exactly_what_the_server_painted(self) -> None:
        self.receive(full_screen(), cursor_rect(), POINTER_AT)

        expected = Image.new("RGB", (64, 48), BACKGROUND)
        self.assertTrue(
            imagematch.matches(self.cli.screen, expected, 0),
            "cursor pixels leaked into the framebuffer",
        )

    def test_a_pointer_move_leaves_no_ghost_behind(self) -> None:
        """The old compositing baked the cursor into the screen at every
        position it passed through; a capture must show it only where it is.
        """
        self.receive(full_screen(), cursor_rect(), pointer_at(10, 10))
        self.receive(pointer_at(30, 20))

        capture = self.capture()

        self.assertEqual(capture.getpixel((30, 20)), IMAGE_2X2[0])
        self.assertEqual(
            capture.getpixel((10, 10)),
            BACKGROUND,
            "cursor ghost left at the previous position",
        )
        for x, y in ((10, 10), (30, 20)):
            self.assertEqual(self.cli.screen.getpixel((x, y)), BACKGROUND)


class TestCaptureCompositing(CursorTestCase):
    def test_the_capture_shows_the_cursor_where_the_server_put_it(self) -> None:
        self.receive(full_screen(), cursor_rect(), POINTER_AT)

        capture = self.capture()

        self.assertEqual(capture.getpixel((30, 20)), IMAGE_2X2[0])
        self.assertEqual(capture.getpixel((31, 20)), IMAGE_2X2[1])
        self.assertEqual(capture.getpixel((30, 21)), IMAGE_2X2[2])
        self.assertEqual(capture.getpixel((31, 21)), IMAGE_2X2[3])
        self.assertEqual(capture.getpixel((0, 0)), BACKGROUND)

    def test_the_hotspot_offsets_where_the_shape_lands(self) -> None:
        self.receive(full_screen(), cursor_rect(1, 1), POINTER_AT)

        capture = self.capture()

        self.assertEqual(capture.getpixel((29, 19)), IMAGE_2X2[0])
        self.assertEqual(capture.getpixel((30, 20)), IMAGE_2X2[3])

    def test_transparent_pixels_let_the_background_through(self) -> None:
        self.receive(full_screen(), cursor_rect(mask=MASK_LEFT_COLUMN), POINTER_AT)

        capture = self.capture()

        self.assertEqual(capture.getpixel((30, 20)), IMAGE_2X2[0])
        self.assertEqual(capture.getpixel((31, 20)), BACKGROUND)
        self.assertEqual(capture.getpixel((30, 21)), IMAGE_2X2[2])
        self.assertEqual(capture.getpixel((31, 21)), BACKGROUND)

    def test_a_fully_transparent_cursor_captures_as_if_absent(self) -> None:
        self.receive(full_screen(), cursor_rect(mask=MASK_TRANSPARENT), POINTER_AT)

        capture = self.capture()

        expected = Image.new("RGB", (64, 48), BACKGROUND)
        self.assertTrue(imagematch.matches(capture, expected, 0))

    def test_a_hidden_cursor_captures_as_if_absent(self) -> None:
        self.receive(full_screen(), cursor_rect(), POINTER_AT)
        self.receive(hide_cursor_rect())

        capture = self.capture()

        expected = Image.new("RGB", (64, 48), BACKGROUND)
        self.assertTrue(imagematch.matches(capture, expected, 0))

    def test_repeated_captures_composite_fresh_each_time(self) -> None:
        """Continuous grabs must not accumulate the cursor into the screen."""
        self.receive(full_screen(), cursor_rect(), POINTER_AT)

        first = self.capture()
        second = self.capture()

        self.assertTrue(imagematch.matches(first, second, 0))
        self.assertEqual(second.getpixel((30, 20)), IMAGE_2X2[0])
        expected_screen = Image.new("RGB", (64, 48), BACKGROUND)
        self.assertTrue(imagematch.matches(self.cli.screen, expected_screen, 0))

    def test_a_region_capture_offsets_the_cursor(self) -> None:
        self.receive(full_screen(), cursor_rect(), POINTER_AT)

        capture = self.capture_region(20, 10, 20, 20)

        # The cursor sits at (30, 20) on screen: (10, 10) within the region.
        self.assertEqual(capture.getpixel((10, 10)), IMAGE_2X2[0])
        self.assertEqual(capture.getpixel((0, 0)), BACKGROUND)

    def test_a_region_away_from_the_cursor_stays_clean(self) -> None:
        self.receive(full_screen(), cursor_rect(), POINTER_AT)

        capture = self.capture_region(40, 30, 10, 10)

        expected = Image.new("RGB", (10, 10), BACKGROUND)
        self.assertTrue(imagematch.matches(capture, expected, 0))


class TestCursorCacheLifecycle(CursorTestCase):
    def test_no_cursor_is_known_before_the_server_sends_one(self) -> None:
        self.assertIsNone(self.cli.cursorInfo())

    def test_the_shape_is_cached_as_standalone_information(self) -> None:
        self.receive(cursor_rect(1, 1), POINTER_AT)

        info = self.cli.cursorInfo()

        self.assertIsNotNone(info)
        self.assertEqual(info.image.size, (2, 2))
        self.assertEqual(info.hotspot, (1, 1))
        self.assertEqual(info.position, (30, 20))

    def test_a_new_shape_replaces_the_cached_one(self) -> None:
        other = [(1, 2, 3)] * 4
        self.receive(cursor_rect())
        self.receive(cursor_rect(pixels=other))

        info = self.cli.cursorInfo()

        self.assertEqual(info.image.getpixel((0, 0)), (1, 2, 3))

    def test_hiding_the_cursor_invalidates_the_cache(self) -> None:
        self.receive(cursor_rect())
        self.assertIsNotNone(self.cli.cursorInfo())

        self.receive(hide_cursor_rect())

        self.assertIsNone(self.cli.cursorInfo())
        self.assertIsNone(self.cli.cursor)
        self.assertIsNone(self.cli.cmask)

    def test_omit_mode_never_caches_the_shape(self) -> None:
        self.cli.factory.cursor = CursorMode.OMIT

        self.receive(cursor_rect(), POINTER_AT)

        self.assertIsNone(self.cli.cursorInfo())
        capture = self.capture()
        self.assertEqual(capture.getpixel((30, 20)), BACKGROUND)

    def test_a_script_move_supersedes_the_server_position(self) -> None:
        self.receive(cursor_rect(), POINTER_AT)
        self.assertEqual(self.cli.cursorInfo().position, (30, 20))

        self.cli.mouseMove(5, 6)

        self.assertIsNone(self.cli.cursor_pos)
        self.assertEqual(self.cli.cursorInfo().position, (5, 6))

    def test_the_server_position_is_tracked_until_superseded(self) -> None:
        self.receive(cursor_rect())
        self.receive(pointer_at(7, 8))

        self.assertEqual(self.cli.cursorInfo().position, (7, 8))


if __name__ == "__main__":
    unittest.main()
