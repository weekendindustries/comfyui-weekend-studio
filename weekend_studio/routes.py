"""ComfyUI-side HTTP routes, for the Weekend Studio tab inside ComfyUI (round 4) and for testing a
workflow locally with curl. Mounted on ComfyUI's own server.

Every route checks who is asking (guard.py, 0.3.0): the X-Weekend-Studio: 1 header, a loopback
Host (or an address you named), and a same-origin Origin. ComfyUI's own same-origin middleware
alone let DNS rebinding, --enable-cors-header and --listen callers through (security review B1).

  GET    /weekend-studio/status                 connection state (+ the pairing code while pairing),
                                                the switches, this install's id
  POST   /weekend-studio/settings               {sameComputer?, autoSync?} → the switches
  POST   /weekend-studio/connect                {name?} → {code, approveUrl, expiresIn}; open approveUrl
  POST   /weekend-studio/disconnect             forget this machine, here and on the account
  GET    /weekend-studio/workflows              published workflows + their descriptions
  POST   /weekend-studio/workflows              {name, prompt (API format), marks?, id?} → publish/update
  GET    /weekend-studio/workflows/{id}
  DELETE /weekend-studio/workflows/{id}
  POST   /weekend-studio/test-run               {workflowId, version?, values, images: {key: base64}}
                                                → {outputs: [base64 PNG…], events: […]}

Nothing reaches the network until the user asks (security review B6): importing the pack only
registers these routes. The relay connection starts at Connect (and again at each start once
connected, until Disconnect); the same-computer server starts when its switch is turned on.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import os
import types
import uuid

from aiohttp import web

from .bridge import ApiError, Bridge
from .bridge_io import Api, Transport, service_config
from .comfy_client import ComfyClient
from .credentials import Credentials
from .guard import allowed_names, check, comfy_listen
from .home import Home, home_dir
from .local_server import DEFAULT_PORT, PORT_TRIES, LocalServer
from .description import description_problems, mark_refusals, output_problems
from .privacy import default_machine_name
from .runner import RunError, Runner
from .store import StoreError, WorkflowStore

VERSION = "0.3.3"

log = logging.getLogger("weekend_studio")


def old_store_root() -> str:
    """Where 0.2.x kept the token and the workflows: ComfyUI/user/default/weekend-studio/, which
    ComfyUI's userdata API serves. Only read to move things out of it (home.py migrate_from)."""
    import folder_paths  # type: ignore
    get_user = getattr(folder_paths, "get_user_directory", None)
    user_dir = get_user() if get_user else os.path.join(folder_paths.base_path, "user")
    return os.path.join(user_dir, "default", "weekend-studio")


def guarded(names: set):
    """Wrap a route handler: refused with 403 unless guard.check passes (before any body is read)."""
    def wrap(handler):
        async def run(request):
            why = check(request.headers, names)
            if why:
                log.warning("Weekend Studio: refused %s %s (%s)", request.method, request.path, why)
                return web.json_response({"error": "forbidden", "message": (
                    "Weekend Studio's settings answer only on the computer ComfyUI runs on (open ComfyUI at "
                    "http://127.0.0.1:<port> there), or at an address listed under allowedHosts in its settings.json."),
                    "reason": why}, status=403)
            return await handler(request)
        run.__name__ = getattr(handler, "__name__", "route")
        return run
    return wrap


def background(coro, loop, what: str):
    """Run `coro` on ComfyUI's loop. Its future would swallow an exception; this one says it in the log."""
    def done(fut):
        if not fut.cancelled() and fut.exception() is not None:
            log.error("Weekend Studio: %s stopped", what, exc_info=fut.exception())
    fut = asyncio.run_coroutine_threadsafe(coro, loop)
    fut.add_done_callback(done)
    return fut


def install(server, home_root: str | None = None):
    """Register the routes. Returns the pieces (tests stop the same-computer server through it).
    `home_root`: tests only (the pack's folder is otherwise home.home_dir's)."""
    import folder_paths  # type: ignore
    routes = server.routes
    home = Home(home_root or home_dir(folder_paths.base_path), folder_paths.base_path)
    try:
        moved = home.migrate_from(old_store_root())
        if moved["token"] or moved["workflows"]:
            log.info("Weekend Studio: moved %s%d workflow(s) out of ComfyUI's user folder into %s",
                     "the machine token and " if moved["token"] else "", moved["workflows"], home.root)
    except OSError:
        log.exception("Weekend Studio: couldn't move its files out of ComfyUI's user folder")
    store = WorkflowStore(home.workflows_dir)
    client = ComfyClient()
    runner = Runner(store, client, folder_paths.get_input_directory())
    config = service_config()
    creds = Credentials(home.machine_file)
    bridge = Bridge(store=store, runner=runner, comfy=client, api=Api(config), transport=Transport(),
                    credentials=creds, version=VERSION, machine_name=default_machine_name())
    names = allowed_names(comfy_listen(), home.allowed_hosts())
    guard = guarded(names)
    can_relay = bool(config["functionsUrl"] and config["publicAnonKey"])
    started = {"bridge": None}

    def start_bridge():
        """The relay loop: started by Connect, or at start-up when this ComfyUI was connected before
        (the user's own choice, kept until Disconnect). Never just because the pack was imported."""
        if started["bridge"] is None and can_relay:
            started["bridge"] = background(bridge.run_forever(), server.loop, "the connection to Weekend Studio")

    if creds.load():
        start_bridge()

    # Same-computer mode (local_server.py): off until the switch in the tab is turned on.
    local = LocalServer(store=store, runner=runner, comfy=client, version=VERSION,
                        machine_name=lambda: (creds.load() or {}).get("name") or default_machine_name(), config=config,
                        machine_id=lambda: (creds.load() or {}).get("machineId"))
    if home.settings()["sameComputer"]:
        background(local.start(home.local_port()), server.loop, "the same-computer server")

    def fail(status, code, message, details=None):
        return web.json_response({"error": code, "message": message, "details": details}, status=status)

    async def described(rec):
        return {"id": rec["id"], "name": rec["name"], "publishedAt": rec.get("published_at"),
                "updatedAt": rec.get("updated_at"), "description": store.describe(rec, await client.object_info())}

    def status_body():
        settings = home.settings()
        return {"version": VERSION, "workflows": len(store.list()), **bridge.status(),
                "local": {"enabled": settings["sameComputer"], "port": local.port},
                "settings": settings, "installId": home.install_id(), "defaultName": default_machine_name(),
                "canConnect": can_relay}

    @routes.get("/weekend-studio/status")
    @guard
    async def status(_request):
        return web.json_response(status_body())

    @routes.post("/weekend-studio/settings")
    @guard
    async def settings(request):
        try:
            body = await request.json()
        except ValueError:
            return fail(400, "bad-json", "not JSON")
        if not isinstance(body, dict):
            return fail(400, "bad-json", "not an object")
        now = home.update_settings({k: body[k] for k in ("sameComputer", "autoSync") if isinstance(body.get(k), bool)})
        if now["sameComputer"] and local.port is None:
            if await local.start(home.local_port()) is None:
                home.update_settings({"sameComputer": False})
                first = home.local_port() or DEFAULT_PORT
                return fail(409, "no-port", f"Ports {first}–{first + PORT_TRIES - 1} are all in use, so same-computer mode can't start.")
        elif not now["sameComputer"] and local.port is not None:
            await local.stop()
        return web.json_response(status_body())

    @routes.post("/weekend-studio/connect")
    @guard
    async def connect(request):
        try:
            body = await request.json()
        except ValueError:
            body = {}
        name = body.get("name") if isinstance(body, dict) and isinstance(body.get("name"), str) else None
        if not can_relay:
            return fail(503, "not-configured", "This copy of the node doesn't know where Weekend Studio is (service.json).")
        start_bridge()
        try:
            return web.json_response(await bridge.pair(name))
        except ApiError as e:
            return fail(e.status if 400 <= e.status < 600 else 502, "pair-failed", e.code)
        except Exception:
            return fail(502, "unreachable", "Can't reach Weekend Studio right now.")

    @routes.post("/weekend-studio/disconnect")
    @guard
    async def disconnect(_request):
        await bridge.disconnect()
        return web.json_response(bridge.status())

    @routes.get("/weekend-studio/workflows")
    @guard
    async def list_workflows(_request):
        await client.object_info(fresh=True)
        return web.json_response({"workflows": [await described(r) for r in store.list()]})

    @routes.get("/weekend-studio/workflows/{id}")
    @guard
    async def get_workflow(request):
        try:
            return web.json_response(await described(store.get(request.match_info["id"])))
        except StoreError as e:
            return fail(404 if e.code == "not-found" else 400, e.code, str(e))

    @routes.post("/weekend-studio/workflows")
    @guard
    async def publish(request):
        try:
            body = await request.json()
        except ValueError:
            return fail(400, "bad-json", "not JSON")
        if not isinstance(body, dict):
            return fail(400, "bad-json", "not an object")
        try:
            rec = store.publish(body.get("name"), body.get("prompt"), body.get("marks"), body.get("id"))
        except StoreError as e:
            return fail(404 if e.code == "not-found" else 400, e.code, str(e))
        await bridge.workflows_changed()
        out = await described(rec)
        refused = mark_refusals(rec["graph"], await client.object_info(), rec.get("marks"))
        return web.json_response({**out, "problems": output_problems(rec["graph"]) + refused + description_problems(out["description"])})

    # "Show in" (0.3.1): the Weekend Studio apps a shown workflow stays out of. Only that list changes.
    @routes.post("/weekend-studio/workflows/{id}/show-in")
    @guard
    async def show_in(request):
        try:
            body = await request.json()
        except ValueError:
            return fail(400, "bad-json", "not JSON")
        if not isinstance(body, dict):
            return fail(400, "bad-json", "not an object")
        try:
            rec = store.set_hidden_in(request.match_info["id"], body.get("hiddenIn"))
        except StoreError as e:
            return fail(404 if e.code == "not-found" else 400, e.code, str(e))
        await bridge.workflows_changed()
        return web.json_response(await described(rec))

    @routes.delete("/weekend-studio/workflows/{id}")
    @guard
    async def unpublish(request):
        try:
            store.remove(request.match_info["id"])
        except StoreError as e:
            return fail(404 if e.code == "not-found" else 400, e.code, str(e))
        await bridge.workflows_changed()
        return web.json_response({"ok": True})

    @routes.post("/weekend-studio/test-run")
    @guard
    async def test_run(request):
        try:
            body = await request.json()
        except ValueError:
            return fail(400, "bad-json", "not JSON")
        if not isinstance(body, dict):
            return fail(400, "bad-json", "not an object")
        images = body.get("images") or {}
        if not isinstance(images, dict):
            return fail(400, "invalid", "images must be {key: base64}")
        job = {"jobId": f"test-{uuid.uuid4().hex[:12]}", "workflowId": body.get("workflowId"),
               "version": body.get("version"), "values": body.get("values") or {}, "images": list(images)}
        if job["version"] is None:   # curl convenience: run whatever is published now
            try:
                job["version"] = store.describe(store.get(str(job["workflowId"])), await client.object_info())["version"]
            except StoreError as e:
                return fail(404, e.code, str(e))
        outputs, events = [], []

        async def fetch_input(key):
            try:
                return base64.b64decode(images[key], validate=True)
            except (binascii.Error, ValueError, TypeError):
                return b""

        async def put_output(_index, data, mime):
            outputs.append({"mime": mime, "base64": base64.b64encode(data).decode("ascii")})

        async def emit(ev):
            events.append(ev)

        try:
            await runner.run(job, fetch_input=fetch_input, put_output=put_output, emit=emit)
        except RunError as e:
            return fail(409 if e.code in ("stale", "busy") else 400, e.code, str(e), e.details)
        return web.json_response({"outputs": outputs, "events": events})

    return types.SimpleNamespace(home=home, store=store, bridge=bridge, local=local, start_bridge=start_bridge)
