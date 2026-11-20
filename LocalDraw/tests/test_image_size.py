import unittest

from comic_workflow import image_size


class ImageSizeTests(unittest.TestCase):
    def test_existing_sizes_and_equivalent_ratios_are_preserved(self):
        for ratio, expected in image_size.DEFAULT_SIZES.items():
            self.assertEqual(image_size.dimensions(ratio), expected)
        self.assertEqual(image_size.dimensions("6:4"), (1216, 832))

    def test_custom_ratio_resolution_orientation_and_latent_rounding(self):
        self.assertEqual(image_size.dimensions("7:5", "1536"), (1536, 1096))
        self.assertEqual(image_size.dimensions("5:7", "1536"), (1096, 1536))
        self.assertEqual(image_size.dimensions("16:9", "2048"), (2048, 1152))
        for ratio in ["1:8", "8:1", "7:5", "1:1", "9999:9998"]:
            for resolution in image_size.RESOLUTIONS[1:]:
                width, height = image_size.dimensions(ratio, resolution)
                self.assertEqual(max(width, height), int(resolution))
                self.assertTrue(width % 8 == height % 8 == 0)

    def test_invalid_values_rejected(self):
        for ratio in [None, [], 1, "0:1", "1:0", "-1:2", "1.5:1", "10000:1", "9:1", "1:9", "NaN:1"]:
            with self.subTest(ratio=ratio), self.assertRaises(ValueError):
                image_size.dimensions(ratio)
        for resolution in [None, [], 1024, "0", "4096", "bad"]:
            with self.subTest(resolution=resolution), self.assertRaises(ValueError):
                image_size.dimensions("1:1", resolution)

    def test_moss_dimensions_match_its_sixteen_pixel_latent_grid(self):
        self.assertEqual(image_size.dimensions("3:2", "1024", alignment=16), (1024, 688))
        self.assertEqual(image_size.dimensions("5:7", "1536", alignment=16), (1104, 1536))
        for ratio in image_size.DEFAULT_SIZES:
            self.assertEqual(image_size.dimensions(ratio, alignment=16), image_size.DEFAULT_SIZES[ratio])
