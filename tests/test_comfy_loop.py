"""The ComfyUI client's run loop against a stand-in ComfyUI over real HTTP + WebSocket (aiohttp,
which ComfyUI ships; without it these skip). It pins the two 0.2.2 fixes that only show up on the
wire: a prompt dropped from the queue ends the run, and concurrent runs each keep their own events."""
import asyncio
import unittest

try:
    from aiohttp import web
    HAVE_AIOHTTP = True
except ImportError:   # pragma: no cover
    HAVE_AIOHTTP = False

import weekend_studio.comfy_client as cc
from weekend_studio.comfy_client import ComfyClient


class StandInComfy:
    """Just enough of ComfyUI's API: one socket per clientId (a second one with the same id replaces
    the first, as ComfyUI does), a queue, a history, /interrupt."""

    def __init__(self):
        self.sockets, self.pending, self.running, self.history = {}, [], [], {}
        self.n = 0
        self.app = web.Application()
        self.app.add_routes([web.get("/ws", self.ws), web.post("/prompt", self.prompt), web.get("/queue", self.queue),
                             web.post("/queue", self.queue_post), web.get("/history/{pid}", self.hist),
                             web.post("/interrupt", self.interrupt)])

    async def ws(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        cid = request.query["clientId"]
        old = self.sockets.pop(cid, None)
        if old is not None:
            await old.close()
        self.sockets[cid] = ws
        async for _ in ws:
            pass
        if self.sockets.get(cid) is ws:
            del self.sockets[cid]
        return ws

    async def prompt(self, request):
        body = await request.json()
        self.n += 1
        pid = f"p{self.n}"
        self.pending.append([self.n, pid, body["prompt"], {"client_id": body["client_id"]}, []])
        return web.json_response({"prompt_id": pid, "number": self.n})

    async def queue(self, _r):
        return web.json_response({"queue_running": self.running, "queue_pending": self.pending})

    async def queue_post(self, request):
        drop = set((await request.json()).get("delete") or [])
        self.pending = [p for p in self.pending if p[1] not in drop]
        return web.json_response({})

    async def hist(self, request):
        pid = request.match_info["pid"]
        return web.json_response({pid: self.history[pid]} if pid in self.history else {})

    async def interrupt(self, _r):
        return web.json_response({})

    async def send(self, pid, event, data):
        item = next(p for p in self.pending + self.running if p[1] == pid)
        ws = self.sockets.get(item[3]["client_id"])
        if ws is not None:
            await ws.send_json({"type": event, "data": {"prompt_id": pid, **data}})


@unittest.skipUnless(HAVE_AIOHTTP, "aiohttp not installed (ComfyUI ships it)")
class RunLoop(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.poll, cc.HISTORY_POLL = cc.HISTORY_POLL, 0.1
        self.comfy = StandInComfy()
        self.runner = web.AppRunner(self.comfy.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.client = ComfyClient(f"http://127.0.0.1:{port}")

    async def asyncTearDown(self):
        cc.HISTORY_POLL = self.poll
        await self.client.close()
        await self.runner.cleanup()

    async def collect(self, gen, into):
        async for ev in gen:
            into.append(ev)

    async def test_a_prompt_dropped_from_the_queue_ends_the_run(self):
        events = []
        task = asyncio.ensure_future(self.collect(self.client.run({"1": {}}), events))
        while not events:
            await asyncio.sleep(0.01)
        self.assertEqual(events[0]["type"], "queued")
        await self.client.cancel(events[0]["prompt_id"])     # ComfyUI sends nothing for this
        await asyncio.wait_for(task, 2)                       # it used to wait for two hours
        self.assertEqual(events[-1], {"type": "interrupted"})

    async def test_clear_queue_in_comfyui_ends_the_run_too(self):
        events = []
        task = asyncio.ensure_future(self.collect(self.client.run({"1": {}}), events))
        while not events:
            await asyncio.sleep(0.01)
        self.comfy.pending.clear()
        await asyncio.wait_for(task, 2)
        self.assertEqual(events[-1], {"type": "interrupted"})

    async def test_concurrent_runs_each_hear_their_own_events(self):
        a, b = [], []
        ta = asyncio.ensure_future(self.collect(self.client.run({"1": {}}), a))
        tb = asyncio.ensure_future(self.collect(self.client.run({"2": {}}), b))
        while not (a and b):
            await asyncio.sleep(0.01)
        pa, pb = a[0]["prompt_id"], b[0]["prompt_id"]
        for pid in (pa, pb):
            await self.comfy.send(pid, "progress", {"value": 1, "max": 2, "node": "3"})
        for pid in (pa, pb):
            await self.comfy.send(pid, "execution_success", {})
        await asyncio.wait_for(asyncio.gather(ta, tb), 2)
        self.assertEqual([e["type"] for e in a], ["queued", "progress", "done"], "the first run kept its socket")
        self.assertEqual([e["type"] for e in b], ["queued", "progress", "done"])

    async def test_a_finished_run_is_found_in_history_without_its_event(self):
        events = []
        task = asyncio.ensure_future(self.collect(self.client.run({"1": {}}), events))
        while not events:
            await asyncio.sleep(0.01)
        pid = events[0]["prompt_id"]
        self.comfy.pending.clear()
        self.comfy.history[pid] = {"status": {"completed": True}, "outputs": {"9": {"images": []}}}
        await asyncio.wait_for(task, 2)
        self.assertEqual(events[-1], {"type": "done"})


if __name__ == "__main__":
    unittest.main()
