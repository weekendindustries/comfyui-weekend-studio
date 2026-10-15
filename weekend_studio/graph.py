"""Filling a published graph with one run's values. The graph is the user's own, stored on their
machine; the values arrived from the app and have ALREADY passed validate_values."""
from __future__ import annotations

import copy

from .description import IMAGE_TYPES, REF_INPUT, is_link


class GraphError(Exception):
    """The stored graph no longer matches its description (edited by hand, half-written)."""


def fill_graph(graph: dict, description: dict, values: dict, image_refs: dict) -> dict:
    """A copy of `graph` with every described input set.

    values      {key: value} — validate_values' normalized output (defaults already filled)
    image_refs  {key: "weekend-studio/<job>/<file>"} — inputs saved into ComfyUI's input folder
    """
    g = copy.deepcopy(graph)
    for inp in description["inputs"]:
        node = g.get(inp["node"])
        if not isinstance(node, dict) or not isinstance(node.get("inputs"), dict):
            raise GraphError(f"node {inp['node']} is gone")
        inputs = node["inputs"]
        if inp["type"] in IMAGE_TYPES:
            ref = image_refs.get(inp["key"])
            if ref:
                inputs[inp["input"]] = ref
            elif inp["input"] == REF_INPUT:
                inputs[REF_INPUT] = ""   # the bridge node runs on its fallback
            continue
        if is_link(inputs.get(inp["input"])):
            raise GraphError(f"{inp['key']} is wired, not a setting")
        inputs[inp["input"]] = values[inp["key"]]
    return g
