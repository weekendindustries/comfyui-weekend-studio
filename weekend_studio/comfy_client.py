"""Talking to the ComfyUI we're installed in, through its own HTTP + WebSocket API.

Over loopback HTTP rather than by reaching into ComfyUI's internals: the API is the stable surface
across ComfyUI versions (and Comfy Desktop), and it runs every graph through ComfyUI's own prompt
validation. Requests from here carry no Origin header, so ComfyUI's same-origin guard lets them by.

Needs aiohttp, which ComfyUI itself ships with. Imported lazily so the pure modules test without it.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid

OBJECT_INFO_TTL = 30.0       # seconds — /object_info is several MB on a big install
HISTORY_POLL = 5.0           # ask /history (and /queue) this often while a run is going
CANCEL_POLL = 0.5            # … and this often once we've asked ComfyUI to drop it
RUN_LIMIT = 2 * 60 * 60      # a single run may take up to two hours
HISTORY_WAIT_TRIES = 40       # × HISTORY_WAIT_STEP: up to 6 s for ComfyUI to file a finished run
HISTORY_WAIT_STEP = 0.15


def local_base_url() -> str:
    """Where this ComfyUI listens, as seen from inside the same process."""
    try:
        from comfy.cli_args import args  # type: ignore
    except Exception:
        return "http://127.0.0.1:8188"
    port = getattr(args, "port", 8188) or 8188
    scheme = "https" if getattr(args, "tls_keyfile", None) and getattr(args, "tls_certfile", None) else "http"
    listen = (getattr(args, "listen", None) or "127.0.0.1").split(",")
    hosts = [h.strip() for h in listen if h.strip()]
    if not hosts or any(h in ("0.0.0.0", "::", "127.0.0.1", "localhost", "::1") for h in hosts):
        host = "127.0.0.1"
    else:
        host = hosts[0]
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"{scheme}://{host}:{port}"


def own_tls_context():
    """TLS for talking to THIS ComfyUI over its own address, when it was started with --tls-keyfile:
    such certificates are usually self-signed for 127.0.0.1, so they can't be verified. Used only for
    the address local_base_url() worked out from ComfyUI's own arguments — never for another host
    (everything else this pack fetches is verified normally)."""
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


class ComfyClient:
    def __init__(self, base_url: str | None = None):
        self._own = base_url is None   # the address came from ComfyUI's own arguments
        self.base = (base_url or local_base_url()).rstrip("/")
        self._session = None
        self._canceling = set()     # prompt ids we asked ComfyUI to drop: check on them sooner
        self._object_info = None
        self._object_info_at = 0.0

    async def _http(self):
        import aiohttp  # ComfyUI's own dependency
        if self._session is None or self._session.closed:
            # Plain http on loopback is the usual case. Only our own https address skips verification.
            own_tls = self._own and self.base.startswith("https://")
            connector = aiohttp.TCPConnector(ssl=own_tls_context()) if own_tls else aiohttp.TCPConnector()
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=120),
                connector=connector,
            )
        return self._session

    async def close(self):
        if self._session is not None:
            await self._session.close()

    async def object_info(self, fresh: bool = False) -> dict:
        now = time.monotonic()
        if fresh or self._object_info is None or now - self._object_info_at > OBJECT_INFO_TTL:
            s = await self._http()
            async with s.get(f"{self.base}/object_info") as r:
                r.raise_for_status()
                self._object_info = await r.json()
                self._object_info_at = now
        return self._object_info

    async def run(self, graph: dict):
        """Queue `graph` and yield events until it ends:
        queued{prompt_id} · executing{node} · progress{value,max,node} · error{message,node,node_type}
        · interrupted · done. The socket opens BEFORE queueing so no early event is missed.

        Each run has its own client id (0.2.2): ComfyUI keeps ONE socket per client id and replaces it
        when another connects with the same id, so runs sharing one id lost each other's events."""
        s = await self._http()
        client_id = f"weekend-studio-{uuid.uuid4().hex[:12]}"
        ws_url = self.base.replace("http", "ws", 1) + f"/ws?clientId={client_id}"
        async with s.ws_connect(ws_url, heartbeat=30) as ws:
            async with s.post(f"{self.base}/prompt", json={"prompt": graph, "client_id": client_id}) as r:
                body = await r.json(content_type=None)
                if r.status != 200 or "prompt_id" not in body:
                    yield {"type": "error", "message": prompt_error(body)}
                    return
                prompt_id = body["prompt_id"]
            yield {"type": "queued", "prompt_id": prompt_id, "position": body.get("number")}

            import aiohttp
            closed = (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR)
            deadline = time.monotonic() + RUN_LIMIT
            next_check = time.monotonic() + HISTORY_POLL
            ws_open = True
            try:
                while time.monotonic() < deadline:
                    # Every few seconds, socket chatter or not, ask ComfyUI where the run is: a prompt
                    # dropped from the queue (our cancel, or "Clear queue" in ComfyUI) sends no event
                    # at all, so waiting for one hung the run for two hours.
                    wait = max(0.0, next_check - time.monotonic())
                    if ws_open and wait > 0:
                        try:
                            msg = await ws.receive(timeout=wait)
                        except asyncio.TimeoutError:
                            msg = None
                        if msg is not None and msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                ev = self._event(json.loads(msg.data), prompt_id)
                            except ValueError:
                                ev = None
                            if ev:
                                yield ev
                                if ev["type"] in ("done", "error", "interrupted"):
                                    return
                            continue
                        if msg is not None and msg.type in closed:
                            ws_open = False
                        elif msg is not None:
                            continue   # binary preview frames — a later round forwards these
                    elif wait > 0:
                        await asyncio.sleep(wait)
                    next_check = time.monotonic() + (CANCEL_POLL if prompt_id in self._canceling else HISTORY_POLL)
                    ended = await self._where(prompt_id)
                    if ended:
                        yield ended
                        return
                await self.cancel(prompt_id)
                yield {"type": "error", "message": "the run took longer than two hours and was stopped"}
            finally:
                self._canceling.discard(prompt_id)

    async def _where(self, prompt_id: str):
        """How the run ended, or None while it's still queued or running. History first; then the
        queue; a prompt in neither was dropped without a word. History is asked once more after the
        queue, because a run can finish between the two questions."""
        ended = await self._history_status(prompt_id)
        if ended:
            return ended
        if await self._queued(prompt_id) is not False:
            return None
        return await self._history_status(prompt_id) or {"type": "interrupted"}

    async def _queued(self, prompt_id: str):
        """"running" / "pending" when ComfyUI holds this prompt, False when it doesn't, None when the
        queue couldn't be read (then nothing is concluded)."""
        s = await self._http()
        try:
            async with s.get(f"{self.base}/queue") as r:
                if r.status != 200:
                    return None
                q = await r.json()
        except Exception:
            return None
        return queue_state(q, prompt_id)

    def _event(self, m: dict, prompt_id: str):
        t, d = m.get("type"), m.get("data") or {}
        if d.get("prompt_id") not in (None, prompt_id):
            return None
        if t == "progress" and d.get("prompt_id") == prompt_id:
            return {"type": "progress", "value": d.get("value"), "max": d.get("max"), "node": d.get("node")}
        if t == "executing" and d.get("prompt_id") == prompt_id:
            return {"type": "done"} if d.get("node") is None else {"type": "executing", "node": d.get("node")}
        if t == "execution_success" and d.get("prompt_id") == prompt_id:
            return {"type": "done"}
        if t == "execution_error" and d.get("prompt_id") == prompt_id:
            return {"type": "error", "message": d.get("exception_message") or "ComfyUI stopped with an error",
                    "node": d.get("node_id"), "node_type": d.get("node_type")}
        if t == "execution_interrupted" and d.get("prompt_id") == prompt_id:
            return {"type": "interrupted"}
        return None

    async def _history_status(self, prompt_id: str):
        entry = await self._history(prompt_id)
        if not entry:
            return None
        status = entry.get("status") or {}
        if status.get("status_str") == "error":
            return {"type": "error", "message": "ComfyUI stopped with an error"}
        if status.get("completed") or entry.get("outputs"):
            return {"type": "done"}
        return None

    async def _history(self, prompt_id: str):
        s = await self._http()
        async with s.get(f"{self.base}/history/{prompt_id}") as r:
            if r.status != 200:
                return None
            return (await r.json()).get(prompt_id)

    async def outputs(self, prompt_id: str) -> dict:
        """The run's outputs. ComfyUI says "done" on its socket a moment BEFORE it writes the run into
        its history (prompt_worker stores it after execute returns), so a read right after "done" can
        find nothing — seen 2026-10-14 when a same-computer run was faster than the relay ever is.
        Ask again for a few seconds until the entry is there."""
        for _ in range(HISTORY_WAIT_TRIES):
            entry = await self._history(prompt_id)
            if entry and (entry.get("outputs") or (entry.get("status") or {}).get("completed")):
                return entry.get("outputs") or {}
            await asyncio.sleep(HISTORY_WAIT_STEP)
        return {}

    async def view(self, image: dict) -> bytes:
        s = await self._http()
        params = {"filename": image.get("filename", ""), "subfolder": image.get("subfolder", ""), "type": image.get("type", "output")}
        async with s.get(f"{self.base}/view", params=params) as r:
            r.raise_for_status()
            return await r.read()

    async def cancel(self, prompt_id: str) -> None:
        """Drop it from the queue if it's waiting; interrupt it only if it's the one running.
        (ComfyUI before mid-2025 ignores prompt_id on /interrupt and stops whatever runs — so a
        blind interrupt could stop the person's own job.)"""
        self._canceling.add(prompt_id)
        s = await self._http()
        try:
            async with s.post(f"{self.base}/queue", json={"delete": [prompt_id]}):
                pass
            if await self._queued(prompt_id) == "running":
                async with s.post(f"{self.base}/interrupt", json={"prompt_id": prompt_id}):
                    pass
        except Exception:
            pass


def queue_state(q, prompt_id: str):
    """Where `prompt_id` is in ComfyUI's GET /queue answer: "running", "pending" or False.
    Each entry is [number, prompt_id, prompt, extra, outputs]."""
    if not isinstance(q, dict):
        return None
    for key, state in (("queue_running", "running"), ("queue_pending", "pending")):
        for item in q.get(key) or []:
            if isinstance(item, (list, tuple)) and len(item) > 1 and item[1] == prompt_id:
                return state
    return False


READABLE_NODES = {"WeekendImageIn": "Weekend Image In", "WeekendMaskIn": "Weekend Mask In", "WeekendOutput": "Weekend Output",
                  "SaveImage": "Save Image", "PreviewImage": "Preview Image"}


def prompt_error(body) -> str:
    """ComfyUI's /prompt refusal as one readable line."""
    if not isinstance(body, dict):
        return "ComfyUI refused the workflow"
    err = body.get("error")
    msg = err.get("message") if isinstance(err, dict) else (err if isinstance(err, str) else None)
    details = []
    for node_id, ne in (body.get("node_errors") or {}).items():
        name = READABLE_NODES.get((ne or {}).get("class_type"), (ne or {}).get("class_type", node_id))
        for e in (ne or {}).get("errors") or []:
            if e.get("type") == "required_input_missing" and e.get("details"):
                details.append(f"{name}: the \u201c{e['details']}\u201d input isn't connected")
            else:
                details.append(f"{name}: {e.get('details') or e.get('message')}")
    line = msg or "ComfyUI refused the workflow"
    return f"{line} — {'; '.join(details[:3])}" if details else line
