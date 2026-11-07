"""routes.install against a stand-in ComfyUI server (0.3.0, security review B1 + B6): importing starts
nothing, every route is guarded, the switches start and stop same-computer mode, 0.2.x files move.

Needs aiohttp (ComfyUI ships it); without it this skips."""
import asyncio
import json
import os
import socket
import sys
import tempfile
import types
import unittest

try:
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    HAVE_AIOHTTP = True
except ImportError:
    HAVE_AIOHTTP = False

from tests.helpers import fx

TAB = {"X-Weekend-Studio": "1"}


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@unittest.skipUnless(HAVE_AIOHTTP, "aiohttp not installed (ComfyUI ships it)")
class Install(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = os.path.join(self.tmp.name, "ComfyUI")
        self.old = os.path.join(base, "user", "default", "weekend-studio")
        os.makedirs(os.path.join(base, "input"))
        fp = types.ModuleType("folder_paths")
        fp.base_path = base
        fp.get_user_directory = lambda: os.path.join(base, "user")
        fp.get_input_directory = lambda: os.path.join(base, "input")
        self._saved = {k: sys.modules.get(k) for k in ("folder_paths",)}
        sys.modules["folder_paths"] = fp
        # The pack's folder and its same-computer port, the way a person would set the port: settings.json.
        self.home_root = os.path.join(self.tmp.name, "home")
        self.port = free_port()
        from weekend_studio.home import write_private_json
        write_private_json(os.path.join(self.home_root, "settings.json"), {"localPort": self.port})

        from weekend_studio import bridge, routes
        self.routes = routes
        self.started = []
        self._run_forever = bridge.Bridge.run_forever

        async def fake_run_forever(b):
            self.started.append(b)
        bridge.Bridge.run_forever = fake_run_forever
        self.servers = []

    async def asyncTearDown(self):
        from weekend_studio import bridge
        bridge.Bridge.run_forever = self._run_forever
        if getattr(self, "pack", None):
            await self.pack.local.stop()
        for c in self.servers:
            await c.close()
        for k, v in self._saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
        self.tmp.cleanup()

    async def boot(self):
        server = types.SimpleNamespace(routes=web.RouteTableDef(), loop=asyncio.get_running_loop())
        self.pack = self.routes.install(server, home_root=self.home_root)
        app = web.Application()
        app.add_routes(server.routes)
        client = TestClient(TestServer(app))
        await client.start_server()
        self.servers.append(client)
        await asyncio.sleep(0.05)   # let anything install() scheduled run
        return client

    def local_listening(self):
        with socket.socket() as s:
            return s.connect_ex(("127.0.0.1", self.port)) == 0

    async def test_a_fresh_install_starts_nothing(self):
        client = await self.boot()
        self.assertEqual(self.started, [], "no relay connection at import")
        self.assertFalse(self.local_listening(), "no second server at import")
        r = await client.get("/weekend-studio/status", headers={**TAB, "Host": f"127.0.0.1:{client.port}"})
        body = await r.json()
        self.assertEqual(r.status, 200, body)
        self.assertEqual(body["settings"], {"sameComputer": False, "autoSync": False})
        self.assertEqual(body["local"], {"enabled": False, "port": None})
        self.assertRegex(body["installId"], r"^[0-9a-f]{16}$")
        self.assertNotIn(socket.gethostname(), json.dumps(body), "the hostname isn't offered as the name")

    async def test_every_route_is_guarded(self):
        client = await self.boot()
        host = {"Host": f"127.0.0.1:{client.port}"}
        with self.assertLogs("weekend_studio", "WARNING"):   # each refusal says why in ComfyUI's log
            await self._refusals(client, host)

    async def _refusals(self, client, host):
        for method, path in (("GET", "/weekend-studio/status"), ("POST", "/weekend-studio/connect"),
                             ("POST", "/weekend-studio/disconnect"), ("GET", "/weekend-studio/workflows"),
                             ("POST", "/weekend-studio/workflows"), ("DELETE", "/weekend-studio/workflows/x"),
                             ("POST", "/weekend-studio/test-run"), ("POST", "/weekend-studio/settings"),
                             ("POST", "/weekend-studio/workflows/x/show-in")):
            r = await client.request(method, path, headers=host, data="{}")
            self.assertEqual(r.status, 403, f"{method} {path} without the header")
            r = await client.request(method, path, headers={**TAB, "Host": f"rebind.evil.example:{client.port}"}, data="{}")
            self.assertEqual(r.status, 403, f"{method} {path} from a rebinding name")
            r = await client.request(method, path, headers={**TAB, **host, "Origin": "https://evil.example"}, data="{}")
            self.assertEqual(r.status, 403, f"{method} {path} from another site")

    async def test_same_computer_mode_is_a_switch(self):
        client = await self.boot()
        tab = {**TAB, "Host": f"127.0.0.1:{client.port}", "Content-Type": "application/json"}
        r = await client.post("/weekend-studio/settings", headers=tab, data=json.dumps({"sameComputer": True}))
        body = await r.json()
        self.assertEqual((r.status, body["local"]), (200, {"enabled": True, "port": self.port}), body)
        self.assertTrue(self.local_listening())
        r = await client.post("/weekend-studio/settings", headers=tab, data=json.dumps({"sameComputer": False}))
        self.assertEqual((await r.json())["local"], {"enabled": False, "port": None})
        self.assertFalse(self.local_listening(), "off means the port is closed")

    async def test_switched_on_before_starts_with_comfyui(self):
        from weekend_studio.home import Home
        Home(self.home_root).update_settings({"sameComputer": True})
        await self.boot()
        for _ in range(40):
            if self.local_listening():
                break
            await asyncio.sleep(0.05)
        self.assertTrue(self.local_listening())

    async def test_0_2_files_move_and_a_connected_machine_reconnects(self):
        from weekend_studio.store import WorkflowStore
        os.makedirs(self.old)
        rec = WorkflowStore(self.old).publish("Portrait", fx("txt2img.api.json"))
        with open(os.path.join(self.old, ".machine.json"), "w") as f:
            json.dump({"machineToken": "wsm_" + "a" * 43, "machineId": "m1", "name": "PC"}, f)
        await self.boot()
        self.assertFalse(os.path.exists(self.old), "the served folder is emptied and gone")
        self.assertEqual(len(self.started), 1, "connected before = the user's choice: it reconnects")
        self.assertEqual(self.pack.store.get(rec["id"])["name"], "Portrait")
        self.assertEqual(self.pack.bridge.credentials.load()["machineId"], "m1")


if __name__ == "__main__":
    unittest.main()
