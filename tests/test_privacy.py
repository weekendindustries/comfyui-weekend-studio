"""What leaves the computer (privacy.py, 0.3.0, security review B5)."""
import unittest

from weekend_studio.privacy import default_machine_name, scrub, scrub_all


class Privacy(unittest.TestCase):
    def test_home_paths_become_tilde(self):
        self.assertEqual(scrub("No such file: /Users/firstname/ComfyUI/models/x.safetensors", homes=[]),
                         "No such file: ~/ComfyUI/models/x.safetensors")
        self.assertEqual(scrub(r"C:\Users\First Last\ComfyUI\models", homes=[]), r"~\ComfyUI\models")
        self.assertEqual(scrub("/home/sam/ComfyUI/x and /root/ComfyUI/y", homes=[]), "~/ComfyUI/x and ~/ComfyUI/y")
        self.assertEqual(scrub("D:/Users/sam/x", homes=[]), "~/x")
        self.assertEqual(scrub("in /srv/x/home.py", homes=[]), "in /srv/x/home.py", "other paths stay")
        self.assertEqual(scrub("/data/me/ComfyUI failed", homes=["/data/me"]), "~/ComfyUI failed", "the real home folder too")
        self.assertIsNone(scrub(None))

    def test_details_too(self):
        self.assertEqual(scrub_all({"node": "4", "why": ["/Users/a/x", 3]}), {"node": "4", "why": ["~/x", 3]})

    def test_default_name_is_never_the_hostname(self):
        self.assertEqual(default_machine_name("Darwin"), "ComfyUI (macOS)")
        self.assertEqual(default_machine_name("Windows"), "ComfyUI (Windows)")
        self.assertEqual(default_machine_name("Linux"), "ComfyUI (Linux)")
        self.assertEqual(default_machine_name("Plan9"), "ComfyUI")


if __name__ == "__main__":
    unittest.main()
