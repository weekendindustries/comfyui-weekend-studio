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
        self.assertEqual((a["id"], b["id"]), ("portrait-v2", "portrait-v2-2"))
        updated = self.store.publish("Portrait v2", fx("inpaint.api.json"), workflow_id="portrait-v2")
        self.assertEqual(updated["published_at"], a["published_at"])
        self.assertEqual([r["id"] for r in self.store.list()], ["portrait-v2", "portrait-v2-2"])
        self.store.remove("portrait-v2-2")
        self.assertEqual(len(self.store.list()), 1)
        with self.assertRaises(StoreError):
            self.store.remove("portrait-v2-2")

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
        self.assertEqual([r["id"] for r in self.store.list()], ["good"])

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
