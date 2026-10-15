"""The description: what a published workflow asks for, and what it gives back.

Built HERE, on the user's machine (we have the graph and ComfyUI's /object_info); read by the apps
to draw a form. validate_description and validate_values mirror packages/comfy/description.js rule
for rule — both run the shared cases in packages/comfy/fixtures/. Change one, change the other.

validate_values is the real gate: the app only ever sends {workflowId, version, values}, and
nothing is queued until every value has passed here. Custom nodes run arbitrary Python, so the
graph never comes from the app.

Pure standard library. No ComfyUI imports, so it runs in plain `python -m unittest`.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re

FORMAT_VERSION = 1

INPUT_TYPES = ("text", "int", "float", "seed", "bool", "choice", "image", "mask")
ROLES = ("prompt", "negative", "width", "height", "area", "layer", "canvas", "mask", "reference")
IMAGE_TYPES = ("image", "mask")

LIMITS = {
    "inputs": 200,
    "outputs": 16,
    "label": 80,
    "help": 400,
    "group": 40,
    "name": 80,
    "options": 2000,
    "option": 512,
    "text_default": 20_000,
    "text_max": 100_000,
}

# JSON numbers stop being exact in a browser past 2^53-1; ComfyUI seeds go to 2^64-1. Every
# integer's max is clamped to this so both sides agree on what a value can be.
MAX_SAFE = 2**53 - 1

KEY_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
NODE_RE = re.compile(r"^[A-Za-z0-9_:.-]{1,64}$")
HEX_RE = re.compile(r"^[0-9a-f]{8,64}$")

# The bridge nodes this pack adds (nodes.py). Always in a description, marked or not.
IMAGE_IN = "WeekendImageIn"
MASK_IN = "WeekendMaskIn"
OUTPUT = "WeekendOutput"
REF_INPUT = "weekend_ref"

# ── Zero-setup defaults ─────────────────────────────────────────────────────────────────────────
# When the user hasn't picked settings, these are the ones a workflow shows. Text prompts are found
# by tracing (below), not listed, so SDXL/Flux/custom encoders work without a table entry.
KNOWN = {
    "KSampler": ("seed", "steps", "cfg", "sampler_name", "scheduler", "denoise"),
    "KSamplerAdvanced": ("noise_seed", "steps", "cfg", "sampler_name", "scheduler"),
    "RandomNoise": ("noise_seed",),
    "KSamplerSelect": ("sampler_name",),
    "BasicScheduler": ("scheduler", "steps", "denoise"),
    "CFGGuider": ("cfg",),
    "FluxGuidance": ("guidance",),
    "EmptyLatentImage": ("width", "height"),
    "EmptySD3LatentImage": ("width", "height"),
}
SIZE_ROLES = {"width": "width", "height": "height"}
LABELS = {
    "seed": "Seed", "noise_seed": "Seed", "steps": "Steps", "cfg": "CFG", "sampler_name": "Sampler",
    "scheduler": "Scheduler", "denoise": "Denoise", "guidance": "Guidance", "width": "Width", "height": "Height",
}
SAVE_NODES = ("SaveImage", "PreviewImage")
CONDITIONING_SOURCES = (("positive", "positive"), ("negative", "negative"), ("conditioning", "positive"))


# ── Building ────────────────────────────────────────────────────────────────────────────────────

def is_link(value) -> bool:
    """ComfyUI API graphs write a wire as [node_id, output_slot]."""
    return isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and isinstance(value[1], int)


def node_order(node_id: str):
    return (0, int(node_id)) if node_id.isdigit() else (1, node_id)


def widget_spec(class_info: dict, name: str):
    """(type, options, cfg) for a widget input, or None for a wire-only input (MODEL, IMAGE…)."""
    for section in ("required", "optional"):
        spec = (class_info.get("input") or {}).get(section, {}).get(name)
        if spec is None:
            continue
        kind = spec[0] if spec else None
        cfg = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
        if isinstance(kind, list):
            return ("COMBO", kind, cfg)
        if kind == "COMBO":
            return ("COMBO", list(cfg.get("options") or []), cfg)
        if kind in ("INT", "FLOAT", "STRING", "BOOLEAN"):
            return (kind, None, cfg)
        return None
    return None


def make_input(node_id, input_name, class_info, current, label, *, group=None, role=None, help=None, overrides=None):
    """One description input from a widget, or None when the widget can't be offered."""
    spec = widget_spec(class_info, input_name)
    if spec is None or is_link(current):
        return None
    kind, options, cfg = spec
    o = overrides or {}
    inp = {"key": f"{node_id}.{input_name}", "node": node_id, "input": input_name, "label": label[: LIMITS["label"]]}

    if kind in ("INT", "FLOAT"):
        is_int = kind == "INT"
        lo = cfg.get("min", -MAX_SAFE if is_int else -1e9)
        hi = cfg.get("max", MAX_SAFE if is_int else 1e9)
        if is_int:
            lo, hi = max(int(lo), -MAX_SAFE), min(int(hi), MAX_SAFE)
        lo, hi = _narrow(lo, hi, o, is_int)
        seedish = is_int and (input_name in ("seed", "noise_seed") or cfg.get("control_after_generate"))
        inp["type"] = "seed" if seedish else ("int" if is_int else "float")
        inp["min"], inp["max"] = lo, hi
        step = o.get("step", cfg.get("step"))
        if _num(step) and step > 0:
            inp["step"] = int(step) if is_int and float(step).is_integer() else step
        dflt = current if _num(current) else cfg.get("default", lo)
        if is_int:
            dflt = int(dflt) if _num(dflt) and float(dflt).is_integer() else lo
        inp["default"] = min(hi, max(lo, dflt))
    elif kind == "STRING":
        inp["type"] = "text"
        if cfg.get("multiline"):
            inp["multiline"] = True
        inp["default"] = current if isinstance(current, str) else str(cfg.get("default", ""))
        inp["default"] = inp["default"][: LIMITS["text_default"]]
    elif kind == "BOOLEAN":
        inp["type"] = "bool"
        inp["default"] = current if isinstance(current, bool) else bool(cfg.get("default", False))
    else:  # COMBO
        opts = [x for x in (options or []) if (isinstance(x, str) and len(x) <= LIMITS["option"]) or _num(x)]
        opts = opts[: LIMITS["options"]]
        if not opts:
            return None
        inp["type"] = "choice"
        inp["options"] = opts
        inp["default"] = current if current in opts else opts[0]

    g = o.get("group", group)
    if g:
        inp["group"] = str(g)[: LIMITS["group"]]
    h = o.get("help", help)
    if h:
        inp["help"] = str(h)[: LIMITS["help"]]
    r = o.get("role", role)
    if r in ROLES:
        inp["role"] = r
    return inp


def _narrow(lo, hi, o, is_int):
    """A mark may tighten a range, never widen it past what the node accepts."""
    mlo, mhi = o.get("min"), o.get("max")
    if _num(mlo):
        lo = max(lo, int(mlo) if is_int else mlo)
    if _num(mhi):
        hi = min(hi, int(mhi) if is_int else mhi)
    if lo > hi:
        lo = hi
    return lo, hi


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _title(graph, node_id, object_info):
    """The node's title when the user renamed it, else None."""
    node = graph[node_id]
    title = ((node.get("_meta") or {}).get("title") or "").strip()
    info = object_info.get(node.get("class_type"), {})
    if title and title not in (info.get("display_name"), info.get("name"), node.get("class_type")):
        return title
    return None


def text_encoders(graph, object_info):
    """Prompt text widgets, traced back from the samplers/guiders: [(node_id, input, side)].

    A text encoder is any node that outputs CONDITIONING and has a multiline STRING widget
    (CLIPTextEncode, the SDXL/Flux encoders, most custom ones). Positive is traced before negative,
    so the first positive text is the prompt."""
    def is_encoder(nid):
        info = object_info.get(graph[nid].get("class_type"), {})
        return "CONDITIONING" in (info.get("output") or []) and bool(_multiline_texts(graph[nid], info))

    def upstream_conditioning(nid):
        for value in (graph[nid].get("inputs") or {}).values():
            if not is_link(value) or value[0] not in graph:
                continue
            src_info = object_info.get(graph[value[0]].get("class_type"), {})
            outs = src_info.get("output") or []
            if value[1] < len(outs) and outs[value[1]] == "CONDITIONING":
                yield value[0]

    found, seen_enc = [], set()
    for side_wanted in ("positive", "negative"):
        for nid in sorted(graph, key=node_order):
            inputs = graph[nid].get("inputs") or {}
            for name, side in CONDITIONING_SOURCES:
                if side != side_wanted or not is_link(inputs.get(name)):
                    continue
                stack, visited = [inputs[name][0]], set()
                while stack and len(visited) < 64:
                    cur = stack.pop(0)
                    if cur in visited or cur not in graph:
                        continue
                    visited.add(cur)
                    if is_encoder(cur):
                        if cur not in seen_enc:
                            seen_enc.add(cur)
                            info = object_info.get(graph[cur]["class_type"], {})
                            for text_input in _multiline_texts(graph[cur], info):
                                found.append((cur, text_input, side))
                        continue
                    stack.extend(upstream_conditioning(cur))
    if not found:  # no sampler wired the usual way: every encoder, in node order, is positive
        for nid in sorted(graph, key=node_order):
            if is_encoder(nid):
                info = object_info.get(graph[nid]["class_type"], {})
                found.extend((nid, t, "positive") for t in _multiline_texts(graph[nid], info))
    return found


def _multiline_texts(node, info):
    out = []
    for name, value in (node.get("inputs") or {}).items():
        spec = widget_spec(info, name)
        if spec and spec[0] == "STRING" and spec[2].get("multiline") and not is_link(value):
            out.append(name)
    return out


def default_inputs(graph, object_info):
    """The settings a workflow shows when the user hasn't picked any."""
    out = []
    # Prompts.
    prompts = text_encoders(graph, object_info)
    pos = [e for e in prompts if e[2] == "positive"]
    neg = [e for e in prompts if e[2] == "negative"]
    for i, (nid, name, _side) in enumerate(pos):
        label = _title(graph, nid, object_info) or ("Prompt" if i == 0 else f"Prompt {i + 1}")
        if name not in ("text",):
            label = f"{label} ({name})"
        inp = make_input(nid, name, object_info[graph[nid]["class_type"]], graph[nid]["inputs"][name], label,
                         role="prompt" if i == 0 else None)
        if inp:
            out.append(inp)
    for i, (nid, name, _side) in enumerate(neg):
        label = _title(graph, nid, object_info) or ("Negative prompt" if i == 0 else f"Negative prompt {i + 1}")
        inp = make_input(nid, name, object_info[graph[nid]["class_type"]], graph[nid]["inputs"][name], label,
                         role="negative" if i == 0 else None)
        if inp:
            out.append(inp)

    # LoadImage: the area when the workflow has no Weekend Image In, references after that.
    has_bridge = any(n.get("class_type") == IMAGE_IN for n in graph.values())
    loads = [nid for nid in sorted(graph, key=node_order) if graph[nid].get("class_type") == "LoadImage"]
    for i, nid in enumerate(loads):
        area = i == 0 and not has_bridge
        n = i + 1 if has_bridge else i
        label = _title(graph, nid, object_info) or ("Image" if area else f"Reference {n}")
        out.append({"key": f"{nid}.image", "node": nid, "input": "image", "label": label,
                    "type": "image", "role": "area" if area else "reference"})

    # Sizes, then sampler settings.
    for group, wanted in (("Size", ("EmptyLatentImage", "EmptySD3LatentImage")), ("Sampler", None)):
        for nid in sorted(graph, key=node_order):
            cls = graph[nid].get("class_type")
            if cls not in KNOWN or (wanted and cls not in wanted) or (not wanted and cls in ("EmptyLatentImage", "EmptySD3LatentImage")):
                continue
            for name in KNOWN[cls]:
                if name not in (graph[nid].get("inputs") or {}):
                    continue
                inp = make_input(nid, name, object_info[cls], graph[nid]["inputs"][name], LABELS.get(name, name),
                                 group=group, role=SIZE_ROLES.get(name) if group == "Size" else None)
                if inp:
                    out.append(inp)
    return out


def bridge_inputs(graph, object_info):
    """Weekend Image In / Mask In: pixels the app supplies. Always present."""
    out = []
    for nid in sorted(graph, key=node_order):
        node = graph[nid]
        cls = node.get("class_type")
        if cls not in (IMAGE_IN, MASK_IN):
            continue
        inputs = node.get("inputs") or {}
        label = inputs.get("label") if isinstance(inputs.get("label"), str) and inputs.get("label").strip() else None
        if cls == IMAGE_IN:
            source = inputs.get("source") if inputs.get("source") in ("area", "layer", "canvas") else "area"
            inp = {"type": "image", "role": source, "label": (label or "Image")[: LIMITS["label"]]}
        else:
            inp = {"type": "mask", "role": "mask", "label": (label or "Mask")[: LIMITS["label"]]}
        inp = {"key": f"{nid}.{REF_INPUT}", "node": nid, "input": REF_INPUT, **inp}
        if is_link(inputs.get("fallback")):
            inp["optional"] = True   # runs on its own fallback when the app sends nothing
        out.append(inp)
    return out


def marked_inputs(graph, object_info, marks):
    """The settings the user ticked in ComfyUI. A mark for a wire, a missing node or an unknown
    widget is skipped, not an error: a workflow edited after marking still publishes."""
    out = []
    for m in marks:
        if not isinstance(m, dict):
            continue
        nid, name = str(m.get("node", "")), str(m.get("input", ""))
        node = graph.get(nid)
        if not node or name not in (node.get("inputs") or {}):
            continue
        cls = node.get("class_type")
        if cls in (IMAGE_IN, MASK_IN) or cls not in object_info:
            continue
        if cls == "LoadImage" and name == "image":
            label = str(m.get("label") or _title(graph, nid, object_info) or "Image")
            role = m.get("role") if m.get("role") in ("area", "layer", "canvas", "reference") else "reference"
            out.append({"key": f"{nid}.image", "node": nid, "input": "image", "label": label[: LIMITS["label"]],
                        "type": "image", "role": role})
            continue
        default_label = LABELS.get(name) or name.replace("_", " ").capitalize()
        title = _title(graph, nid, object_info)
        label = str(m.get("label") or (f"{title}: {default_label}" if title else default_label))
        inp = make_input(nid, name, object_info[cls], node["inputs"][name], label, overrides=m)
        if inp:
            out.append(inp)
    return out


def outputs_of(graph):
    for classes in ((OUTPUT,), SAVE_NODES[:1], SAVE_NODES[1:]):
        found = [nid for nid in sorted(graph, key=node_order) if graph[nid].get("class_type") in classes]
        if found:
            return [{"node": nid, "kind": "image"} for nid in found[: LIMITS["outputs"]]]
    return []


def dedupe_labels(inputs, graph):
    counts = {}
    for inp in inputs:
        counts[inp["label"]] = counts.get(inp["label"], 0) + 1
    for inp in inputs:
        if counts[inp["label"]] > 1:
            inp["label"] = f"{inp['label']} ({inp['node']})"[: LIMITS["label"]]
    keys = set()
    unique = []
    for inp in inputs:
        if inp["key"] in keys:
            continue
        keys.add(inp["key"])
        unique.append(inp)
    return unique


def version_of(name: str, graph: dict, marks) -> str:
    """Hash of what the user PUBLISHED (graph, marks, name) — not of /object_info, so installing a
    new LoRA doesn't make every open form stale. Option lists are checked per run instead."""
    blob = json.dumps({"name": name, "graph": graph, "marks": marks or []}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:20]


def build_description(workflow_id: str, name: str, graph: dict, object_info: dict, marks=None) -> dict:
    """The description for one published workflow, against the ComfyUI it runs on right now."""
    graph = copy.deepcopy(graph)
    missing = sorted({n.get("class_type") for n in graph.values() if n.get("class_type") not in object_info})
    known = {nid: n for nid, n in graph.items() if n.get("class_type") in object_info}

    picked = marked_inputs(known, object_info, marks) if marks else []
    chosen = picked if picked else default_inputs(known, object_info)
    inputs = bridge_inputs(known, object_info) + chosen
    # Prompts first, then images, then everything else — the order a form reads best in.
    rank = {"prompt": 0, "negative": 1}
    inputs.sort(key=lambda i: (rank.get(i.get("role"), 3 if i["type"] not in IMAGE_TYPES else 2)))
    inputs = dedupe_labels(inputs, graph)[: LIMITS["inputs"]]

    d = {
        "format": FORMAT_VERSION,
        "id": workflow_id,
        "name": name[: LIMITS["name"]],
        "version": version_of(name, graph, marks),
        "inputs": inputs,
        "outputs": outputs_of(known),
    }
    if missing:
        d["missing"] = missing
    return d


# ── Checking (mirrors packages/comfy/description.js) ────────────────────────────────────────────

def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and -MAX_SAFE <= v <= MAX_SAFE


def _is_float(v) -> bool:
    return _num(v)


def _int_valued(v) -> bool:
    """JS has one number type: 20.0 IS 20 there. Accept a float holding an integer."""
    if _is_int(v):
        return True
    return isinstance(v, float) and math.isfinite(v) and v.is_integer() and abs(v) <= MAX_SAFE


def _str(v, limit) -> bool:
    return isinstance(v, str) and len(v) <= limit


def _text_max(inp):
    return inp.get("maxLength", LIMITS["text_default"])


def validate_description(d) -> dict:
    errors = []

    def err(path, msg):
        errors.append(f"{path}: {msg}")

    if not isinstance(d, dict):
        return {"ok": False, "errors": ["description: not an object"]}
    if d.get("format") != FORMAT_VERSION:
        err("format", f"expected {FORMAT_VERSION}")
    if not isinstance(d.get("id"), str) or not ID_RE.match(d["id"]):
        err("id", "not a slug")
    if not _str(d.get("name"), LIMITS["name"]) or not d["name"].strip():
        err("name", "missing or too long")
    if not isinstance(d.get("version"), str) or not HEX_RE.match(d["version"]):
        err("version", "not a hex hash")
    if "missing" in d and (not isinstance(d["missing"], list) or not all(_str(m, 200) for m in d["missing"])):
        err("missing", "not a list of node names")

    inputs = d.get("inputs")
    if not isinstance(inputs, list):
        err("inputs", "not a list")
    else:
        if len(inputs) > LIMITS["inputs"]:
            err("inputs", f"more than {LIMITS['inputs']}")
        seen = set()
        for i, inp in enumerate(inputs):
            p = f"inputs[{i}]"
            if not isinstance(inp, dict):
                err(p, "not an object")
                continue
            key = inp.get("key")
            if not isinstance(key, str) or not KEY_RE.match(key):
                err(f"{p}.key", "bad key")
            elif key in seen:
                err(f"{p}.key", "duplicate")
            else:
                seen.add(key)
            if not isinstance(inp.get("node"), str) or not NODE_RE.match(inp["node"]):
                err(f"{p}.node", "bad node id")
            if not isinstance(inp.get("input"), str) or not KEY_RE.match(inp["input"]):
                err(f"{p}.input", "bad input name")
            if inp.get("type") not in INPUT_TYPES:
                err(f"{p}.type", "unknown type")
            if not _str(inp.get("label"), LIMITS["label"]) or not inp["label"].strip():
                err(f"{p}.label", "missing or too long")
            if "group" in inp and not _str(inp["group"], LIMITS["group"]):
                err(f"{p}.group", "too long")
            if "help" in inp and not _str(inp["help"], LIMITS["help"]):
                err(f"{p}.help", "too long")
            if "role" in inp and inp["role"] not in ROLES:
                err(f"{p}.role", "unknown role")
            if "optional" in inp and not isinstance(inp["optional"], bool):
                err(f"{p}.optional", "not a boolean")
            _check_type_fields(inp, p, err)

    outputs = d.get("outputs")
    if not isinstance(outputs, list):
        err("outputs", "not a list")
    else:
        if len(outputs) > LIMITS["outputs"]:
            err("outputs", f"more than {LIMITS['outputs']}")
        for i, o in enumerate(outputs):
            if not isinstance(o, dict) or not isinstance(o.get("node"), str) or not NODE_RE.match(o["node"]) or o.get("kind") != "image":
                err(f"outputs[{i}]", "bad output")
    return {"ok": not errors, "errors": errors}


def _check_type_fields(inp, p, err):
    t = inp.get("type")
    if t in ("int", "seed", "float"):
        num = _is_float if t == "float" else _int_valued
        lo, hi, dflt = inp.get("min"), inp.get("max"), inp.get("default")
        if not num(lo) or not num(hi) or lo > hi:
            err(f"{p}.min/max", "bad range")
        elif not num(dflt) or dflt < lo or dflt > hi:
            err(f"{p}.default", "outside range")
        if "step" in inp and (not _num(inp["step"]) or inp["step"] <= 0):
            err(f"{p}.step", "not positive")
    elif t == "text":
        if not isinstance(inp.get("default"), str):
            err(f"{p}.default", "not a string")
        if "maxLength" in inp and (not _int_valued(inp["maxLength"]) or inp["maxLength"] < 1 or inp["maxLength"] > LIMITS["text_max"]):
            err(f"{p}.maxLength", "bad length")
        elif isinstance(inp.get("default"), str) and len(inp["default"]) > _text_max(inp):
            err(f"{p}.default", "too long")
        if "multiline" in inp and not isinstance(inp["multiline"], bool):
            err(f"{p}.multiline", "not a boolean")
    elif t == "bool":
        if not isinstance(inp.get("default"), bool):
            err(f"{p}.default", "not a boolean")
    elif t == "choice":
        opts = inp.get("options")
        if not isinstance(opts, list) or not 1 <= len(opts) <= LIMITS["options"]:
            err(f"{p}.options", "bad list")
        elif not all(_str(o, LIMITS["option"]) or _num(o) for o in opts):
            err(f"{p}.options", "bad entry")
        elif not _in_options(inp.get("default"), opts):
            err(f"{p}.default", "not an option")


def _in_options(v, opts) -> bool:
    """`in`, but True != 1 and "1" != 1 — JS's includes() keeps them apart, so must we."""
    if isinstance(v, str):
        return any(isinstance(o, str) and o == v for o in opts)
    if _num(v):
        return any(_num(o) and o == v for o in opts)
    return False


def validate_values(description: dict, values=None, images=()) -> dict:
    """{ok, values (every non-image input, defaults filled), errors: [{key, code}]}.
    Codes: unknown · type · range · choice · too-long · image-missing. No coercion."""
    values = values if isinstance(values, dict) else {}
    errors, out = [], {}
    by_key = {inp["key"]: inp for inp in description["inputs"]}

    for key in values:
        inp = by_key.get(key)
        if inp is None:
            errors.append({"key": key, "code": "unknown"})
        elif inp["type"] in IMAGE_TYPES:
            errors.append({"key": key, "code": "type"})
    supplied = set()
    for key in images:
        inp = by_key.get(key)
        if inp is None or inp["type"] not in IMAGE_TYPES:
            errors.append({"key": key, "code": "unknown"})
        else:
            supplied.add(key)

    for inp in description["inputs"]:
        if inp["type"] in IMAGE_TYPES:
            if inp["key"] not in supplied and not inp.get("optional"):
                errors.append({"key": inp["key"], "code": "image-missing"})
            continue
        v = values[inp["key"]] if inp["key"] in values else inp.get("default")
        code = _check_value(inp, v)
        if code:
            errors.append({"key": inp["key"], "code": code})
        else:
            out[inp["key"]] = int(v) if inp["type"] in ("int", "seed") else v
    return {"ok": not errors, "values": out, "errors": errors}


def _check_value(inp, v):
    t = inp["type"]
    if t == "text":
        if not isinstance(v, str):
            return "type"
        return "too-long" if len(v) > _text_max(inp) else None
    if t in ("int", "seed"):
        if not _int_valued(v):
            return "type"
        return "range" if v < inp["min"] or v > inp["max"] else None
    if t == "float":
        if not _is_float(v):
            return "type"
        return "range" if v < inp["min"] or v > inp["max"] else None
    if t == "bool":
        return None if isinstance(v, bool) else "type"
    if t == "choice":
        return None if _in_options(v, inp["options"]) else "choice"
    return "type"
