"""The connection to Weekend Studio: pairing, then one outbound socket to the relay.

  pairing    pair(name) → a code + a link; polling redeems the approval for a machine token,
             saved in ComfyUI's user folder (credentials.py). Nothing to type into ComfyUI.
  connected  machine token → comfy-token → a short relay token → wss://…/hub. Says hello with the
             published workflows, answers run/cancel, re-auths when the relay asks.

Everything that touches the network is injected (api, transport), so this file is the logic only
and tests run it against fakes; bridge_io.py is the aiohttp side.

Relay protocol: workers/comfy-bridge/README.md.
"""
from __future__ import annotations

import asyncio
import json
import time

from .runner import RunError

PING_EVERY = 25.0
BACKOFF = (1, 2, 5, 10, 30, 60)
REFUSED_WAIT = 300.0             # membership lapsed / account frozen: try again in five minutes
# Over the relay, progress at most every 12 s and no per-node "executing" chatter (cost, 2026-10-14):
# the hub is billed while awake, and it can hibernate only after 10 s of quiet — so a long run with
# frequent updates kept it awake (and billed) start to finish. Same-computer runs are free and keep
# their fine-grained progress (local_server.py).
PROGRESS_EVERY = 12.0
MAX_PARALLEL = 4                 # jobs handed to ComfyUI at once (it queues them itself)

CLOSE_REMOVED = 4403
CLOSE_REAUTH = 4401
CLOSE_REPLACED = 4409


class ApiError(Exception):
    def __init__(self, status: int, code: str):
        super().__init__(f"{status} {code}")
        self.status = status
        self.code = code


class Bridge:
    def __init__(self, *, store, runner, comfy, api, transport, credentials, version: str,
                 machine_name: str = "ComfyUI", sleep=asyncio.sleep, clock=time.monotonic, time_scale: float = 1.0):
        self.store = store
        self.runner = runner
        self.comfy = comfy
        self.api = api
        self.transport = transport
        self.credentials = credentials
        self.version = version
        self.machine_name = machine_name
        self.sleep = sleep
        self.clock = clock
        self.time_scale = time_scale   # tests shrink every wait
        self.state = "disconnected"
        self.detail = None          # why we're not connected, in words
        self.pairing = None         # {code, approveUrl, expiresAt}
        self.conn = None
        self.hub_http = None
        self._wake = asyncio.Event()
        self._stopped = False
        self._jobs = {}
        self._slots = asyncio.Semaphore(MAX_PARALLEL)

    # ── What the ComfyUI tab shows ─────────────────────────────────────────────────────────────
    def status(self) -> dict:
        creds = self.credentials.load()
        out = {"state": self.state, "detail": self.detail, "machineName": (creds or {}).get("name"),
               "running": len(self._jobs)}
        if self.pairing:
            out["pairing"] = {"code": self.pairing["code"], "approveUrl": self.pairing["approveUrl"],
                              "expiresIn": max(0, int(self.pairing["expiresAt"] - self.clock()))}
        return out

    # ── Pairing ────────────────────────────────────────────────────────────────────────────────
    async def pair(self, name: str | None = None) -> dict:
        """Start (or restart) pairing. Returns the code and the link to open."""
        r = await self.api.pair_start(name or self.machine_name)
        self.pairing = {"id": r["pairingId"], "secret": r["pollSecret"], "code": r["code"],
                        "approveUrl": r["approveUrl"], "expiresAt": self.clock() + r["expiresIn"],
                        "interval": max(1, int(r.get("interval") or 3))}
        self.state, self.detail = "pairing", None
        asyncio.ensure_future(self._poll(self.pairing))
        return self.status()["pairing"]

    async def _poll(self, pairing):
        while self.pairing is pairing and self.clock() < pairing["expiresAt"]:
            await self.sleep(pairing["interval"] * self.time_scale)
            if self.pairing is not pairing:
                return
            try:
                r = await self.api.pair_redeem(pairing["id"], pairing["secret"])
            except ApiError as e:
                if e.status == 429:
                    await self.sleep(30 * self.time_scale)
                continue
            except Exception:
                continue   # a network blip: keep polling until the code runs out
            if r.get("status") == "approved":
                self.credentials.save({"machineToken": r["machineToken"], "machineId": r["machineId"], "name": r["name"]})
                self.pairing = None
                self.state, self.detail = "connecting", None
                self._wake.set()
                return
            if r.get("status") == "expired":
                break
        if self.pairing is pairing:
            self.pairing = None
            self.state, self.detail = "disconnected", "The code expired. Click Connect for a new one."

    async def disconnect(self):
        """Forget this machine: on the account (best effort) and here."""
        creds = self.credentials.load()
        self.pairing = None
        if creds:
            try:
                await self.api.forget(creds["machineToken"])
            except Exception:
                pass
        self.credentials.clear()
        self.state, self.detail = "disconnected", None
        if self.conn:
            await self.conn.close(1000)

    # ── The connection ─────────────────────────────────────────────────────────────────────────
    def stop(self):
        self._stopped = True
        self._wake.set()

    async def run_forever(self):
        attempt = 0
        while not self._stopped:
            creds = self.credentials.load()
            if not creds:
                if not self.pairing:
                    self.state = "disconnected"
                self._wake.clear()
                await self._wake.wait()
                continue
            self.state = "connecting"
            try:
                tok = await self.api.machine_token(creds["machineToken"])
            except ApiError as e:
                if e.status == 401:   # removed from the account: forget, offer Connect again
                    self.credentials.clear()
                    self.state, self.detail = "disconnected", "This machine was removed from your account. Click Connect to add it again."
                    continue
                if e.status == 403:
                    self.state = "refused"
                    self.detail = ("Your account is paused." if e.code == "account_frozen"
                                   else "Connecting ComfyUI is part of membership. Renew it to use this machine.")
                    await self._nap(REFUSED_WAIT)
                    continue
                attempt += 1
                await self._nap(BACKOFF[min(attempt, len(BACKOFF)) - 1])
                continue
            except Exception:
                attempt += 1
                self.detail = "Can't reach Weekend Studio. Retrying…"
                await self._nap(BACKOFF[min(attempt, len(BACKOFF)) - 1])
                continue

            hub = tok["hubUrl"].rstrip("/")
            self.hub_http = "https" + hub[3:] if hub.startswith("wss") else "http" + hub[2:] if hub.startswith("ws") else hub
            try:
                self.conn = await self.transport.connect(f"{hub}/hub", tok["token"])
            except Exception:
                attempt += 1
                self.detail = "Can't reach Weekend Studio. Retrying…"
                await self._nap(BACKOFF[min(attempt, len(BACKOFF)) - 1])
                continue
            attempt = 0
            code = await self._session(creds)
            self.conn = None
            if code == CLOSE_REMOVED:
                self.credentials.clear()
                self.state, self.detail = "disconnected", "This machine was removed from your account."
            elif code == CLOSE_REPLACED:
                self.state, self.detail = "connecting", "Another ComfyUI connected with this machine's identity."
                await self._nap(60)
            elif code != CLOSE_REAUTH and not self._stopped and self.credentials.load():
                self.state = "connecting"
                await self._nap(BACKOFF[0])

    async def _nap(self, seconds):
        """Sleep, but wake early for a stop."""
        self._wake.clear()
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=seconds * self.time_scale)
        except asyncio.TimeoutError:
            pass

    async def _session(self, creds) -> int:
        self.state, self.detail = "connected", None
        await self.send_hello()
        pinger = asyncio.ensure_future(self._ping())
        try:
            while True:
                kind, data = await self.conn.receive()
                if kind == "close":
                    return data
                if kind != "text":
                    continue
                try:
                    msg = json.loads(data)
                except ValueError:
                    continue
                if isinstance(msg, dict):
                    await self.handle(msg)
        finally:
            pinger.cancel()
            # The relay has already failed these jobs; stop them in ComfyUI too, to free the GPU.
            for job_id, task in list(self._jobs.items()):
                await self.runner.cancel(job_id)
                task.cancel()

    async def _ping(self):
        while True:
            await self.sleep(PING_EVERY * self.time_scale)
            await self._send({"t": "ping"})

    async def _send(self, msg):
        if self.conn:
            try:
                await self.conn.send(json.dumps(msg, separators=(",", ":")))
            except Exception:
                pass

    async def _descriptions(self):
        object_info = await self.comfy.object_info(fresh=True)
        return [self.store.describe(r, object_info) for r in self.store.list()]

    async def send_hello(self):
        await self._send({"t": "hello", "version": self.version, "workflows": await self._descriptions(),
                          "status": {"busy": bool(self._jobs), "queue": len(self._jobs)}})

    async def workflows_changed(self):
        """Publish/unpublish in ComfyUI → the app's list updates live."""
        if self.state == "connected":
            await self._send({"t": "workflows", "workflows": await self._descriptions()})

    # ── Relay messages ─────────────────────────────────────────────────────────────────────────
    async def handle(self, msg: dict):
        t = msg.get("t")
        if t == "run":
            job_id = (msg.get("job") or {}).get("jobId")
            if isinstance(job_id, str) and job_id not in self._jobs:
                self._jobs[job_id] = asyncio.ensure_future(self._run(msg))
                await self._status()
        elif t == "cancel":
            if isinstance(msg.get("jobId"), str):
                await self.runner.cancel(msg["jobId"])
        elif t == "reauth":
            creds = self.credentials.load()
            if not creds:
                return
            try:
                tok = await self.api.machine_token(creds["machineToken"])
            except ApiError as e:
                if e.status in (401, 403) and self.conn:
                    await self.conn.close(1000)   # the next connect attempt explains why
                return
            except Exception:
                return   # the relay closes us after its grace; reconnecting retries
            await self._send({"t": "reauth", "token": tok["token"]})

    async def _status(self):
        await self._send({"t": "status", "busy": bool(self._jobs), "queue": len(self._jobs)})

    def _url(self, path: str) -> str:
        if not isinstance(path, str) or not path.startswith("/blob/"):
            raise RunError("invalid", "bad file path from the relay")
        return f"{self.hub_http}{path}"

    async def _run(self, msg):
        job = msg.get("job") or {}
        job_id = job.get("jobId")
        inputs = msg.get("inputs") or {}
        output = msg.get("output") or {}
        outputs = []
        last_progress = [0.0]

        async def fetch_input(key):
            spec = inputs.get(key)
            if not isinstance(spec, dict):
                return b""
            return await self.transport.get_bytes(self._url(spec.get("path")), spec.get("ticket"))

        async def put_output(index, data, mime):
            name = f"out-{index}"
            await self.transport.put_bytes(self._url(f"{output.get('prefix', '')}{index}"), output.get("ticket"), data, mime)
            outputs.append({"name": name, "mime": mime, "size": len(data)})   # the relay trusts this: no existence check

        async def emit(ev):
            if ev.get("type") == "executing":
                return
            if ev.get("type") == "progress":
                now = self.clock()
                if now - last_progress[0] < PROGRESS_EVERY:
                    return
                last_progress[0] = now
            if ev.get("type") != "done":   # the relay sends done itself, with tickets
                await self._send({"t": "event", "jobId": job_id, "ev": ev})

        try:
            async with self._slots:
                await self.runner.run(job, fetch_input=fetch_input, put_output=put_output, emit=emit)
            await self._send({"t": "done", "jobId": job_id, "outputs": outputs})
        except RunError as e:
            await self._send({"t": "failed", "jobId": job_id, "code": e.code, "message": str(e), "details": e.details})
        except asyncio.CancelledError:
            raise
        except Exception as e:   # a network error moving pixels, a ComfyUI hiccup
            await self._send({"t": "failed", "jobId": job_id, "code": "comfy", "message": str(e)[:300] or "the run failed"})
        finally:
            self._jobs.pop(job_id, None)
            await self._status()
