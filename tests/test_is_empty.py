"""Weekend Is Empty (nodes.py): the yes/no the bundled Flux3 Klein Edit switches on. Needs torch,
which ComfyUI ships; ComfyUI's own modules are stood in for, since nodes.py imports them."""
import sys
import types
import unittest

try:
    import torch
    HAVE_TORCH = True
except ImportError:   # pragma: no cover
    HAVE_TORCH = False


@unittest.skipUnless(HAVE_TORCH, "needs torch (run with ComfyUI's Python)")
class IsEmpty(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for name in ("folder_paths", "node_helpers"):
            sys.modules.setdefault(name, types.ModuleType(name))
        from weekend_studio import nodes
        cls.nodes = nodes

    def test_black_is_empty_and_any_white_is_not(self):
        check = self.nodes.WeekendIsEmpty().check
        self.assertEqual(check(torch.zeros(1, 64, 64)), (True,))
        m = torch.zeros(1, 64, 64); m[0, 10, 10] = 1.0
        self.assertEqual(check(m), (False,))

    def test_threshold(self):
        check = self.nodes.WeekendIsEmpty().check
        faint = torch.full((1, 8, 8), 0.02)
        self.assertEqual(check(faint, 0.03), (True,), "detail under the threshold counts as blank")
        self.assertEqual(check(faint, 0.0), (False,))

    def test_no_pixels_is_empty(self):
        self.assertTrue(self.nodes.mask_is_empty(torch.zeros(0)))

    def test_image_in_says_whether_it_was_sent(self):
        node = self.nodes.WeekendImageIn()
        img, mask, provided = node.load("reference", "Reference 1")
        self.assertFalse(provided, "a reference left out is not an error")
        self.assertEqual(tuple(img.shape), (1, 64, 64, 3))
        fb = torch.ones(1, 8, 8, 3)
        self.assertEqual(node.load("area", "Image", fallback=fb)[2], False)
        with self.assertRaises(ValueError):
            node.load("area", "Image")   # the area, layer and canvas still need a picture or a fallback
        self.assertEqual(self.nodes.WeekendImageIn.RETURN_TYPES, ("IMAGE", "MASK", "BOOLEAN"))
        self.assertIn("reference", self.nodes.WeekendImageIn.INPUT_TYPES()["required"]["source"][0])

    def test_registered_under_its_names(self):
        self.assertIn("WeekendIsEmpty", self.nodes.NODE_CLASS_MAPPINGS)
        self.assertEqual(self.nodes.NODE_DISPLAY_NAME_MAPPINGS["WeekendIsEmpty"], "Weekend Is Empty")
        self.assertEqual(self.nodes.WeekendIsEmpty.RETURN_TYPES, ("BOOLEAN",))


if __name__ == "__main__":
    unittest.main()
