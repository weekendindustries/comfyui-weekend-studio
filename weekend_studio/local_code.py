"""The no-account code (0.4.0, owner 2026-11-16: "it should run on a computer on the same network or same
computer … this should not require server").

With "Use without an account" on, the tab shows a code like ABCD-EFGH-JKMN. Typed into Weekend Studio in a
browser on this computer or another one on the same network, it says where this ComfyUI is (its address on
the network and its port) and carries a secret that proves the person read it here. Nothing goes through
Weekend Studio's servers: the browser talks straight to this computer (local_server.py), which accepts a
request from another computer only with the code.

Pure; the browser's copy is packages/comfy/localCode.js, and packages/comfy/fixtures/local_codes.json holds the two
together.

The code is a 12-character number in the pairing code's alphabet (no 0, 1, I, L or O), 31^12 ≈ 2^59:
    value = ((secret · 8 + port − 8189) · 2^bits + address) · 4 + kind
    kind 0  192.168.x.y   (16 bits)      kind 2  172.16–31.x.y  (20 bits)
    kind 1  10.x.y.z      (24 bits)      kind 3  any IPv4       (32 bits)
The secret takes what's left: about 2^38 for a home network (kind 0), 2^22 at the least (kind 3, an address outside the private ranges). A
computer on the network that guesses gets 20 wrong codes before it's shut out for ten minutes
(local_server.py), so a guess takes years.
"""
from __future__ import annotations

import ipaddress
import secrets

ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"   # the pairing code's (packages/comfy/code.js)
LENGTH = 12
SPACE = len(ALPHABET) ** LENGTH
FIRST_PORT = 8189
PORT_SPAN = 8                                   # 8189…8196: the node tries five from localPort's default
KIND_BITS = (16, 24, 20, 32)


def _kind(ip: ipaddress.IPv4Address) -> tuple[int, int]:
    """(kind, address bits) for an IPv4 address."""
    n = int(ip)
    if ip in ipaddress.ip_network("192.168.0.0/16"):
        return 0, n & 0xFFFF
    if ip in ipaddress.ip_network("10.0.0.0/8"):
        return 1, n & 0xFFFFFF
    if ip in ipaddress.ip_network("172.16.0.0/12"):
        return 2, n & 0xFFFFF
    return 3, n


def _address(kind: int, bits: int) -> str:
    base = (0xC0A80000, 0x0A000000, 0xAC100000, 0)[kind]
    return str(ipaddress.IPv4Address(base | bits))


def secret_room(kind: int) -> int:
    """How many secrets fit beside a kind's address and the port."""
    return SPACE // (4 * (1 << KIND_BITS[kind]) * PORT_SPAN)


def new_secret() -> int:
    """Stored once (home.py); each code uses it modulo its kind's room."""
    return secrets.randbits(64)


def _spell(value: int) -> str:
    chars = []
    for _ in range(LENGTH):
        value, d = divmod(value, len(ALPHABET))
        chars.append(ALPHABET[d])
    s = "".join(reversed(chars))
    return f"{s[0:4]}-{s[4:8]}-{s[8:12]}"


def encode(address: str, port: int, secret: int) -> str | None:
    """The code for this ComfyUI at address:port, or None when it can't say that (IPv6, a port out of range)."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return None
    if ip.version != 4 or not (FIRST_PORT <= int(port) < FIRST_PORT + PORT_SPAN):
        return None
    kind, bits = _kind(ip)
    value = (((secret % secret_room(kind)) * PORT_SPAN + (int(port) - FIRST_PORT)) * (1 << KIND_BITS[kind]) + bits) * 4 + kind
    return _spell(value)


def normalize(code) -> str | None:
    """A typed or pasted code → its 12 characters, or None."""
    if not isinstance(code, str):
        return None
    chars = "".join(c for c in code.upper() if c in ALPHABET)
    return chars if len(chars) == LENGTH else None


def decode(code) -> dict | None:
    """→ {address, port, secret} (secret modulo its kind's room), or None for anything that isn't a code."""
    chars = normalize(code)
    if not chars:
        return None
    value = 0
    for c in chars:
        value = value * len(ALPHABET) + ALPHABET.index(c)
    value, kind = divmod(value, 4)
    value, bits = divmod(value, 1 << KIND_BITS[kind])
    secret, offset = divmod(value, PORT_SPAN)
    if secret >= secret_room(kind):
        return None
    return {"address": _address(kind, bits), "port": FIRST_PORT + offset, "secret": secret, "kind": kind}


def matches(code, secret: int) -> bool:
    """Does a presented code carry this ComfyUI's secret? (Its address and port may be out of date: it got
    here, so it was right enough.)"""
    d = decode(code)
    if not d:
        return False
    return secrets.compare_digest(str(d["secret"]), str(secret % secret_room(d["kind"])))


def lan_address() -> str | None:
    """This computer's address on the network, as other computers there reach it; None if it has none.
    A UDP socket "connected" to a private address picks the interface without sending anything."""
    import socket
    for probe in ("10.255.255.255", "192.168.255.255"):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((probe, 1))
            ip = s.getsockname()[0]
            if ip and not ipaddress.ip_address(ip).is_loopback and ip != "0.0.0.0":
                return ip
        except OSError:
            pass
        finally:
            s.close()
    return None
