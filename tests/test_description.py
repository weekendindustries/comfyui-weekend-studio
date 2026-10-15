"""The description contract from the node's side. The value cases are SHARED with
packages/comfy/description.test.mjs: both sides must agree on every error, in order."""
import copy
import unittest

from weekend_studio.description import (
    MAX_SAFE, build_description, validate_description, validate_values, version_of,
)
from tests.helpers import fx

OBJECT_INFO = fx("object_info.json")
SHARED = fx("values.cases.json")


def describe(d):
    if isinstance(d, str):
        return SHARED["inline"].get(d) or fx(f"{d}.description.json")
    return d


class Golden(unittest.TestCase):
    """The committed descriptions are what the builder makes. A change here is a contract change:
    regenerate the fixtures deliberately and check the JS tests still pass."""

    def test_builds_match_fixtures(self):
        cases = [("txt2img", "Txt2img", None), ("inpaint", "Inpaint", None), ("missing-node", "Missing node", None),
                 ("inpaint-marked", "Inpaint (marked)", fx("inpaint.marks.json"))]
        for wid, name, marks in cases:
            graph = fx(("inpaint" if wid == "inpaint-marked" else wid) + ".api.json")
            built = build_description(wid, name, graph, OBJECT_INFO, marks)
            self.assertEqual(built, fx(f"{wid}.description.json"), wid)
            self.assertTrue(validate_description(built)["ok"], wid)


class SharedCases(unittest.TestCase):
    def test_values(self):
        for c in SHARED["cases"]:
            r = validate_values(describe(c["description"]), c["values"], c.get("images", []))
            self.assertEqual(r["ok"], c["ok"], c["name"])
            self.assertEqual(r["errors"], c["errors"], c["name"])
            if "normalized" in c:
                self.assertEqual(r["values"], c["normalized"], c["name"])

    def test_bad_descriptions(self):
        for c in SHARED["badDescriptions"]:
            d = copy.deepcopy(SHARED["inline"]["small"])
            d.update(c.get("patch", {}))
            d["inputs"].extend(c.get("patchInputs", []))
            r = validate_description(d)
            self.assertFalse(r["ok"], c["name"])
            self.assertTrue(any(c["error"] in e for e in r["errors"]), f"{c['name']}: {r['errors']}")
        self.assertFalse(validate_description(None)["ok"])
        self.assertFalse(validate_description([])["ok"])


class Building(unittest.TestCase):
    def test_seed_max_is_clamped_to_safe_integer(self):
        d = build_description("t", "T", fx("txt2img.api.json"), OBJECT_INFO)
        seed = next(i for i in d["inputs"] if i["type"] == "seed")
        self.assertEqual(seed["max"], MAX_SAFE)

    def test_prompt_traced_through_conditioning_combine(self):
        d = build_description("i", "I", fx("inpaint.api.json"), OBJECT_INFO)
        style = next(i for i in d["inputs"] if i["key"] == "16.text")
        self.assertEqual(style["label"], "Style")   # its renamed title
        self.assertNotIn("role", style)            # only the first positive text is THE prompt

    def test_unwired_encoders_still_show(self):
        graph = {"1": {"class_type": "CLIPTextEncode", "inputs": {"text": "hello", "clip": ["2", 1]}},
                 "2": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"}}}
        d = build_description("u", "U", graph, OBJECT_INFO)
        self.assertEqual([i["key"] for i in d["inputs"]], ["1.text"])
        self.assertEqual(d["inputs"][0]["role"], "prompt")
        self.assertEqual(d["outputs"], [])

    def test_mark_cannot_widen_a_range(self):
        d = build_description("i", "I", fx("inpaint.api.json"), OBJECT_INFO,
                              [{"node": "3", "input": "denoise", "min": -5, "max": 5}])
        den = next(i for i in d["inputs"] if i["key"] == "3.denoise")
        self.assertEqual((den["min"], den["max"]), (0.0, 1.0))

    def test_marked_load_image_keeps_role(self):
        d = build_description("t", "T", {"1": {"class_type": "LoadImage", "inputs": {"image": "example.png"}}},
                              OBJECT_INFO, [{"node": "1", "input": "image", "role": "area", "label": "Photo"}])
        self.assertEqual(d["inputs"], [{"key": "1.image", "node": "1", "input": "image", "label": "Photo",
                                        "type": "image", "role": "area"}])

    def test_optional_when_fallback_is_wired(self):
        graph = copy.deepcopy(fx("inpaint.api.json"))
        graph["12"]["inputs"]["fallback"] = ["30", 0]
        graph["30"] = {"class_type": "LoadImage", "inputs": {"image": "example.png"}}
        d = build_description("i", "I", graph, OBJECT_INFO)
        area = next(i for i in d["inputs"] if i["key"] == "12.weekend_ref")
        self.assertTrue(area["optional"])
        ref = next(i for i in d["inputs"] if i["key"] == "30.image")
        self.assertEqual(ref["role"], "reference")   # a bridge node owns the area

    def test_version_tracks_what_was_published_not_object_info(self):
        g = fx("txt2img.api.json")
        v = version_of("T", g, None)
        self.assertEqual(v, version_of("T", dict(reversed(list(g.items()))), None), "key order doesn't matter")
        g2 = copy.deepcopy(g)
        g2["3"]["inputs"]["steps"] = 21
        self.assertNotEqual(v, version_of("T", g2, None))
        oi = copy.deepcopy(OBJECT_INFO)
        oi["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0].append("new.safetensors")
        self.assertEqual(build_description("t", "T", g, oi)["version"], build_description("t", "T", g, OBJECT_INFO)["version"])


if __name__ == "__main__":
    unittest.main()
