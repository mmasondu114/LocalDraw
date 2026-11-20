import unittest
from PIL import Image, ImageChops, ImageOps
from comic_workflow import repair_masks


class RepairMaskTests(unittest.TestCase):
    def test_feathered_cuff_edit_preserves_all_pixels_outside_drawn_shape(self):
        rect = [.2,.2,.8,.8]
        outline = [[.2,.2],[.8,.2],[.4,.8]]
        hard = repair_masks.edit_mask((200,160), rect, outline)
        blend = repair_masks.blend_mask(hard)
        original = Image.new('RGB', hard.size, '#223344')
        regenerated = Image.new('RGB', hard.size, '#ffffff')
        result = Image.composite(regenerated, original, blend.convert('L'))
        diff = ImageChops.difference(result, original)
        self.assertIsNone(ImageChops.multiply(diff, ImageOps.invert(hard)).getbbox())
        self.assertEqual(result.getpixel((155,120)), original.getpixel((155,120)))
        self.assertNotEqual(result.getpixel((80,60)), original.getpixel((80,60)))
        self.assertGreater(sum(blend.convert('L').histogram()[1:255]), 0)

    def test_outline_rejects_nonfinite_or_outside_selection_before_rendering(self):
        rect = [.2,.2,.8,.8]
        for points in ([[.2,.2],[.8,.2]], [[.2,.2],[.8,.2],[.4,.9]],
                       [[.2,.2],[.8,.2],[float('nan'),.6]], [[.2,.2],[.8,.2],[True,.6]]):
            with self.subTest(points=points), self.assertRaises(ValueError):
                repair_masks.validate_outline(points, rect)


if __name__ == '__main__': unittest.main()
