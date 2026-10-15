"""The network half of bridge.py, on aiohttp (which ComfyUI ships with): the edge functions
(comfy-pair, comfy-token), the relay socket, and a job's files on /blob."""
from __future__ import annotations

import json
import os

from .bridge import ApiError

MAX_FILE = 64 * 1024 * 1024
HERE = os.path.dirname(os.path.abspath(__file__))


def service_config() -> dict:
    """Where Weekend Studio is. service.json is written from the app's env at release
    (comfy-node/tools/write-service-json.mjs); WEEKEND_STUDIO_* env vars override it."""
    try:
        with open(os.path.join(HERE, "service.json"), "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        cfg = {}
    return {
        "functionsUrl": os.environ.get("WEEKEND_STUDIO_FUNCTIONS_URL") or cfg.get("functionsUrl", ""),
        "apiKey": os.environ.get("WEEKEND_STUDIO_API_KEY") or cfg.get("apiKey", ""),
        "appUrl": os.environ.get("WEEKEND_STUDIO_APP_URL") or cfg.get("appUrl", ""),
        # Sites allowed to use same-computer mode (local_server.py); defaults to Weekend Studio's own.
        "localOrigins": cfg.get("localOrigins"),
    }


class _Session:
    def __init__(self):
        self._s = None

    async def get(self):
        import aiohttp
        if self._s is None or self._s.closed:
            self._s = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=120))
        return self._s

    async def close(self):
        if self._s is not None and not self._s.closed:
            await self._s.close()


class Api:
    def __init__(self, config: dict, session: _Session | None = None):
        self.base = config["functionsUrl"].rstrip("/")
        self.key = config["apiKey"]
        self.session = session or _Session()

    async def _post(self, fn: str, body: dict) -> dict:
        s = await self.session.get()
        # The publishable key only — no Authorization: the node has no session, and the functions
        # authenticate it by its machine token or poll secret.
        async with s.post(f"{self.base}/{fn}", json=body, headers={"apikey": self.key}) as r:
            try:
                data = await r.json(content_type=None)
            except ValueError:
                data = {}
            if r.status != 200:
                raise ApiError(r.status, str((data or {}).get("error") or r.reason or "error"))
            return data

    def machine_token(self, machine_token: str):
        return self._post("comfy-token", {"machineToken": machine_token})

    def pair_start(self, name: str):
        return self._post("comfy-pair", {"action": "start", "name": name})

    def pair_redeem(self, pairing_id: str, poll_secret: str):
        return self._post("comfy-pair", {"action": "redeem", "pairingId": pairing_id, "pollSecret": poll_secret})

    def forget(self, machine_token: str):
        return self._post("comfy-pair", {"action": "forget", "machineToken": machine_token})


class _Conn:
    def __init__(self, ws):
        self.ws = ws

    async def send(self, text: str):
        await self.ws.send_str(text)

    async def receive(self):
        import aiohttp
        msg = await self.ws.receive()
        if msg.type == aiohttp.WSMsgType.TEXT:
            return ("text", msg.data)
        if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
            return ("close", self.ws.close_code or 1006)
        return ("other", None)

    async def close(self, code: int = 1000):
        await self.ws.close(code=code)


class Transport:
    def __init__(self, session: _Session | None = None):
        self.session = session or _Session()

    async def connect(self, url: str, token: str):
        s = await self.session.get()
        ws = await s.ws_connect(url, protocols=("wknd-comfy", token), heartbeat=None, max_msg_size=16 * 1024 * 1024)
        return _Conn(ws)

    async def get_bytes(self, url: str, ticket: str) -> bytes:
        s = await self.session.get()
        async with s.get(url, headers={"Authorization": f"Bearer {ticket}"}) as r:
            if r.status != 200:
                raise RuntimeError(f"couldn't fetch an input ({r.status})")
            if (r.content_length or 0) > MAX_FILE:
                raise RuntimeError("an input is too large")
            parts, size = [], 0
            async for chunk in r.content.iter_chunked(1 << 20):
                size += len(chunk)
                if size > MAX_FILE:
                    raise RuntimeError("an input is too large")
                parts.append(chunk)
            return b"".join(parts)

    async def put_bytes(self, url: str, ticket: str, data: bytes, mime: str) -> None:
        s = await self.session.get()
        async with s.put(url, data=data, headers={"Authorization": f"Bearer {ticket}", "content-type": mime}) as r:
            if r.status != 201:
                raise RuntimeError(f"couldn't upload the result ({r.status})")
