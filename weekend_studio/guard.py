"""Who may use the /weekend-studio/* routes on ComfyUI's own server (0.3.0, security review B1).

They used to rely on ComfyUI's same-origin middleware alone. That middleware only looks when the
Host is loopback, compares Host with Origin (both are the attacker's name under DNS rebinding), and
isn't installed at all with `--enable-cors-header`; with `--listen` a LAN Host skips it. So every
route checks the same three things local_server.py checks for its own:

  1. a custom header, X-Weekend-Studio: 1. A page on another site can't send it: ComfyUI's CORS
     preflight (with --enable-cors-header) allows only Content-Type and Authorization, and without
     that flag there is no CORS at all. The tab's own requests are same-origin, so no preflight.
  2. the Host is a literal loopback name (127.0.0.1, localhost, [::1]) — a rebinding name fails —
     or one of the specific addresses ComfyUI was told to --listen on (never 0.0.0.0 / ::), or a
     name listed under `allowedHosts` in the pack's settings.json (home.py: a reverse proxy, a LAN
     name you choose).
  3. an Origin, when the browser sends one, equal to the Host (same origin), and a Sec-Fetch-Site,
     when sent, of same-origin or none.

A LAN client that isn't a browser can fake all three; with `--listen 0.0.0.0` the routes therefore
answer only on loopback unless you name an address. Pure: tests/test_guard.py.
"""
from __future__ import annotations

from urllib.parse import urlsplit

HEADER = "X-Weekend-Studio"
LOOPBACK = ("127.0.0.1", "localhost", "::1")
WILDCARDS = ("0.0.0.0", "::", "")


def split_host(host: str | None) -> tuple[str, str]:
    """'Host' header → (hostname lowercased without brackets, port or '')."""
    host = (host or "").strip().lower()
    if host.startswith("["):
        end = host.find("]")
        if end < 0:
            return "", ""
        name, rest = host[1:end], host[end + 1:]
        return name, rest[1:] if rest.startswith(":") else ""
    if host.count(":") == 1:
        name, port = host.split(":")
        return name, port
    return host, ""


def allowed_names(listen: str | None = None, extra=()) -> set:
    """Host names the routes answer on: loopback, the specific --listen addresses, and `extra`
    (settings.json `allowedHosts`, Home.allowed_hosts())."""
    names = set(LOOPBACK)
    for part in (listen or "").split(","):
        h = part.strip().strip("[]").lower()
        if h not in WILDCARDS:
            names.add(h)
    for part in extra or ():
        h = split_host(str(part).strip())[0]
        if h:
            names.add(h)
    return names


def comfy_listen() -> str | None:
    try:
        from comfy.cli_args import args  # type: ignore  (ComfyUI)
    except Exception:
        return None
    return getattr(args, "listen", None)


def check(headers, names: set) -> str | None:
    """None when the request may go ahead, else the reason (for the 403 and the log)."""
    get = headers.get
    if (get(HEADER) or "").strip() != "1":
        return "missing the X-Weekend-Studio header"
    name, port = split_host(get("Host"))
    if not name or name not in names:
        return "not this computer's address"
    origin = get("Origin")
    if origin is not None:
        o = urlsplit(origin.strip()) if origin.strip() and origin.strip() != "null" else None
        if not o or o.scheme not in ("http", "https") or not o.hostname:
            return "a foreign origin"
        o_port = str(o.port) if o.port else ""
        h_port = port
        if o.hostname.lower() != name or (o_port or _default_port(o.scheme)) != (h_port or _default_port(o.scheme)):
            return "a foreign origin"
    site = get("Sec-Fetch-Site")
    if site is not None and site not in ("same-origin", "none"):
        return "a cross-site request"
    return None


def _default_port(scheme: str) -> str:
    return "443" if scheme == "https" else "80"
