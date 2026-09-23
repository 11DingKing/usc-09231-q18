"""Tests for the image comparison behind `expect` and `stable`."""

from __future__ import annotations

import unittest

from PIL import Image

from vncdotool import imagematch, pixelformat, rfb
from vncdotool.pixelformat import PIXEL_FORMATS


def swatch(*pixels: tuple[int, int, int]) -> Image.Image:
    image = Image.new("RGB", (len(pixels), 1))
    image.putdata(pixels)
    return image


class TestMatches(unittest.TestCase):
    def test_identical_images_match_exactly(self) -> None:
        image = swatch((0x2A, 0x2A, 0x2A), (0xC1, 0xC1, 0xC1))

        self.assertTrue(imagematch.matches(image, image.copy(), 0))

    def test_a_channel_within_the_fuzz_matches(self) -> None:
        target = swatch((0x2A, 0x2A, 0x2A))
        close = swatch((0x2C, 0x2A, 0x2A))

        self.assertTrue(imagematch.matches(target, close, 2))

    def test_a_channel_beyond_the_fuzz_does_not_match(self) -> None:
        target = swatch((0x2A, 0x2A, 0x2A))
        off = swatch((0x2C, 0x2A, 0x2A))

        self.assertFalse(imagematch.matches(target, off, 1))

    def test_the_fuzz_is_per_channel_not_a_total(self) -> None:
        """A swapped channel is far away however well the others agree."""
        target = swatch((0xB4, 0x28, 0x28))
        swapped = swatch((0x28, 0x28, 0xB4))

        self.assertFalse(imagematch.matches(target, swapped, 100))

    def test_different_sizes_never_match(self) -> None:
        target = swatch((0x2A, 0x2A, 0x2A), (0x2A, 0x2A, 0x2A))
        screen = swatch((0x2A, 0x2A, 0x2A))

        self.assertFalse(imagematch.matches(target, screen, 255))

    def test_blur_carries_a_lossy_frame_through(self) -> None:
        target = swatch((0x2A,) * 3, (0x2A,) * 3, (0x2A,) * 3)
        screen = swatch((0x2A,) * 3, (0x6A,) * 3, (0x2A,) * 3)

        self.assertFalse(imagematch.matches(target, screen, 20))
        self.assertTrue(imagematch.matches(target, screen, 20, blur=2))


class TestFuzzForFormat(unittest.TestCase):
    def test_rgb565_needs_room_for_its_widest_channel_step(self) -> None:
        # 5-bit channels jump in steps of 255/32, the 6-bit one in 255/64;
        # the widest step is the slack an exact comparison can never meet.
        self.assertEqual(imagematch.fuzz_for_format(PIXEL_FORMATS["rgb565"]), 7)

    def test_full_8_bit_channels_need_no_slack(self) -> None:
        self.assertEqual(imagematch.fuzz_for_format(PIXEL_FORMATS["rgbx8888"]), 0)
        self.assertEqual(imagematch.fuzz_for_format(PIXEL_FORMATS["bgrx8888"]), 0)

    def test_a_format_pillow_cannot_unpack_is_unsupported(self) -> None:
        colour_mapped = rfb.PixelFormat(8, 8, False, False, 0, 0, 0, 0, 0, 0)

        with self.assertRaises(pixelformat.UnsupportedPixelFormat):
            imagematch.fuzz_for_format(colour_mapped)


if __name__ == "__main__":
    unittest.main()
