"""The bridge's logic against fakes: pairing, connecting, running a relayed job, re-auth, removal."""
import asyncio
import json
import os
import tempfile
import unittest

from weekend_studio.bridge import ApiError, Bridge
from weekend_studio.credentials import Credentials
from weekend_studio.runner import RunError
from weekend_studio.store import WorkflowStore
from tests.helpers import fx

OBJECT_INFO = fx("object_info.json")
TOKEN = "wsm_" + "a" * 43


class FakeApi:
    def __init__(self):
        self.redeems = [{"status": "pending"}, {"status": "approved", "machineToken": TOKEN, "machineId": "m1", "name": "Studio PC"}]
        self.token_error = None
        self.forgotten = []
        self.tokens = 0

    async def pair_start(self, name):
        self.started = name
        return {"pairingId": "p1", "pollSecret": "s", "code": "ABCD-EFGH", "approveUrl": "https://app/#comfy-connect/ABCD-EFGH",
                "expiresIn": 600, "interval": 1}

    async def pair_redeem(self, pid, secret):
        return self.redeems.pop(0) if self.redeems else {"status": "pending"}

    async def machine_token(self, t):
        if self.token_error:
            raise self.token_error
        self.tokens += 1
        return {"token": f"hub-token-{self.tokens}", "hubUrl": "wss://hub.test"}

    async def forget(self, t):
        self.forgotten.append(t)


class FakeConn:
    def __init__(self):
        self.incoming = asyncio.Queue()
        self.sent = []
        self.closed = None

    async def send(self, text):
        self.sent.append(json.loads(text))

    async def receive(self):
        return await self.incoming.get()

    async def close(self, code=1000):
        self.closed = code
        await self.incoming.put(("close", code))

    def push(self, msg):
        self.incoming.put_nowait(("text", json.dumps(msg)))

    def of(self, t):
        return [m for m in self.sent if m.get("t") == t]


class FakeTransport:
    def __init__(self):
        self.conns = []
        self.gets = []
        self.puts = []

    async def connect(self, url, token):
        self.url, self.token = url, token
        conn = FakeConn()
        self.conns.append(conn)
        return conn

    async def get_bytes(self, url, ticket):
        self.gets.append((url, ticket))
        return b"\x89PNG"

    async def put_bytes(self, url, ticket, data, mime):
        self.puts.append((url, ticket, data, mime))


class FakeRunner:
    def __init__(self, fail=None):
        self.fail = fail
        self.canceled = []

    async def run(self, job, *, fetch_input, put_output, emit):
        for key in job.get("images", []):
            await fetch_input(key)
        await emit({"type": "queued", "position": 0})
        await emit({"type": "executing", "node": "3"})
        for i in range(10):
            await emit({"type": "progress", "value": i, "max": 10})
        if self.fail:
            raise self.fail
        await put_output(0, b"\x89PNG-out", "image/png")
        await emit({"type": "done", "outputs": 1})
        return {"outputs": 1}

    async def cancel(self, job_id):
        if job_id == "boom":
            raise RuntimeError("boom")
        self.canceled.append(job_id)
        return True


class FakeComfy:
    async def object_info(self, fresh=False):
        return OBJECT_INFO


async def until(pred, timeout=2.0):
    for _ in range(int(timeout / 0.005)):
        if pred():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition never became true")


class BridgeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = WorkflowStore(os.path.join(self.tmp.name, "store"))
        self.wf = self.store.publish("Portrait", fx("txt2img.api.json"))["id"]
        self.creds = Credentials(os.path.join(self.tmp.name, "store", ".machine.json"))
        self.api, self.transport, self.runner = FakeApi(), FakeTransport(), FakeRunner()
        self.bridge = self.make()
        self.task = None

    def make(self):
        clock = [0.0]

        def tick():
            clock[0] += 0.05   # every read moves time on, so throttles let some events through
            return clock[0]
        return Bridge(store=self.store, runner=self.runner, comfy=FakeComfy(), api=self.api, transport=self.transport,
                      credentials=self.creds, version="0.1.0", machine_name="Studio PC", clock=tick, time_scale=0.001)

    async def asyncTearDown(self):
        self.bridge.stop()
        if self.task:
            for c in self.transport.conns:
                await c.close()
            await asyncio.wait_for(self.task, 2)
        self.tmp.cleanup()

    def start(self):
        self.task = asyncio.ensure_future(self.bridge.run_forever())

    async def connected(self):
        self.creds.save({"machineToken": TOKEN, "machineId": "m1", "name": "Studio PC"})
        self.start()
        await until(lambda: self.transport.conns and self.transport.conns[-1].of("hello"))
        return self.transport.conns[-1]

    async def test_pairing_then_connecting(self):
        self.start()
        pairing = await self.bridge.pair()
        self.assertEqual(pairing["code"], "ABCD-EFGH")
        self.assertEqual(self.bridge.status()["state"], "pairing")
        await until(lambda: self.transport.conns and self.transport.conns[0].of("hello"))
        self.assertEqual(self.creds.load()["machineToken"], TOKEN, "the token is kept")
        self.assertEqual(self.transport.url, "wss://hub.test/hub")
        hello = self.transport.conns[0].of("hello")[0]
        self.assertEqual([w["id"] for w in hello["workflows"]], [self.wf])
        self.assertEqual(self.bridge.status()["state"], "connected")

    async def test_a_relayed_job(self):
        conn = await self.connected()
        conn.push({"t": "run", "job": {"jobId": "j1", "workflowId": self.wf, "version": "v", "values": {}, "images": ["12.weekend_ref"]},
                   "inputs": {"12.weekend_ref": {"path": "/blob/j1/in-0", "ticket": "get-t"}},
                   "output": {"prefix": "/blob/j1/out-", "ticket": "put-t"}})
        await until(lambda: conn.of("done"))
        self.assertEqual(self.transport.gets, [("https://hub.test/blob/j1/in-0", "get-t")])
        self.assertEqual(self.transport.puts[0][:2], ("https://hub.test/blob/j1/out-0", "put-t"))
        self.assertEqual(conn.of("done")[0], {"t": "done", "jobId": "j1", "outputs": [{"name": "out-0", "mime": "image/png", "size": len(b"\x89PNG-out")}]})
        progress = [m for m in conn.of("event") if m["ev"]["type"] == "progress"]
        self.assertLess(len(progress), 10, "progress is throttled")
        self.assertNotIn("done", [m["ev"]["type"] for m in conn.of("event")], "the relay announces done itself")
        self.assertLessEqual(len(progress), 1, "over the relay, progress at most every 12 s (the fake clock moves 0.05 s a read)")
        self.assertNotIn("executing", [m["ev"]["type"] for m in conn.of("event")], "no per-node chatter over the relay")
        self.assertEqual(conn.of("status")[-1], {"t": "status", "busy": False, "queue": 0})

    async def test_a_failed_run_says_why(self):
        self.runner.fail = RunError("invalid", "some settings aren't valid", [{"key": "3.steps", "code": "range"}])
        conn = await self.connected()
        conn.push({"t": "run", "job": {"jobId": "j2"}, "inputs": {}, "output": {"prefix": "/blob/j2/out-", "ticket": "t"}})
        await until(lambda: conn.of("failed"))
        self.assertEqual(conn.of("failed")[0], {"t": "failed", "jobId": "j2", "code": "invalid", "message": "some settings aren't valid",
                                                "details": [{"key": "3.steps", "code": "range"}]})

    async def test_files_only_from_the_relay(self):
        conn = await self.connected()
        conn.push({"t": "run", "job": {"jobId": "j3", "images": ["k"]}, "inputs": {"k": {"path": "https://evil.test/x", "ticket": "t"}},
                   "output": {"prefix": "/blob/j3/out-", "ticket": "t"}})
        await until(lambda: conn.of("failed"))
        self.assertEqual(conn.of("failed")[0]["code"], "invalid")
        self.assertEqual(self.transport.gets, [])

    async def test_cancel_reaches_the_runner(self):
        conn = await self.connected()
        conn.push({"t": "cancel", "jobId": "j9"})
        await until(lambda: self.runner.canceled)
        self.assertEqual(self.runner.canceled, ["j9"])

    async def test_reauth_on_the_open_socket(self):
        conn = await self.connected()
        conn.push({"t": "reauth"})
        await until(lambda: conn.of("reauth"))
        self.assertEqual(conn.of("reauth")[0]["token"], "hub-token-2")

    async def test_removed_while_connected(self):
        conn = await self.connected()
        await conn.close(4403)
        await until(lambda: self.creds.load() is None)
        await until(lambda: self.bridge.status()["state"] == "disconnected")

    async def test_removed_while_away(self):
        self.api.token_error = ApiError(401, "machine_unknown")
        self.creds.save({"machineToken": TOKEN, "machineId": "m1", "name": "Studio PC"})
        self.start()
        await until(lambda: self.creds.load() is None)
        self.assertEqual(self.transport.conns, [])

    async def test_lapsed_membership_waits(self):
        self.api.token_error = ApiError(403, "membership_required")
        self.creds.save({"machineToken": TOKEN, "machineId": "m1", "name": "Studio PC"})
        self.start()
        await until(lambda: self.bridge.status()["state"] == "refused")
        self.assertIn("membership", self.bridge.status()["detail"])
        self.assertIsNotNone(self.creds.load(), "kept: renewing brings the machine back")

    async def test_disconnect_forgets_everywhere(self):
        await self.connected()
        await self.bridge.disconnect()
        self.assertEqual(self.api.forgotten, [TOKEN])
        self.assertIsNone(self.creds.load())

    async def test_reconnects_after_a_drop(self):
        conn = await self.connected()
        await conn.close(1006)
        await until(lambda: len(self.transport.conns) == 2 and self.transport.conns[1].of("hello"))

    async def test_credentials_file_is_private(self):
        self.creds.save({"machineToken": TOKEN})
        self.assertEqual(os.stat(self.creds.path).st_mode & 0o777, 0o600)

    # ── 0.2.2: nothing stops the connection for good ──────────────────────────────────────────
    async def test_an_odd_relay_message_is_skipped(self):
        conn = await self.connected()
        conn.push({"t": "run", "job": "x"})                       # used to raise AttributeError
        conn.push({"t": "run", "job": {"jobId": "j5"}, "inputs": "nope", "output": {}})
        conn.push({"t": "cancel", "jobId": "boom"})               # the runner raises on this one
        conn.push({"t": "cancel", "jobId": "j7"})
        with self.assertLogs("weekend_studio", "ERROR"):
            await until(lambda: self.runner.canceled)
        self.assertEqual(self.runner.canceled, ["j7"], "the session still reads messages")
        self.assertEqual(self.bridge.status()["state"], "connected")
        self.assertEqual(conn.of("failed"), [], "nothing ran for the malformed ones")

    async def test_comfyui_not_answering_at_hello_is_retried(self):
        calls = {"n": 0}

        class SlowStart(FakeComfy):
            async def object_info(self, fresh=False):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise ConnectionRefusedError("ComfyUI isn't listening yet")
                return OBJECT_INFO
        self.bridge.comfy = SlowStart()
        with self.assertLogs("weekend_studio", "ERROR") as logged:
            conn = await self.connected()
        self.assertIn("the connection failed", logged.output[0], "it says so in ComfyUI's log")
        self.assertEqual(len(self.transport.conns), 2, "the first socket was closed and a second one said hello")
        self.assertEqual(self.transport.conns[0].closed, 1011)
        self.assertEqual(conn.of("hello")[0]["workflows"][0]["id"], self.wf)

    async def test_quick_closes_climb_the_backoff(self):
        naps = []
        nap = self.bridge._nap

        async def record(seconds):
            naps.append(seconds)
            await nap(seconds)
        self.bridge._nap = record
        await self.connected()
        for _ in range(3):
            n = len(self.transport.conns)
            await self.transport.conns[-1].close(1006)
            await until(lambda: len(self.transport.conns) == n + 1 and self.transport.conns[-1].of("hello"))
        self.assertEqual(naps[:3], [1, 2, 5], "a session that didn't last doesn't start the backoff over")

    async def test_a_refused_message_waits_minutes(self):
        naps = []
        nap = self.bridge._nap

        async def record(seconds):
            naps.append(seconds)
            await nap(seconds)
        conn = await self.connected()
        self.bridge._nap = record
        await conn.close(1008)
        await until(lambda: naps)
        self.assertEqual(naps[0], 300)
        self.assertIn("refused", self.bridge.status()["detail"])

    def test_the_workflow_list_is_trimmed_to_fit(self):
        import weekend_studio.bridge as b
        limit, b.HELLO_LIMIT = b.HELLO_LIMIT, 5000
        try:
            small = [{"id": f"w{i}", "pad": "x" * 100} for i in range(5)]
            big = {"id": "loras", "pad": "x" * 6000}
            kept = self.bridge._fit(small[:2] + [big] + small[2:])
            self.assertEqual([w["id"] for w in kept], [w["id"] for w in small], "the oversized one is left out, order kept")
            self.assertIn("1 shown workflow is too large", self.bridge.detail)
            self.assertEqual(self.bridge._fit(small), small)
        finally:
            b.HELLO_LIMIT = limit

    async def test_an_unusable_approval_keeps_polling(self):
        self.api.redeems = [{"status": "approved", "machineToken": None}, {"status": "approved", "machineToken": TOKEN, "machineId": "m1"}]
        self.start()
        await self.bridge.pair()
        await until(lambda: self.creds.load() is not None)
        self.assertEqual(self.creds.load()["name"], "Studio PC", "a missing name falls back to the machine's")

    # ── 0.3.0: which account, and what leaves the computer ────────────────────────────────────
    async def test_the_tab_can_say_which_account(self):
        self.api.redeems = [{"status": "approved", "machineToken": TOKEN, "machineId": "m1", "name": "Studio PC", "handle": "@sam"}]
        self.start()
        await self.bridge.pair("  My   PC  ")
        self.assertEqual(self.api.started, "My   PC")
        await until(lambda: self.creds.load() is not None)
        self.assertEqual(self.bridge.status()["account"], {"handle": "sam"})

    async def test_comfy_token_renames_and_names_the_account(self):
        tokens = self.api.machine_token

        async def named(t):
            return {**(await tokens(t)), "name": "Renamed PC", "handle": "sam"}
        self.api.machine_token = named
        await self.connected()
        self.assertEqual((self.creds.load()["name"], self.creds.load()["handle"]), ("Renamed PC", "sam"))
        self.assertEqual(self.bridge.status()["machineName"], "Renamed PC")

    async def test_a_bad_handle_is_dropped(self):
        self.api.redeems = [{"status": "approved", "machineToken": TOKEN, "machineId": "m1", "handle": "<script>"}]
        self.start()
        await self.bridge.pair()
        await until(lambda: self.creds.load() is not None)
        self.assertEqual(self.bridge.status()["account"], {"handle": None})

    async def test_errors_leave_without_home_paths(self):
        self.runner.fail = RunError("comfy", "No such file: /Users/firstname/ComfyUI/models/x.safetensors",
                                    {"node": "4", "path": "/home/firstname/x"})
        conn = await self.connected()
        conn.push({"t": "run", "job": {"jobId": "j8"}, "inputs": {}, "output": {"prefix": "/blob/j8/out-", "ticket": "t"}})
        await until(lambda: conn.of("failed"))
        failed = conn.of("failed")[0]
        self.assertEqual(failed["message"], "No such file: ~/ComfyUI/models/x.safetensors")
        self.assertEqual(failed["details"], {"node": "4", "path": "~/x"})

if __name__ == "__main__":
    unittest.main()
