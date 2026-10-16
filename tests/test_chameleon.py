import unittest
from PIL import Image, ImageDraw
from astock_hub.chameleon import background_color, foreground_color


class ChameleonTest(unittest.TestCase):
    def test_dominant_surrounding_color_ignores_strip_and_minor_colors(self):
        image = Image.new('RGB', (100, 40), '#e0e0e0')
        draw = ImageDraw.Draw(image)
        draw.rectangle((8, 8, 91, 31), fill='#ff0000')
        draw.rectangle((0, 0, 3, 39), fill='#000000')
        self.assertEqual(background_color(image, (8, 8, 92, 32)), '#e0e0e0')

    def test_no_background_pixels_returns_none(self):
        self.assertIsNone(background_color(Image.new('RGB', (10, 10)), (0, 0, 10, 10)))

    def test_text_contrast_tracks_light_and_dark_backgrounds(self):
        self.assertEqual(foreground_color('#ffffff'), '#17191d')
        self.assertEqual(foreground_color('#20272d'), '#f3f4f6')
