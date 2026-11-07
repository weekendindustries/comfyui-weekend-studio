import json
import os
import tempfile
import unittest

from weekend_studio.description import build_description, validate_values
from weekend_studio.graph import GraphError, fill_graph
from weekend_studio.store import StoreError, WorkflowStore
from tests.helpers import fx

OBJECT_INFO = fx("object_info.json")


class Store(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = WorkflowStore(os.path.join(self.tmp.name, "weekend-studio"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_publish_list_update_remove(self):
        a = self.store.publish("Portrait v2", fx("txt2img.api.json"))
        b = self.store.publish("Portrait v2", fx("txt2img.api.json"))
        # Random ids (0.3.0, B2): a name slug plus 6 hex, so another ComfyUI's "txt2img" never lands on yours.
        for rec in (a, b):
            self.assertRegex(rec["id"], r"^portrait-v2-[0-9a-f]{6}$")
        self.assertNotEqual(a["id"], b["id"])
        updated = self.store.publish("Portrait v2", fx("inpaint.api.json"), workflow_id=a["id"])
        self.assertEqual(updated["published_at"], a["published_at"])
        self.assertEqual(sorted(r["id"] for r in self.store.list()), sorted([a["id"], b["id"]]))
        self.store.remove(b["id"])
        self.assertEqual(len(self.store.list()), 1)
        with self.assertRaises(StoreError):
            self.store.remove(b["id"])
        with self.assertRaises(StoreError) as e:   # an update never creates: an unknown id is refused
            self.store.publish("Portrait v2", fx("txt2img.api.json"), workflow_id="txt2img")
        self.assertEqual(e.exception.code, "not-found")

    def test_show_in_hides_without_making_anything_stale(self):
        # The tab's "Show in" (0.3.1): only the list changes — not the graph, marks or version.
        rec = self.store.publish("Portrait", fx("txt2img.api.json"))
        before = self.store.describe(rec, OBJECT_INFO)
        self.assertNotIn("hiddenIn", before, "shown everywhere by default")
        rec = self.store.set_hidden_in(rec["id"], ["moods", "moods", "Not An App!", 7, "animate"])
        after = self.store.describe(self.store.get(rec["id"]), OBJECT_INFO)
        self.assertEqual(after["hiddenIn"], ["moods", "animate"], "deduplicated, junk dropped")
        self.assertEqual(after["version"], before["version"], "open forms don't go stale")
        from weekend_studio.description import validate_description
        self.assertTrue(validate_description(after)["ok"], validate_description(after))
        # Republishing (an edit in ComfyUI) keeps where it shows.
        self.store.publish("Portrait", fx("txt2img.api.json"), workflow_id=rec["id"])
        self.assertEqual(self.store.get(rec["id"]).get("hidden_in"), ["moods", "animate"])
        # Shown everywhere again.
        self.store.set_hidden_in(rec["id"], [])
        self.assertNotIn("hiddenIn", self.store.describe(self.store.get(rec["id"]), OBJECT_INFO))
        with self.assertRaises(StoreError) as e:
            self.store.set_hidden_in(rec["id"], "moods")
        self.assertEqual(e.exception.code, "bad-apps")
        with self.assertRaises(StoreError):
            self.store.set_hidden_in("nope", [])

    def test_long_names_still_make_valid_ids(self):
        rec = self.store.publish("x" * 80, fx("txt2img.api.json"))
        self.assertLessEqual(len(rec["id"]), 64)
        self.assertEqual(self.store.get(rec["id"])["id"], rec["id"])

    def test_ui_format_is_refused(self):
        with self.assertRaises(StoreError) as e:
            self.store.publish("UI", {"nodes": [], "links": []})
        self.assertEqual(e.exception.code, "bad-graph")

    def test_ids_cannot_escape_the_folder(self):
        for bad in ("../secrets", "a/b", "", "UPPER"):
            with self.assertRaises(StoreError):
                self.store.get(bad)

    def test_a_broken_file_hides_itself(self):
        self.store.publish("Good", fx("txt2img.api.json"))
        with open(os.path.join(self.store.root, "broken.json"), "w") as f:
            f.write("{nope")
        self.assertEqual([r["name"] for r in self.store.list()], ["Good"])

    def test_describe_uses_current_object_info(self):
        rec = self.store.publish("Up", fx("missing-node.api.json"))
        self.assertEqual(self.store.describe(rec, OBJECT_INFO)["missing"], ["UltimateSDUpscale"])
        oi = dict(OBJECT_INFO, UltimateSDUpscale={"input": {"required": {}}, "output": ["IMAGE"]})
        self.assertNotIn("missing", self.store.describe(rec, oi))


class Fill(unittest.TestCase):
    def test_values_and_refs_land_and_original_is_untouched(self):
        graph = fx("inpaint.api.json")
        d = build_description("i", "I", graph, OBJECT_INFO)
        checked = validate_values(d, {"6.text": "a blue door", "3.steps": 12}, ["12.weekend_ref", "13.weekend_ref"])
        filled = fill_graph(graph, d, checked["values"], {"12.weekend_ref": "weekend-studio/j/in0.png",
                                                           "13.weekend_ref": "weekend-studio/j/in1.png"})
        self.assertEqual(filled["6"]["inputs"]["text"], "a blue door")
        self.assertEqual(filled["3"]["inputs"]["steps"], 12)
        self.assertEqual(filled["3"]["inputs"]["model"], ["10", 0], "wires untouched")
        self.assertEqual(filled["12"]["inputs"]["weekend_ref"], "weekend-studio/j/in0.png")
        self.assertEqual(graph["6"]["inputs"]["text"], "a red door", "the stored graph is not mutated")

    def test_a_setting_that_became_a_wire_is_refused(self):
        graph = fx("txt2img.api.json")
        d = build_description("t", "T", graph, OBJECT_INFO)
        graph["3"]["inputs"]["steps"] = ["99", 0]
        with self.assertRaises(GraphError):
            fill_graph(graph, d, validate_values(d, {})["values"], {})


if __name__ == "__main__":
    unittest.main()


class UnwiredOutputs(unittest.TestCase):
    """2026-10-14, a real PC: an unwired Weekend Output failed every run ("WeekendOutput: images")."""

    def graph(self):
        g = fx("txt2img.api.json")
        g["20"] = {"class_type": "WeekendOutput", "_meta": {"title": "Weekend Output"}, "inputs": {}}   # added, never wired
        return g

    def test_left_out_of_the_run_and_save_image_is_used(self):
        from weekend_studio.description import output_problems, outputs_of
        g = self.graph()
        self.assertEqual(outputs_of(g), [{"node": "9", "kind": "image"}], "falls back to the wired Save Image")
        d = build_description("t", "T", g, OBJECT_INFO)
        filled = fill_graph(g, d, validate_values(d, {})["values"], {}, OBJECT_INFO)
        self.assertNotIn("20", filled, "the unwired output node isn't sent to ComfyUI")
        self.assertIn("9", filled)
        self.assertIn("Weekend Output isn't connected", output_problems(g)[0])

    def test_nothing_wired_at_all_is_said_plainly(self):
        from weekend_studio.description import output_problems
        g = self.graph()
        del g["9"]
        self.assertTrue(any(p.startswith("Nothing comes back yet") for p in output_problems(g)))
        self.assertEqual(output_problems(fx("txt2img.api.json")), [], "a healthy workflow has nothing to say")
