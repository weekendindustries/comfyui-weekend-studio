"""What leaves this computer is trimmed of what identifies the person (0.3.0, security review B5).

ComfyUI's error text often carries a full path, `/Users/<name>/ComfyUI/…` or `C:\\Users\\<name>\\…`,
and those messages go to every open Weekend Studio tab (and through the relay). The home folder in
them becomes `~`. Pure: tests/test_privacy.py.
"""
from __future__ import annotations

import os
import platform
import re

_GENERIC = (
    # C:\Users\name — Windows names may hold spaces ("First Last"), so the name runs to the next slash.
    re.compile(r"(?i)\b[a-z]:[\\/]+(?:users|documents and settings)[\\/]+[^\\/'\"<>|:\r\n]+"),
    re.compile(r"/(?:Users|home)/[^/\s'\"<>|:]+"),                                              # /Users/name, /home/name
    re.compile(r"/root(?=/)"),
)


def _homes() -> list:
    out = []
    for h in {os.path.expanduser("~"), os.path.realpath(os.path.expanduser("~"))}:
        if h and h not in ("/", "~") and len(h) > 3:
            out += [h, h.replace("\\", "/"), h.replace("/", "\\")]
    return sorted(set(out), key=len, reverse=True)


def scrub(text, homes=None):
    """`text` with home-folder paths shown as `~`. Anything that isn't a string comes back as it is."""
    if not isinstance(text, str) or not text:
        return text
    for h in homes if homes is not None else _homes():
        text = text.replace(h, "~")
    for rx in _GENERIC:
        text = rx.sub("~", text)
    return text


def scrub_all(value, depth: int = 0):
    """scrub() through lists and dicts (an error's details), values only."""
    if depth > 6:
        return value
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, list):
        return [scrub_all(v, depth + 1) for v in value]
    if isinstance(value, dict):
        return {k: scrub_all(v, depth + 1) for k, v in value.items()}
    return value


def default_machine_name(system: str | None = None) -> str:
    """The name a new connection offers. Never the hostname (it is often the person's own name,
    "Firstname's MacBook Pro"); the member can name it at Connect or rename it in Weekend Studio."""
    system = system if system is not None else platform.system()
    pretty = {"Darwin": "macOS", "Windows": "Windows", "Linux": "Linux"}.get(system)
    return f"ComfyUI ({pretty})" if pretty else "ComfyUI"
