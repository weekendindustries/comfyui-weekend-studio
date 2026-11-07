"""This machine's identity: the machine token comfy-pair issued, readable by this user only. It's the
one secret the node holds; Disconnect deletes it.

Kept in the pack's own folder (home.py), never under ComfyUI's user/ folder: ComfyUI serves that
over HTTP (/api/userdata), so a token there could be read, or swapped for someone else's, by
anything that reached ComfyUI's port (0.3.0, security review B1)."""
from __future__ import annotations

import json
import os
import tempfile


class Credentials:
    def __init__(self, path: str):
        self.path = path

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or not isinstance(data.get("machineToken"), str):
            return None
        return data

    def save(self, data: dict) -> None:
        folder = os.path.dirname(self.path)
        os.makedirs(folder, exist_ok=True)
        try:
            os.chmod(folder, 0o700)
        except OSError:
            pass
        fd, tmp = tempfile.mkstemp(dir=folder, prefix=".tmp-", suffix=".json")
        try:
            os.chmod(tmp, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise

    def clear(self) -> None:
        try:
            os.remove(self.path)
        except FileNotFoundError:
            pass
