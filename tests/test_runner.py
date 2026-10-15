import os
import tempfile
import unittest

from weekend_studio.runner import RunError, Runner
from weekend_studio.store import WorkflowStore
from tests.helpers import fx

OBJECT_INFO = fx("object_info.json")
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32


class FakeComfy:
    """Stands in for ComfyUI's API. `script` is the events a run yields after `queued`."""

    def __init__(self, script=None, outputs=None):
        self.script = script if script is not None else [{"type": "progress", "value": 1, "max": 2, "node": "3"}, {"type": "done"}]
        self.history = outputs if outputs is not None else {"20": {"images": [{"filename": "weekend-studio_00001_.png", "subfolder": "", "type": "temp"}]}}
        self.queued = []
        self.canceled = []
        self.on_queued = None

    async def object_info(self, fresh=False):
        return OBJECT_INFO

    async def run(self, graph):
        self.queued.append(graph)
        yield {"type": "queued", "prompt_id": "p1", "position": 0}
        if self.on_queued:
            await self.on_queued()
        for ev in self.script:
            yield ev

    async def outputs(self, prompt_id):
        return self.history

    async def view(self, image):
        return PNG

    async def cancel(self, prompt_id):
        self.canceled.append(prompt_id)


class RunnerTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.input_dir = os.path.join(self.tmp.name, "input")
        os.makedirs(self.input_dir)
        self.store = WorkflowStore(os.path.join(self.tmp.name, "store"))
        self.rec = self.store.publish("Inpaint", fx("inpaint.api.json"))
        self.version = self.store.describe(self.rec, OBJECT_INFO)["version"]
        self.events, self.outputs, self.fetched = [], [], []

    def tearDown(self):
        self.tmp.cleanup()

    def job(self, **kw):
        j = {"jobId": "job1", "workflowId": "inpaint", "version": self.version,
             "values": {"6.text": "a blue door"}, "images": ["12.weekend_ref", "13.weekend_ref"]}
        j.update(kw)
        return j

    async def go(self, client, job, image=PNG):
        runner = Runner(self.store, client, self.input_dir)
        seen_files = []

        async def fetch_input(key):
            self.fetched.append(key)
            return image

        async def put_output(i, data, mime):
            self.outputs.append((i, len(data), mime))

        async def emit(ev):
            self.events.append(ev)
            if ev["type"] == "queued":
                seen_files.extend(os.listdir(os.path.join(self.input_dir, "weekend-studio", job["jobId"])))

        result = await runner.run(job, fetch_input=fetch_input, put_output=put_output, emit=emit)
        return runner, result, seen_files

    async def test_happy_path(self):
        client = FakeComfy()
        _, result, files = await self.go(client, self.job())
        self.assertEqual(result, {"outputs": 1, "promptId": "p1"})
        graph = client.queued[0]
        self.assertEqual(graph["6"]["inputs"]["text"], "a blue door")
        self.assertEqual(graph["12"]["inputs"]["weekend_ref"], "weekend-studio/job1/in0.png")
        self.assertEqual(graph["13"]["inputs"]["weekend_ref"], "weekend-studio/job1/in1.png")
        self.assertEqual(sorted(files), ["in0.png", "in1.png"], "inputs on disk while it runs")
        self.assertFalse(os.path.exists(os.path.join(self.input_dir, "weekend-studio", "job1")), "cleaned up after")
        self.assertEqual(self.outputs, [(0, len(PNG), "image/png")])
        self.assertEqual([e["type"] for e in self.events], ["queued", "progress", "done"])

    async def assertRefused(self, code, client=None, **kw):
        client = client or FakeComfy()
        with self.assertRaises(RunError) as e:
            await self.go(client, kw.pop("job", None) or self.job(), **kw)
        self.assertEqual(e.exception.code, code)
        return client, e.exception

    async def test_stale_version_queues_nothing(self):
        client, _ = await self.assertRefused("stale", job=self.job(version="0" * 20))
        self.assertEqual(client.queued, [])
        self.assertEqual(self.fetched, [], "no bytes fetched for a run that can't happen")

    async def test_bad_values_queue_nothing(self):
        client, err = await self.assertRefused("invalid", job=self.job(values={"3.steps": 100000}))
        self.assertEqual(err.details, [{"key": "3.steps", "code": "range"}])
        self.assertEqual(client.queued, [])

    async def test_a_graph_in_the_job_is_ignored(self):
        evil = {"1": {"class_type": "PythonExec", "inputs": {"code": "import os"}}}
        client = FakeComfy()
        await self.go(client, self.job(graph=evil, prompt=evil))
        self.assertNotIn("PythonExec", [n["class_type"] for n in client.queued[0].values()])

    async def test_not_an_image(self):
        await self.assertRefused("bad-image", image=b"#!/bin/sh\nrm -rf /")
        self.assertFalse(os.path.exists(os.path.join(self.input_dir, "weekend-studio", "job1")))

    async def test_bad_job_id(self):
        await self.assertRefused("invalid", job=self.job(jobId="../../etc"))

    async def test_missing_nodes(self):
        self.store.publish("Up", fx("missing-node.api.json"))
        await self.assertRefused("missing-nodes", job=self.job(workflowId="up", images=[], values={},
                                 version=self.store.describe(self.store.get("up"), OBJECT_INFO)["version"]))

    async def test_comfy_error(self):
        _, err = await self.assertRefused("comfy", client=FakeComfy(script=[{"type": "error", "message": "CUDA out of memory", "node": "3"}]))
        self.assertIn("out of memory", str(err))

    async def test_lost_contact(self):
        await self.assertRefused("comfy", client=FakeComfy(script=[{"type": "progress", "value": 1, "max": 2}]))

    async def test_no_output(self):
        await self.assertRefused("no-output", client=FakeComfy(outputs={}))

    async def test_cancel_while_running(self):
        client = FakeComfy(script=[{"type": "interrupted"}])
        runner_box = {}

        async def cancel_now():
            await runner_box["r"].cancel("job1")

        client.on_queued = cancel_now
        runner = Runner(self.store, client, self.input_dir)
        runner_box["r"] = runner

        async def nothing(*_a):
            return None

        async def fetch(_k):
            return PNG

        with self.assertRaises(RunError) as e:
            await runner.run(self.job(), fetch_input=fetch, put_output=nothing, emit=nothing)
        self.assertEqual(e.exception.code, "canceled")
        self.assertEqual(client.canceled, ["p1"])
        self.assertFalse(await runner.cancel("job1"), "finished jobs can't be canceled")


if __name__ == "__main__":
    unittest.main()
