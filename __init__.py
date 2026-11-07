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

# Registers the tab's /weekend-studio/* routes on ComfyUI's own server. That is all an import does
# (0.3.0): no second server and no outbound connection start here. The relay connection starts when
# the user clicks Connect in the tab; same-computer mode when they turn it on there. See README.md,
# "Network and data".
if PromptServer is not None and getattr(PromptServer, "instance", None) is not None:
    from .weekend_studio import routes as _routes

    _routes.install(PromptServer.instance)

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
