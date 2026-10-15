"""Weekend Studio for ComfyUI — use your own ComfyUI workflows as generators in Weekend Studio.

Install from ComfyUI Manager, restart, and connect from the Weekend Studio tab. Plan and rounds:
reviews/comfyui-plan-2026-10-14.md in the Weekend Studio repository.
"""
from .weekend_studio.nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS

# The Weekend Studio tab and the right-click setting picker (web/weekend_studio.js).
WEB_DIRECTORY = "./web"

try:
    from server import PromptServer  # type: ignore  (ComfyUI)
except ImportError:  # imported outside ComfyUI (tests, tooling)
    PromptServer = None

if PromptServer is not None and getattr(PromptServer, "instance", None) is not None:
    from .weekend_studio import routes as _routes

    _routes.install(PromptServer.instance)

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
