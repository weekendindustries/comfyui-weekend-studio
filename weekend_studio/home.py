"""Where this pack keeps what is its own: the machine token, the published workflows, the install
id and the two switches (0.3.0, security review B1).

None of it lives under ComfyUI's `user/` folder any more. ComfyUI's userdata API serves
`user/<user>/…` over HTTP for reading AND writing (`GET/POST /api/userdata/<file>`), so anyone who
could reach ComfyUI's port (`--listen` on a LAN, `--enable-cors-header`, DNS rebinding) could read
the machine token there, or plant a published workflow. The pack's folder is outside the ComfyUI
tree altogether, one per ComfyUI install:

  Windows   %LOCALAPPDATA%\\WeekendStudio\\ComfyUI\\<install>\\
  macOS     ~/Library/Application Support/Weekend Studio/ComfyUI/<install>/
  Linux     $XDG_CONFIG_HOME (or ~/.config)/weekend-studio/comfyui/<install>/
            (Docker: put that folder on a volume)

The only things read from the environment are the OS's own folder names above (LOCALAPPDATA,
XDG_CONFIG_HOME), to find where per-user settings belong. Nothing of the pack's own comes from
environment variables (0.3.2: the Comfy Registry's scan reads an environment variable next to a
network call as a possible credential leak, and the pack never needs one).

<install> is a hash of the ComfyUI folder's path, so two ComfyUIs on one computer never share a
machine token (they would keep replacing each other on the relay). Folders are 0700, files 0600.

  machine.json    the machine token comfy-pair issued (credentials.py)
  install.json    {installId, comfyPath, createdAt}: installId is written into each workflow this
                  ComfyUI shows in Weekend Studio, so a workflow file from elsewhere is told apart (B2)
  settings.json   {sameComputer, autoSync}: both off until the user turns them on in the tab (B6, B2).
                  Two more keys, edited by hand when needed, never by the tab: `localPort` (where
                  same-computer mode starts looking, default 8189) and `allowedHosts` (names the tab's
                  routes also answer on: a reverse proxy, a LAN name; guard.py)
  workflows/      the published workflows (store.py)

Pure standard library: the tests run it in plain `python -m unittest`.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import sys
import tempfile
import time

SETTINGS_DEFAULTS = {"sameComputer": False, "autoSync": False}
ADVANCED_KEYS = ("localPort", "allowedHosts")


def config_base(env=None, platform: str | None = None, home: str | None = None) -> str:
    """The OS's per-user config folder for this pack (all installs)."""
    env = os.environ if env is None else env
    platform = platform or sys.platform
    home = home or os.path.expanduser("~")
    if platform.startswith("win"):
        root = env.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
        return os.path.join(root, "WeekendStudio", "ComfyUI")
    if platform == "darwin":
        return os.path.join(home, "Library", "Application Support", "Weekend Studio", "ComfyUI")
    root = env.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
    return os.path.join(root, "weekend-studio", "comfyui")


def install_key(comfy_path: str) -> str:
    return hashlib.sha256(os.path.realpath(comfy_path).encode("utf-8")).hexdigest()[:16]


def home_dir(comfy_path: str, env=None, platform: str | None = None, home: str | None = None) -> str:
    """This install's folder (not created here)."""
    return os.path.join(config_base(env, platform, home), install_key(comfy_path))


def ensure_private_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass   # Windows / a mounted volume: the folder is already the user's own
    return path


def write_private_json(path: str, data) -> None:
    """Atomic, 0600: never a half-written file, never readable by other users."""
    folder = ensure_private_dir(os.path.dirname(path))
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".tmp-", suffix=".json")
    try:
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def read_json(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


class Home:
    """One ComfyUI install's own folder."""

    def __init__(self, root: str, comfy_path: str = ""):
        self.root = root
        self.comfy_path = comfy_path

    # The pieces
    @property
    def machine_file(self) -> str:
        return os.path.join(self.root, "machine.json")

    @property
    def workflows_dir(self) -> str:
        return os.path.join(self.root, "workflows")

    @property
    def install_file(self) -> str:
        return os.path.join(self.root, "install.json")

    @property
    def settings_file(self) -> str:
        return os.path.join(self.root, "settings.json")

    # The install id: random, made once, never derived from anything about the computer.
    def install_id(self) -> str:
        data = read_json(self.install_file)
        if isinstance(data, dict) and isinstance(data.get("installId"), str) and len(data["installId"]) == 16:
            return data["installId"]
        new = secrets.token_hex(8)
        write_private_json(self.install_file, {"installId": new, "comfyPath": self.comfy_path, "createdAt": int(time.time())})
        return new

    # The switches. Both off until the user turns them on in the tab.
    def settings(self) -> dict:
        data = read_json(self.settings_file)
        out = dict(SETTINGS_DEFAULTS)
        if isinstance(data, dict):
            for k in SETTINGS_DEFAULTS:
                if isinstance(data.get(k), bool):
                    out[k] = data[k]
        return out

    def update_settings(self, changes: dict) -> dict:
        out = self.settings()
        for k, v in (changes or {}).items():
            if k in SETTINGS_DEFAULTS and isinstance(v, bool):
                out[k] = v
        data = read_json(self.settings_file)
        kept = {k: data[k] for k in ADVANCED_KEYS if isinstance(data, dict) and k in data}
        write_private_json(self.settings_file, {**kept, **out})   # the hand-edited keys stay
        return out

    # The hand-edited keys (see the top of this file). Bad values are ignored, never fatal.
    def local_port(self) -> int | None:
        data = read_json(self.settings_file)
        v = data.get("localPort") if isinstance(data, dict) else None
        return v if isinstance(v, int) and not isinstance(v, bool) and 1024 <= v <= 65530 else None

    def allowed_hosts(self) -> list:
        data = read_json(self.settings_file)
        v = data.get("allowedHosts") if isinstance(data, dict) else None
        if isinstance(v, str):
            v = v.split(",")
        return [h.strip() for h in v if isinstance(h, str) and h.strip()][:32] if isinstance(v, list) else []

    # From 0.2.x: everything used to sit in ComfyUI's served user/default/weekend-studio/.
    def migrate_from(self, old_dir: str) -> dict:
        """Move the machine token and the published workflows out of the folder ComfyUI serves.
        Runs at every start; does nothing once the old folder is gone. Returns what it moved."""
        moved = {"token": False, "workflows": 0}
        if not old_dir or not os.path.isdir(old_dir):
            return moved
        ensure_private_dir(self.root)
        old_token = os.path.join(old_dir, ".machine.json")
        if os.path.isfile(old_token):
            data = read_json(old_token)
            if not os.path.exists(self.machine_file) and isinstance(data, dict) and isinstance(data.get("machineToken"), str):
                write_private_json(self.machine_file, data)
                moved["token"] = True
            _remove(old_token)   # served over HTTP: it goes either way
        ensure_private_dir(self.workflows_dir)
        for name in sorted(os.listdir(old_dir)):
            src = os.path.join(old_dir, name)
            if name.startswith(".tmp-") and os.path.isfile(src):
                _remove(src)   # a write cut short (it may hold a token): never leave it in a served folder
                continue
            if not name.endswith(".json") or name.startswith(".") or not os.path.isfile(src):
                continue
            dst = os.path.join(self.workflows_dir, name)
            if not os.path.exists(dst):
                try:
                    shutil.copyfile(src, dst)
                    try:
                        os.chmod(dst, 0o600)
                    except OSError:
                        pass
                    moved["workflows"] += 1
                except OSError:
                    continue   # left where it is; the next start tries again
            _remove(src)
        try:
            os.rmdir(old_dir)   # only if empty: anything else the user put there stays
        except OSError:
            pass
        return moved


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
