"""Same-computer mode (local_server.py): who may call, and a run over real HTTP.

The rules are pure and always tested. The HTTP half (preflight, refusals before the body is read, a
streamed run) needs aiohttp, which ComfyUI ships; without it those tests skip."""
import base64
import json
import os
import tempfile
import unittest

from weekend_studio.local_server import (
    DEFAULT_ORIGINS, LocalServer, allowed_origins, cors_headers, host_ok, origin_ok, parse_run,
)
from weekend_studio.runner import Runner
from weekend_studio.store import WorkflowStore
from tests.helpers import fx

try:
    import aiohttp  # noqa: F401
    from aiohttp.test_utils import TestClient, TestServer
    HAVE_AIOHTTP = True
except ImportError:
    HAVE_AIOHTTP = False

OBJECT_INFO = fx("object_info.json")
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 24
APP = "https://weekendindustries.app"


class Rules(unittest.TestCase):
    def test_origins(self):
        o = allowed_origins({}, env={})
        self.assertEqual(o, set(DEFAULT_ORIGINS))
        self.assertTrue(origin_ok(APP, o))
        self.assertTrue(origin_ok(APP + "/", o))
        for bad in (None, "", "null", "https://evil.example", "http://weekendindustries.app", "https://weekendindustries.app.evil.example"):
            self.assertFalse(origin_ok(bad, o), bad)
        dev = allowed_origins({}, env={"WEEKEND_STUDIO_LOCAL_ORIGINS": "http://localhost:5173, http://127.0.0.1:5173/"})
        self.assertTrue(origin_ok("http://localhost:5173", dev) and origin_ok("http://127.0.0.1:5173", dev))
        self.assertEqual(allowed_origins({"localOrigins": ["https://a.example"]}, env={}), {"https://a.example"})

    def test_hosts(self):
        self.assertTrue(host_ok("127.0.0.1:8189", 8189))
        self.assertTrue(host_ok("localhost:8189", 8189))
        for bad in (None, "", "127.0.0.1:8188", "evil.example:8189", "rebind.evil.example:8189", "127.0.0.1"):
            self.assertFalse(host_ok(bad, 8189), bad)

    def test_private_network_answer_only_when_asked(self):
        self.assertNotIn("Access-Control-Allow-Private-Network", cors_headers(APP))
        self.assertEqual(cors_headers(APP, private_network=True)["Access-Control-Allow-Private-Network"], "true")

    def test_parse_run(self):
        job, pics = parse_run({"jobId": "j1", "workflowId": "w", "version": "v", "values": {"a": 1},
                               "images": {"12.weekend_ref": base64.b64encode(PNG).decode()}})
        self.assertEqual(job, {"jobId": "j1", "workflowId": "w", "version": "v", "values": {"a": 1}, "images": ["12.weekend_ref"]})
        self.assertEqual(pics["12.weekend_ref"], PNG)
        for bad in ([], {"jobId": "../x"}, {"jobId": "j", "images": {"k": "not base64!"}}, {"jobId": "j", "images": ["x"]}):
            with self.assertRaises(ValueError):
                parse_run(bad)
        self.assertNotIn("graph", parse_run({"jobId": "j", "graph": {"1": {}}})[0], "a graph in the request goes nowhere")


class FakeComfy:
    async def object_info(self, fresh=False):
        return OBJECT_INFO

    async def run(self, graph):
        self.graph = graph
        yield {"type": "queued", "prompt_id": "p1", "position": 0}
        yield {"type": "progress", "value": 1, "max": 1, "node": "3"}
        yield {"type": "done"}

    async def outputs(self, prompt_id):
        return {"20": {"images": [{"filename": "weekend-studio_00001_.png", "subfolder": "", "type": "temp"}]}}

    async def view(self, image):
        return PNG

    async def cancel(self, prompt_id):
        pass


@unittest.skipUnless(HAVE_AIOHTTP, "aiohttp not installed (ComfyUI ships it)")
class OverHttp(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        store = WorkflowStore(os.path.join(self.tmp.name, "store"))
        store.publish("Inpaint", fx("inpaint.api.json"))
        self.version = store.describe(store.get("inpaint"), OBJECT_INFO)["version"]
        os.makedirs(os.path.join(self.tmp.name, "input"))
        self.comfy = FakeComfy()
        self.server = LocalServer(store=store, runner=Runner(store, self.comfy, os.path.join(self.tmp.name, "input")),
                                  comfy=self.comfy, version="0.1.0", machine_name="Studio PC", config={},
                                  machine_id=lambda: "22222222-2222-4222-8222-222222222222")
        self.client = TestClient(TestServer(self.server.app()))
        await self.client.start_server()
        self.server.port = self.client.port
        self.host = f"127.0.0.1:{self.client.port}"

    async def asyncTearDown(self):
        await self.client.close()
        self.tmp.cleanup()

    def h(self, origin=APP, host=None, **extra):
        return {"Origin": origin, "Host": host or self.host, **extra}

    async def test_hello_for_our_site(self):
        r = await self.client.get("/v1/hello", headers=self.h())
        self.assertEqual(r.status, 200)
        self.assertEqual(r.headers["Access-Control-Allow-Origin"], APP)
        body = await r.json()
        self.assertEqual((body["app"], body["machineName"], [w["id"] for w in body["workflows"]]), ("weekend-studio", "Studio PC", ["inpaint"]))
        self.assertEqual(body["machineId"], "22222222-2222-4222-8222-222222222222", "a connected ComfyUI says which account machine it is")

    async def test_everyone_else_is_refused(self):
        for headers in (self.h(origin="https://evil.example"), self.h(origin=None), self.h(host=f"rebind.evil.example:{self.client.port}")):
            headers = {k: v for k, v in headers.items() if v is not None}
            r = await self.client.get("/v1/hello", headers=headers)
            self.assertEqual(r.status, 403, headers)
            self.assertNotIn("Access-Control-Allow-Origin", r.headers)
        # A form-style POST from another site never reaches the runner.
        r = await self.client.post("/v1/run", data="jobId=x", headers={**self.h(origin="https://evil.example"), "Content-Type": "text/plain"})
        self.assertEqual(r.status, 403)
        self.assertFalse(hasattr(self.comfy, "graph"))

    async def test_preflight_answers_the_local_network_check(self):
        r = await self.client.options("/v1/run", headers=self.h(**{"Access-Control-Request-Method": "POST",
                                                                   "Access-Control-Request-Private-Network": "true"}))
        self.assertEqual(r.status, 204)
        self.assertEqual(r.headers["Access-Control-Allow-Private-Network"], "true")
        self.assertIn("content-type", r.headers["Access-Control-Allow-Headers"])
        r = await self.client.options("/v1/run", headers=self.h(origin="https://evil.example"))
        self.assertEqual(r.status, 403)

    async def test_json_only(self):
        r = await self.client.post("/v1/run", data="{}", headers={**self.h(), "Content-Type": "text/plain"})
        self.assertEqual(r.status, 415)

    async def test_a_streamed_run(self):
        body = {"jobId": "j1", "workflowId": "inpaint", "version": self.version, "values": {"6.text": "a blue door"},
                "images": {"12.weekend_ref": base64.b64encode(PNG).decode(), "13.weekend_ref": base64.b64encode(PNG).decode()}}
        r = await self.client.post("/v1/run", data=json.dumps(body), headers={**self.h(), "Content-Type": "application/json"})
        self.assertEqual(r.status, 200)
        self.assertEqual(r.headers["Access-Control-Allow-Origin"], APP)
        lines = [json.loads(x) for x in (await r.text()).splitlines() if x.strip()]
        self.assertEqual([x["type"] for x in lines], ["queued", "progress", "done"])
        self.assertEqual(base64.b64decode(lines[-1]["outputs"][0]["base64"]), PNG)
        self.assertEqual(self.comfy.graph["6"]["inputs"]["text"], "a blue door")

    async def test_a_refused_run_says_why_in_the_stream(self):
        body = {"jobId": "j2", "workflowId": "inpaint", "version": "0" * 20, "values": {}, "images": {}}
        r = await self.client.post("/v1/run", data=json.dumps(body), headers={**self.h(), "Content-Type": "application/json"})
        last = [json.loads(x) for x in (await r.text()).splitlines() if x.strip()][-1]
        self.assertEqual((last["type"], last["code"]), ("failed", "stale"))


if __name__ == "__main__":
    unittest.main()
