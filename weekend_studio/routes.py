"""ComfyUI-side HTTP routes, for the Weekend Studio tab inside ComfyUI (round 4) and for testing a
workflow locally with curl. Mounted on ComfyUI's own server, so ComfyUI's same-origin guard covers
them: another website can't publish into, or run on, someone's ComfyUI through these.

  GET    /weekend-studio/status                 connection state (+ the pairing code while pairing)
  POST   /weekend-studio/connect                {name?} → {code, approveUrl, expiresIn}; open approveUrl
  POST   /weekend-studio/disconnect             forget this machine, here and on the account
  GET    /weekend-studio/workflows              published workflows + their descriptions
  POST   /weekend-studio/workflows              {name, prompt (API format), marks?, id?} → publish/update
  GET    /weekend-studio/workflows/{id}
  DELETE /weekend-studio/workflows/{id}
  POST   /weekend-studio/test-run               {workflowId, version?, values, images: {key: base64}}
                                                → {outputs: [base64 PNG…], events: […]}
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import os
import socket
import uuid

from aiohttp import web

from .bridge import ApiError, Bridge
from .bridge_io import Api, Transport, service_config
from .comfy_client import ComfyClient
from .credentials import Credentials
from .runner import RunError, Runner
from .store import StoreError, WorkflowStore

VERSION = "0.1.0"


def store_root() -> str:
    import folder_paths  # type: ignore
    get_user = getattr(folder_paths, "get_user_directory", None)
    user_dir = get_user() if get_user else os.path.join(folder_paths.base_path, "user")
    return os.path.join(user_dir, "default", "weekend-studio")


def machine_name() -> str:
    """A first name for this machine (the member can rename it in Weekend Studio)."""
    host = socket.gethostname().split(".")[0].replace("-", " ").strip()
    return (host or "ComfyUI")[:60]


def install(server) -> None:
    import folder_paths  # type: ignore
    routes = server.routes
    root = store_root()
    store = WorkflowStore(root)
    client = ComfyClient()
    runner = Runner(store, client, folder_paths.get_input_directory())
    config = service_config()
    bridge = Bridge(store=store, runner=runner, comfy=client, api=Api(config), transport=Transport(),
                    credentials=Credentials(os.path.join(root, ".machine.json")), version=VERSION,
                    machine_name=machine_name())
    # Starts with ComfyUI's own loop. Not connected yet = it waits quietly for Connect.
    if config["functionsUrl"] and config["apiKey"]:
        asyncio.run_coroutine_threadsafe(bridge.run_forever(), server.loop)

    def fail(status, code, message, details=None):
        return web.json_response({"error": code, "message": message, "details": details}, status=status)

    async def described(rec):
        return {"id": rec["id"], "name": rec["name"], "publishedAt": rec.get("published_at"),
                "updatedAt": rec.get("updated_at"), "description": store.describe(rec, await client.object_info())}

    @routes.get("/weekend-studio/status")
    async def status(_request):
        return web.json_response({"version": VERSION, "workflows": len(store.list()), **bridge.status()})

    @routes.post("/weekend-studio/connect")
    async def connect(request):
        try:
            body = await request.json()
        except ValueError:
            body = {}
        name = body.get("name") if isinstance(body, dict) and isinstance(body.get("name"), str) else None
        try:
            return web.json_response(await bridge.pair(name))
        except ApiError as e:
            return fail(e.status if 400 <= e.status < 600 else 502, "pair-failed", e.code)
        except Exception:
            return fail(502, "unreachable", "Can't reach Weekend Studio right now.")

    @routes.post("/weekend-studio/disconnect")
    async def disconnect(_request):
        await bridge.disconnect()
        return web.json_response(bridge.status())

    @routes.get("/weekend-studio/workflows")
    async def list_workflows(_request):
        await client.object_info(fresh=True)
        return web.json_response({"workflows": [await described(r) for r in store.list()]})

    @routes.get("/weekend-studio/workflows/{id}")
    async def get_workflow(request):
        try:
            return web.json_response(await described(store.get(request.match_info["id"])))
        except StoreError as e:
            return fail(404 if e.code == "not-found" else 400, e.code, str(e))

    @routes.post("/weekend-studio/workflows")
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
        return web.json_response(await described(rec))

    @routes.delete("/weekend-studio/workflows/{id}")
    async def unpublish(request):
        try:
            store.remove(request.match_info["id"])
        except StoreError as e:
            return fail(404 if e.code == "not-found" else 400, e.code, str(e))
        await bridge.workflows_changed()
        return web.json_response({"ok": True})

    @routes.post("/weekend-studio/test-run")
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
