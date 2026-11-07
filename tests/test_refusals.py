"""Settings that may never be set from the web (description.py remote_refusal, 0.3.0, security
review B3), and the text caps. The tab's picker mirrors the word lists; this checks they agree."""
import copy
import re
import unittest
from pathlib import Path

from weekend_studio.description import (
    LIMITS, REFUSED_NODE_WORDS, REFUSED_WORDS, build_description, mark_refusals, remote_refusal, validate_description,
    validate_values,
)
from tests.helpers import fx

OBJECT_INFO = fx("object_info.json")

# Stand-ins for packs that write files or run code, and a harmless text setting.
EXTRA = {
    "SaveTextFile": {"name": "SaveTextFile", "display_name": "Save Text File", "output": [],
                     "input": {"required": {"text": ["STRING", {"multiline": True}], "path": ["STRING", {}],
                                            "filename_prefix": ["STRING", {"default": "x"}]}}},
    "RunPythonCode": {"name": "RunPythonCode", "display_name": "Run Python", "output": ["STRING"],
                      "input": {"required": {"source": ["STRING", {"multiline": True}], "repeat": ["INT", {"default": 1, "min": 0, "max": 9}]}}},
    "StyleText": {"name": "StyleText", "display_name": "Style", "output": ["STRING"],
                  "input": {"required": {"style": ["STRING", {"multiline": True, "default": "watercolor"}]}}},
    "WebLoader": {"name": "WebLoader", "display_name": "Load from the web", "output": ["IMAGE"],
                  "input": {"required": {"image_url": ["STRING", {}]}}},
}
OI = {**OBJECT_INFO, **EXTRA}


def graph():
    g = copy.deepcopy(fx("txt2img.api.json"))
    g.update({
        "50": {"class_type": "SaveTextFile", "inputs": {"text": "hi", "path": "./out.txt", "filename_prefix": "x"}},
        "51": {"class_type": "RunPythonCode", "inputs": {"source": "print(1)", "repeat": 1}},
        "52": {"class_type": "StyleText", "inputs": {"style": "watercolor"}},
        "53": {"class_type": "WebLoader", "inputs": {"image_url": "https://example.com/a.png"}},
    })
    return g


class Refusals(unittest.TestCase):
    def test_rules(self):
        self.assertIsNotNone(remote_refusal("SaveTextFile", EXTRA["SaveTextFile"], "path"))
        self.assertIsNotNone(remote_refusal("SaveTextFile", EXTRA["SaveTextFile"], "filename_prefix"))
        self.assertIsNone(remote_refusal("SaveTextFile", EXTRA["SaveTextFile"], "text"), "content is text, not a path")
        self.assertIsNotNone(remote_refusal("RunPythonCode", EXTRA["RunPythonCode"], "source"), "a code node's text")
        self.assertIsNone(remote_refusal("RunPythonCode", EXTRA["RunPythonCode"], "repeat"), "numbers can't be code")
        self.assertIsNotNone(remote_refusal("WebLoader", EXTRA["WebLoader"], "image_url"))
        self.assertIsNone(remote_refusal("StyleText", EXTRA["StyleText"], "style"))
        # A text encoder's prompt is always fine ("CLIPTextEncode" holds "code" in its letters, not its words).
        self.assertIsNone(remote_refusal("CLIPTextEncode", OBJECT_INFO["CLIPTextEncode"], "text"))

    def test_refused_marks_never_reach_the_description(self):
        marks = [{"node": "50", "input": "path"}, {"node": "50", "input": "filename_prefix"}, {"node": "51", "input": "source"},
                 {"node": "53", "input": "image_url"}, {"node": "52", "input": "style"}, {"node": "50", "input": "text"}]
        d = build_description("w", "W", graph(), OI, marks)
        keys = {i["key"] for i in d["inputs"]}
        self.assertEqual(keys, {"52.style", "50.text"})
        self.assertTrue(validate_description(d)["ok"], validate_description(d)["errors"])
        # So a run can't set them either: an unknown key is refused.
        self.assertEqual(validate_values(d, {"50.path": "/x/custom_nodes/evil/__init__.py"})["errors"][0]["code"], "unknown")
        self.assertEqual(len(mark_refusals(graph(), OI, marks)), 4)

    def test_text_caps(self):
        d = build_description("w", "W", graph(), OI, [{"node": "52", "input": "style"}, {"node": "6", "input": "text"}])
        by = {i["key"]: i for i in d["inputs"]}
        self.assertEqual(by["52.style"]["maxLength"], LIMITS["text_other"])
        self.assertNotIn("maxLength", by["6.text"], "a prompt keeps the long limit")
        self.assertEqual(validate_values(d, {"52.style": "x" * (LIMITS["text_other"] + 1)})["errors"][0]["code"], "too-long")
        self.assertTrue(validate_values(d, {"6.text": "x" * 10_000})["ok"])

    def test_the_tab_has_the_same_lists(self):
        js = (Path(__file__).resolve().parents[1] / "web" / "weekend_studio.js").read_text()

        def js_set(name):
            m = re.search(rf"const {name} = new Set\(\[(.*?)\]\);", js, re.S)
            self.assertIsNotNone(m, name)
            return set(re.findall(r'"([^"]+)"', m.group(1)))
        self.assertEqual(js_set("REFUSED_WORDS"), set(REFUSED_WORDS))
        self.assertEqual(js_set("REFUSED_NODE_WORDS"), set(REFUSED_NODE_WORDS))


if __name__ == "__main__":
    unittest.main()
