"""Running one job: {jobId, workflowId, version, values, images} → progress → output images.

The order is the security story, so it is fixed:
  1. the workflow must be published here, at the version the app's form was drawn from
  2. every value passes validate_values (types, ranges, dropdown options AS OF NOW)
  3. input images are fetched, sniffed (PNG/JPEG/WebP only), size-capped, and saved under
     ComfyUI's input folder in a folder of this job's own
  4. only then is the stored graph filled and queued on this ComfyUI

Transport-agnostic: the relay (round 2) and the signed-out direct server (round 6) both hand this
a job plus three callables — fetch an input's bytes, put an output's bytes, emit an event.
"""
from __future__ import annotations

import os
import re
import shutil

from .description import validate_values
from .graph import GraphError, fill_graph
from .store import StoreError

MAX_INPUT_BYTES = 40 * 1024 * 1024
MAX_OUTPUT_BYTES = 64 * 1024 * 1024
JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
INPUT_SUBFOLDER = "weekend-studio"


class RunError(Exception):
    """A run that can't go ahead, with a code the app can turn into a sentence.
    not-found · stale · missing-nodes · invalid · bad-image · comfy · canceled · no-output · busy"""

    def __init__(self, code: str, message: str, details=None):
        super().__init__(message)
        self.code = code
        self.details = details


def sniff_image(data: bytes):
    """The extension for PNG/JPEG/WebP bytes, else None. By content, never by a name we were told."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def mime_for(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower()
    return {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}.get(ext, "application/octet-stream")


class Runner:
    def __init__(self, store, client, input_dir: str, object_info=None):
        """
        store       WorkflowStore
        client      the ComfyUI client (comfy_client.ComfyClient, or a fake in tests):
                      object_info(), run(graph) async-iterates events, outputs(prompt_id),
                      view(image) -> bytes, cancel(prompt_id)
        input_dir   ComfyUI's input folder (folder_paths.get_input_directory())
        """
        self.store = store
        self.client = client
        self.input_dir = input_dir
        self._prompts = {}      # job id → ComfyUI prompt id, while running
        self._canceled = set()

    async def run(self, job: dict, *, fetch_input, put_output, emit) -> dict:
        job_id = job.get("jobId")
        if not isinstance(job_id, str) or not JOB_ID_RE.match(job_id):
            raise RunError("invalid", "bad job id")
        if job_id in self._prompts:
            raise RunError("busy", "this job is already running")
        self._prompts[job_id] = None
        job_dir = os.path.join(self.input_dir, INPUT_SUBFOLDER, job_id)
        try:
            return await self._run(job, job_id, job_dir, fetch_input, put_output, emit)
        finally:
            self._prompts.pop(job_id, None)
            self._canceled.discard(job_id)
            shutil.rmtree(job_dir, ignore_errors=True)

    async def _run(self, job, job_id, job_dir, fetch_input, put_output, emit):
        # 1. Published, at this version.
        try:
            rec = self.store.get(str(job.get("workflowId", "")))
        except StoreError as e:
            raise RunError("not-found", "this workflow isn't published on this machine any more") from e
        object_info = await self.client.object_info()
        desc = self.store.describe(rec, object_info)
        if job.get("version") != desc["version"]:
            raise RunError("stale", "this workflow changed in ComfyUI — reopen it to see its new settings")
        if desc.get("missing"):
            raise RunError("missing-nodes", "this workflow needs nodes that aren't installed", desc["missing"])
        if not desc["outputs"]:
            raise RunError("no-output", "this workflow has no Weekend Output, Save Image or Preview Image node")

        # 2. Every value, against the description as it is now.
        images = job.get("images") or []
        if not isinstance(images, list) or not all(isinstance(k, str) for k in images):
            raise RunError("invalid", "bad image list")
        checked = validate_values(desc, job.get("values"), images)
        if not checked["ok"]:
            raise RunError("invalid", "some settings aren't valid", checked["errors"])
        self._check_canceled(job_id)

        # 3. Input pixels: fetched, sniffed, capped, saved in this job's own folder.
        refs = {}
        if images:
            os.makedirs(job_dir, exist_ok=True)
        for i, key in enumerate(images):
            data = await fetch_input(key)
            if not isinstance(data, (bytes, bytearray)) or not data:
                raise RunError("bad-image", "an input image didn't arrive", [key])
            if len(data) > MAX_INPUT_BYTES:
                raise RunError("bad-image", "an input image is larger than 40 MB", [key])
            ext = sniff_image(bytes(data[:16]))
            if not ext:
                raise RunError("bad-image", "an input image isn't a PNG, JPEG or WebP", [key])
            name = f"in{i}.{ext}"
            with open(os.path.join(job_dir, name), "wb") as f:
                f.write(data)
            # LoadImage and the bridge nodes take a path relative to the input folder. (LoadImage
            # has VALIDATE_INPUTS, so ComfyUI skips its dropdown check for this value.)
            refs[key] = f"{INPUT_SUBFOLDER}/{job_id}/{name}"
        self._check_canceled(job_id)

        # 4. Fill and queue.
        try:
            graph = fill_graph(rec["graph"], desc, checked["values"], refs)
        except GraphError as e:
            raise RunError("stale", f"the saved workflow doesn't match its settings ({e})") from e

        prompt_id = None
        finished = False
        async for ev in self.client.run(graph):
            t = ev.get("type")
            if t == "queued":
                prompt_id = ev["prompt_id"]
                self._prompts[job_id] = prompt_id
                if job_id in self._canceled:
                    await self.client.cancel(prompt_id)
                await emit({"type": "queued", "position": ev.get("position")})
            elif t == "progress":
                await emit({"type": "progress", "value": ev.get("value"), "max": ev.get("max"), "node": ev.get("node")})
            elif t == "executing":
                await emit({"type": "executing", "node": ev.get("node")})
            elif t == "error":
                raise RunError("comfy", ev.get("message") or "ComfyUI stopped with an error",
                               {"node": ev.get("node"), "nodeType": ev.get("node_type")})
            elif t == "interrupted":
                raise RunError("canceled", "canceled")
            elif t == "done":
                finished = True
                break
        if not finished or prompt_id is None:
            raise RunError("comfy", "lost touch with ComfyUI before the run finished")

        # 5. Outputs, in the description's order.
        history = await self.client.outputs(prompt_id)
        count = 0
        for out in desc["outputs"]:
            for image in (history.get(out["node"]) or {}).get("images") or []:
                data = await self.client.view(image)
                if not data or len(data) > MAX_OUTPUT_BYTES:
                    continue
                await put_output(count, data, mime_for(image.get("filename", "")))
                count += 1
        if not count:
            raise RunError("no-output", "the workflow finished without an image")
        await emit({"type": "done", "outputs": count})
        return {"outputs": count, "promptId": prompt_id}

    async def cancel(self, job_id: str) -> bool:
        """Cancel a job: before it's queued it never starts; queued or running, ComfyUI drops it."""
        if job_id not in self._prompts:
            return False
        self._canceled.add(job_id)
        prompt_id = self._prompts.get(job_id)
        if prompt_id:
            await self.client.cancel(prompt_id)
        return True

    def _check_canceled(self, job_id):
        if job_id in self._canceled:
            raise RunError("canceled", "canceled")

