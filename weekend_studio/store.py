"""Published workflows, kept on the user's own machine (ComfyUI/user/default/weekend-studio/).

A record is what the user published — name, API-format graph, marks, times. The description is
built from it on demand against the CURRENT /object_info, so a node uninstalled since publishing
shows up as `missing` and a new LoRA shows up in the dropdown, without republishing."""
from __future__ import annotations

import json
import os
import re
import tempfile
import time

from .description import ID_RE, LIMITS, build_description

MAX_GRAPH_BYTES = 5 * 1024 * 1024
MAX_WORKFLOWS = 200


class StoreError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:56].strip("-")
    return s or "workflow"


def check_graph(graph) -> None:
    """An API-format graph: {node_id: {class_type, inputs}}. Not the UI format — the frontend
    exports API format with graphToPrompt()."""
    if not isinstance(graph, dict) or not graph:
        raise StoreError("bad-graph", "the workflow is empty or not in API format")
    for nid, node in graph.items():
        if not isinstance(nid, str) or not isinstance(node, dict) or not isinstance(node.get("class_type"), str) \
                or not isinstance(node.get("inputs"), dict):
            raise StoreError("bad-graph", "the workflow is not in API format (export it with “Save (API)”)")
    if len(json.dumps(graph)) > MAX_GRAPH_BYTES:
        raise StoreError("too-big", "the workflow is larger than 5 MB")


class WorkflowStore:
    def __init__(self, root: str):
        self.root = root

    def _path(self, workflow_id: str) -> str:
        if not ID_RE.match(workflow_id or ""):
            raise StoreError("bad-id", "not a workflow id")
        return os.path.join(self.root, f"{workflow_id}.json")

    def list(self) -> list:
        if not os.path.isdir(self.root):
            return []
        out = []
        for name in sorted(os.listdir(self.root)):
            if not name.endswith(".json"):
                continue
            try:
                rec = self.get(name[:-5])
            except StoreError:
                continue   # a broken file hides itself rather than breaking the list
            out.append(rec)
        out.sort(key=lambda r: ((r.get("name") or "").lower(), r["id"]))
        return out

    def get(self, workflow_id: str) -> dict:
        path = self._path(workflow_id)
        try:
            with open(path, "r", encoding="utf-8") as f:
                rec = json.load(f)
        except FileNotFoundError:
            raise StoreError("not-found", "no such workflow") from None
        except (OSError, ValueError):
            raise StoreError("unreadable", "the saved workflow can't be read") from None
        if not isinstance(rec, dict) or rec.get("id") != workflow_id:
            raise StoreError("unreadable", "the saved workflow can't be read")
        return rec

    def publish(self, name: str, graph: dict, marks=None, workflow_id: str | None = None) -> dict:
        name = (name or "").strip()[: LIMITS["name"]]
        if not name:
            raise StoreError("bad-name", "give the workflow a name")
        check_graph(graph)
        if marks is not None and not isinstance(marks, list):
            raise StoreError("bad-marks", "marks must be a list")
        now = int(time.time())
        if workflow_id:
            prev = self.get(workflow_id)
            created = prev.get("published_at", now)
        else:
            existing = {r["id"] for r in self.list()}
            if len(existing) >= MAX_WORKFLOWS:
                raise StoreError("too-many", f"at most {MAX_WORKFLOWS} published workflows")
            base = slugify(name)
            workflow_id, n = base, 2
            while workflow_id in existing:
                workflow_id = f"{base}-{n}"
                n += 1
            created = now
        rec = {"id": workflow_id, "name": name, "graph": graph, "marks": marks or [],
               "published_at": created, "updated_at": now}
        self._write(workflow_id, rec)
        return rec

    def remove(self, workflow_id: str) -> None:
        try:
            os.remove(self._path(workflow_id))
        except FileNotFoundError:
            raise StoreError("not-found", "no such workflow") from None

    def describe(self, rec: dict, object_info: dict) -> dict:
        return build_description(rec["id"], rec["name"], rec["graph"], object_info, rec.get("marks") or None)

    def _write(self, workflow_id: str, rec: dict) -> None:
        os.makedirs(self.root, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.root, prefix=".tmp-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(rec, f, indent=1)
            os.replace(tmp, self._path(workflow_id))   # never a half-written workflow
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
