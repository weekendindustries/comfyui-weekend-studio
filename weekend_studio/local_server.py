"""Same-computer mode: Weekend Studio in a browser on THIS computer talks straight to this ComfyUI.

No account, no relay, no pairing — the signed-out path (reviews/comfyui-plan-2026-10-14.md, round 6).
ComfyUI's own server refuses cross-origin requests unless it's started with a flag, so the pack runs
a small server of its own on 127.0.0.1 (default port 8189) that answers Weekend Studio's sites only.

  GET  /v1/hello    → { app, version, machineName, machineId, workflows: [descriptions] }
                    machineId: this ComfyUI's id on the person's account when it's connected (else
                    null), so a signed-in browser can tell "the machine on my account" is right here
                    and skip the relay for it.
  POST /v1/run      { jobId, workflowId, version, values, images: { key: base64 } }
                    → NDJSON: one event per line ({type: queued|progress|executing}), then
                      { type: "done", outputs: [{ mime, base64 }] } or { type: "failed", code, message, details }
  POST /v1/cancel   { jobId }

Who may call it — every request, not just the browser's preflight:
  · Origin must be one of Weekend Studio's sites (service.json `localOrigins`, plus
    WEEKEND_STUDIO_LOCAL_ORIGINS for development). A page elsewhere can't even make it run a job:
    a POST without our Origin is refused before anything is read, and JSON bodies need a preflight.
  · Host must be 127.0.0.1/localhost at our port — a site that points its own name at 127.0.0.1
    (DNS rebinding) is refused.
  · Chrome's local-network check (Access-Control-Request-Private-Network) is answered only for the
    allowed origins.
The same rules as the relay path apply to what runs: the browser sends a workflow id and values,
never a graph, and the runner checks every value (runner.py).
"""
from __future__ import annotations

import base64
import binascii
import json
import os
import re

DEFAULT_PORT = 8189
PORT_TRIES = 5                       # 8189…8193 if something else holds the first
MAX_BODY = 160 * 1024 * 1024         # eight 40 MB pictures as base64, with room to spare
DEFAULT_ORIGINS = ("https://weekendindustries.app", "https://studio.weekendindustries.dev")
JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


# ── Who may call (pure; tests/test_local_server.py) ──────────────────────────────────────────

def allowed_origins(config: dict | None = None, env: dict | None = None) -> set:
    env = os.environ if env is None else env
    origins = set((config or {}).get("localOrigins") or DEFAULT_ORIGINS)
    extra = env.get("WEEKEND_STUDIO_LOCAL_ORIGINS", "")
    origins.update(o.strip().rstrip("/") for o in extra.split(",") if o.strip())
    return {o.rstrip("/") for o in origins}


def origin_ok(origin: str | None, origins: set) -> bool:
    return bool(origin) and origin.rstrip("/") in origins


def host_ok(host: str | None, port: int) -> bool:
    """127.0.0.1:port or localhost:port — nothing else (a rebinding name is refused)."""
    if not host:
        return False
    return host.lower() in (f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}")


def cors_headers(origin: str, private_network: bool = False) -> dict:
    h = {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "content-type",
        "Access-Control-Max-Age": "600",
        "Vary": "Origin",
    }
    if private_network:
        h["Access-Control-Allow-Private-Network"] = "true"
    return h


def parse_run(body) -> tuple[dict, dict]:
    """The run request → (job for runner.run, {key: bytes}). Raises ValueError with a sentence."""
    if not isinstance(body, dict):
        raise ValueError("not an object")
    job_id = body.get("jobId")
    if not isinstance(job_id, str) or not JOB_ID_RE.match(job_id):
        raise ValueError("bad job id")
    images = body.get("images") or {}
    if not isinstance(images, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in images.items()):
        raise ValueError("images must be {key: base64}")
    decoded = {}
    for key, b64 in images.items():
        try:
            decoded[key] = base64.b64decode(b64, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError(f"an image isn't base64 ({key})") from None
    job = {"jobId": job_id, "workflowId": body.get("workflowId"), "version": body.get("version"),
           "values": body.get("values") if isinstance(body.get("values"), dict) else {}, "images": list(decoded)}
    return job, decoded


# ── The server ───────────────────────────────────────────────────────────────────────────────

class LocalServer:
    def __init__(self, *, store, runner, comfy, version: str, machine_name: str, config: dict | None = None,
                 machine_id=lambda: None):
        self.store = store
        self.runner = runner
        self.comfy = comfy
        self.version = version
        self.machine_name = machine_name
        self.machine_id = machine_id
        self.origins = allowed_origins(config)
        self.port = None
        self._runner = None

    def app(self):
        from aiohttp import web

        @web.middleware
        async def guard(request, handler):
            # Every request: our host, our origins. Refused before any body is read.
            ok, origin = self._guard_ok(request)
            if not ok:
                return web.json_response({"error": "forbidden"}, status=403)
            resp = await handler(request)
            if not getattr(resp, "prepared", False):
                for k, v in cors_headers(origin).items():
                    resp.headers.setdefault(k, v)
            return resp

        app = web.Application(client_max_size=MAX_BODY, middlewares=[guard])
        app.router.add_route("OPTIONS", "/{tail:.*}", self._preflight)
        app.router.add_get("/v1/hello", self._hello)
        app.router.add_post("/v1/run", self._run)
        app.router.add_post("/v1/cancel", self._cancel)
        return app

    async def start(self, port: int | None = None) -> int | None:
        """Bind 127.0.0.1 on the first free port from `port`; returns the port (None if none)."""
        from aiohttp import web
        first = port or int(os.environ.get("WEEKEND_STUDIO_LOCAL_PORT") or DEFAULT_PORT)
        self._runner = web.AppRunner(self.app(), access_log=None)
        await self._runner.setup()
        for p in range(first, first + PORT_TRIES):
            try:
                site = web.TCPSite(self._runner, "127.0.0.1", p)
                await site.start()
                self.port = p
                return p
            except OSError:
                continue
        await self._runner.cleanup()
        return None

    async def stop(self):
        if self._runner:
            await self._runner.cleanup()

    def _guard_ok(self, request) -> tuple[bool, str | None]:
        port = self.port or request.url.port
        if not host_ok(request.headers.get("Host"), port):
            return False, None
        origin = request.headers.get("Origin")
        return origin_ok(origin, self.origins), origin

    async def _preflight(self, request):
        from aiohttp import web
        origin = request.headers.get("Origin")
        pna = request.headers.get("Access-Control-Request-Private-Network") == "true"
        return web.Response(status=204, headers=cors_headers(origin, private_network=pna))

    async def _hello(self, request):
        from aiohttp import web
        object_info = await self.comfy.object_info(fresh=True)
        workflows = [self.store.describe(r, object_info) for r in self.store.list()]
        return web.json_response({"app": "weekend-studio", "version": self.version,
                                  "machineName": self.machine_name, "machineId": self.machine_id(), "workflows": workflows})

    async def _cancel(self, request):
        from aiohttp import web
        try:
            body = await request.json()
        except ValueError:
            return web.json_response({"error": "bad request"}, status=400)
        ok = await self.runner.cancel(str((body or {}).get("jobId", "")))
        return web.json_response({"ok": ok})

    async def _run(self, request):
        from aiohttp import web
        from .runner import RunError
        if request.content_type != "application/json":   # forces the browser's preflight
            return web.json_response({"error": "JSON only"}, status=415)
        try:
            job, pictures = parse_run(await request.json())
        except ValueError as e:
            return web.json_response({"error": "bad request", "message": str(e)}, status=400)

        origin = request.headers.get("Origin")
        resp = web.StreamResponse(status=200, headers={"Content-Type": "application/x-ndjson", "Cache-Control": "no-store",
                                                       **cors_headers(origin)})
        await resp.prepare(request)

        async def line(obj):
            await resp.write((json.dumps(obj, separators=(",", ":")) + "\n").encode())

        outputs = []

        async def fetch_input(key):
            return pictures.get(key, b"")

        async def put_output(_index, data, mime):
            outputs.append({"mime": mime, "base64": base64.b64encode(data).decode("ascii")})

        async def emit(ev):
            if ev.get("type") != "done":
                await line(ev)

        try:
            await self.runner.run(job, fetch_input=fetch_input, put_output=put_output, emit=emit)
            await line({"type": "done", "outputs": outputs})
        except RunError as e:
            await line({"type": "failed", "code": e.code, "message": str(e), "details": e.details})
        except Exception as e:   # ComfyUI hiccup, a closed tab
            try:
                await line({"type": "failed", "code": "comfy", "message": str(e)[:300] or "the run failed"})
            except Exception:
                pass
        await resp.write_eof()
        return resp
