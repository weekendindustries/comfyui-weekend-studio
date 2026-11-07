"""The description contract from the node's side. The value cases are SHARED with
packages/comfy/description.test.mjs: both sides must agree on every error, in order."""
import copy
import json
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

    def test_a_ticked_prompt_keeps_its_prompt_role(self):
        # 2026-11-06: ticking the prompt in ComfyUI dropped its role, so Images showed a second text
        # box and never filled it from its own prompt box. A mark without a role gets the unmarked one.
        d = build_description("t", "T", fx("txt2img.api.json"), OBJECT_INFO,
                              [{"node": "6", "input": "text", "label": "Describe it"}, {"node": "7", "input": "text"},
                               {"node": "5", "input": "width"}, {"node": "3", "input": "steps"}])
        roles = {i["key"]: i.get("role") for i in d["inputs"]}
        self.assertEqual(roles["6.text"], "prompt")
        self.assertEqual(roles["7.text"], "negative")
        self.assertEqual(roles["5.width"], "width")
        self.assertIsNone(roles["3.steps"])
        # A role the mark states still wins.
        d = build_description("t", "T", fx("txt2img.api.json"), OBJECT_INFO, [{"node": "6", "input": "text", "role": "negative"}])
        self.assertEqual(d["inputs"][0]["role"], "negative")

    def test_a_reference_image_in_is_an_optional_reference(self):
        graph = {"1": {"class_type": "WeekendImageIn", "inputs": {"source": "reference", "label": "Reference 1", "weekend_ref": ""}},
                 "2": {"class_type": "WeekendImageIn", "inputs": {"source": "reference", "weekend_ref": ""}}}
        d = build_description("r", "R", graph, OBJECT_INFO)
        self.assertEqual([(i["role"], i["label"], i.get("optional")) for i in d["inputs"]],
                         [("reference", "Reference 1", True), ("reference", "Reference", True)])

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

class OddNodeSpecs(unittest.TestCase):
    """0.2.2: a node pack's ±Infinity / NaN / string bounds. Infinity reached the app as bare
    `Infinity` (not JSON: /v1/hello stopped parsing); int(inf) raised and stopped the hello."""

    def build_with(self, cfg_spec, steps_spec):
        oi = copy.deepcopy(OBJECT_INFO)
        oi["KSampler"]["input"]["required"]["cfg"] = ["FLOAT", cfg_spec]
        oi["KSampler"]["input"]["required"]["steps"] = ["INT", steps_spec]
        d = build_description("txt2img", "Txt2img", fx("txt2img.api.json"), oi)
        return d, {i["input"]: i for i in d["inputs"]}

    def test_infinite_and_junk_bounds_fall_back(self):
        d, by = self.build_with({"default": 8.0, "min": float("-inf"), "max": float("inf")},
                                {"default": 20, "min": "one", "max": float("inf")})
        self.assertEqual((by["cfg"]["min"], by["cfg"]["max"]), (-1e9, 1e9))
        self.assertEqual((by["steps"]["min"], by["steps"]["max"]), (-MAX_SAFE, MAX_SAFE))
        self.assertEqual(by["cfg"]["default"], 8)
        json.dumps(d, allow_nan=False)                      # strict JSON: what a browser can parse
        self.assertTrue(validate_description(d)["ok"])

    def test_nan_default_and_reversed_range(self):
        _, by = self.build_with({"default": float("nan"), "min": 5.0, "max": 1.0}, {"default": 20, "min": 1, "max": 50})
        self.assertEqual((by["cfg"]["min"], by["cfg"]["max"]), (1.0, 5.0))
        self.assertTrue(1.0 <= by["cfg"]["default"] <= 5.0)

    def test_finite_float_ranges_are_kept(self):
        _, by = self.build_with({"default": 8.0, "min": 0.0, "max": 1e12}, {"default": 20, "min": 1, "max": 10000})
        self.assertEqual(by["cfg"]["max"], 1e12)


class VersionIgnoresShownValues(unittest.TestCase):
    """0.2.2: the app sends its own value for every setting it shows, so a new default there (a seed
    ComfyUI re-rolled, a prompt edited) mustn't make open forms stale. Anything else still does."""

    def version(self, graph, marks=None):
        return build_description("txt2img", "Txt2img", graph, OBJECT_INFO, marks)["version"]

    def test_shown_values_dont_count(self):
        g = fx("txt2img.api.json")
        v = self.version(g)
        g2 = copy.deepcopy(g)
        g2["3"]["inputs"]["seed"] = 42
        g2["6"]["inputs"]["text"] = "a different prompt"
        self.assertEqual(self.version(g2), v)

    def test_everything_else_does(self):
        g = fx("txt2img.api.json")
        v = self.version(g)
        for change in (lambda x: x["4"]["inputs"].__setitem__("ckpt_name", "other.safetensors"),   # not shown
                       lambda x: x["5"]["inputs"].__setitem__("batch_size", 2),
                       lambda x: x["3"]["inputs"].__setitem__("model", ["9", 0])):                    # a wire
            g2 = copy.deepcopy(g)
            change(g2)
            self.assertNotEqual(self.version(g2), v)

    def test_unshown_seed_still_counts_when_marks_pick_others(self):
        g = fx("txt2img.api.json")
        marks = [{"node": "3", "input": "steps"}]
        v = self.version(g, marks)
        g2 = copy.deepcopy(g)
        g2["3"]["inputs"]["seed"] = 42
        self.assertNotEqual(self.version(g2, marks), v, "the app can't set it, so a run depends on it")


class ProblemsInWords(unittest.TestCase):
    def test_a_description_the_app_would_drop_is_explained(self):
        from weekend_studio.description import description_problems
        d = build_description("txt2img", "Txt2img", fx("txt2img.api.json"), OBJECT_INFO)
        self.assertEqual(description_problems(d), [])
        d["inputs"][2]["key"] = d["inputs"][2]["input"] = "my setting"
        [line] = description_problems(d)
        self.assertIn(f"\u201c{d['inputs'][2]['label']}\u201d: its name has characters Weekend Studio doesn't accept", line)


if __name__ == "__main__":
    unittest.main()
