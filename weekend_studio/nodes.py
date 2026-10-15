"""The three bridge nodes: where Weekend Studio's pixels enter a workflow and where the result leaves.

Everything else in a workflow is the user's own ComfyUI. These only exist where the app has to
supply or receive pixels; the run fills `weekend_ref` with a file it saved under
input/weekend-studio/<job>/ (runner.py). Each takes an optional `fallback` so the workflow still
runs inside ComfyUI on its own while it's being built.

Mask conventions, both stated on the nodes:
  Weekend Mask In   white = selected = the part to change
  Weekend Output    mask white = visible (becomes the layer's alpha)
"""
from __future__ import annotations

import os

import numpy as np
import torch
from PIL import Image, ImageOps

import folder_paths  # type: ignore  (ComfyUI)
import node_helpers  # type: ignore  (ComfyUI)

from .description import REF_INPUT
from .runner import INPUT_SUBFOLDER

CATEGORY = "Weekend Studio"


def _ref_path(ref: str) -> str:
    """The file a run saved, refusing anything outside input/weekend-studio/."""
    base = os.path.realpath(os.path.join(folder_paths.get_input_directory(), INPUT_SUBFOLDER))
    path = os.path.realpath(os.path.join(folder_paths.get_input_directory(), ref))
    if os.path.commonpath([base, path]) != base or not os.path.isfile(path):
        raise ValueError("Weekend Studio: the input picture for this run is missing")
    return path


def _open(ref: str):
    img = node_helpers.pillow(Image.open, _ref_path(ref))
    img = node_helpers.pillow(ImageOps.exif_transpose, img)
    if img.mode == "I":   # 16-bit PNG, as Load Image handles it
        img = img.point(lambda i: i * (1 / 255))
    return img


def _ref_widget():
    return ("STRING", {"default": "", "multiline": False,
                       "tooltip": "Filled in by Weekend Studio for each run. Leave empty."})


class WeekendImageIn:
    DESCRIPTION = "A picture from Weekend Studio: the generation area, the active layer or the whole canvas."

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "source": (["area", "layer", "canvas"], {"default": "area",
                           "tooltip": "What Weekend Studio sends: the generation area, the active layer, or the whole canvas."}),
                "label": ("STRING", {"default": "Image", "multiline": False,
                          "tooltip": "The name Weekend Studio shows for this picture."}),
            },
            "optional": {
                "fallback": ("IMAGE", {"tooltip": "Used when the workflow runs inside ComfyUI, so you can build and test it here."}),
                REF_INPUT: _ref_widget(),
            },
        }

    RETURN_TYPES = ("IMAGE", "MASK")
    RETURN_NAMES = ("image", "mask")
    FUNCTION = "load"
    CATEGORY = CATEGORY

    def load(self, source, label, fallback=None, weekend_ref=""):
        if weekend_ref:
            img = _open(weekend_ref)
            rgb = np.array(img.convert("RGB")).astype(np.float32) / 255.0
            image = torch.from_numpy(rgb)[None,]
            if "A" in img.getbands():   # same convention as Load Image: transparent = 1
                alpha = np.array(img.getchannel("A")).astype(np.float32) / 255.0
                mask = (1.0 - torch.from_numpy(alpha))[None,]
            else:
                mask = torch.zeros((1, rgb.shape[0], rgb.shape[1]), dtype=torch.float32)
            return (image, mask)
        if fallback is not None:
            return (fallback, torch.zeros((fallback.shape[0], fallback.shape[1], fallback.shape[2]), dtype=torch.float32))
        raise ValueError(f"Weekend Image In ({label}): no picture. It comes from Weekend Studio — "
                         "connect a fallback image to try the workflow here.")


class WeekendMaskIn:
    DESCRIPTION = "The selection from Weekend Studio as a mask. White = selected = the part to change."

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "label": ("STRING", {"default": "Mask", "multiline": False,
                          "tooltip": "The name Weekend Studio shows for this mask."}),
            },
            "optional": {
                "fallback": ("MASK", {"tooltip": "Used when the workflow runs inside ComfyUI."}),
                REF_INPUT: _ref_widget(),
            },
        }

    RETURN_TYPES = ("MASK",)
    RETURN_NAMES = ("mask",)
    FUNCTION = "load"
    CATEGORY = CATEGORY

    def load(self, label, fallback=None, weekend_ref=""):
        if weekend_ref:
            img = _open(weekend_ref)
            gray = np.array(img.convert("L")).astype(np.float32) / 255.0
            return (torch.from_numpy(gray)[None,],)
        if fallback is not None:
            return (fallback,)
        raise ValueError(f"Weekend Mask In ({label}): no mask. It comes from Weekend Studio — "
                         "connect a fallback mask to try the workflow here.")


class WeekendOutput:
    DESCRIPTION = "What comes back to Weekend Studio as the new layer. An optional mask becomes its transparency (white = visible)."

    def __init__(self):
        self.output_dir = folder_paths.get_temp_directory()
        self.type = "temp"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {"images": ("IMAGE",)},
            "optional": {"mask": ("MASK", {"tooltip": "White = visible. Becomes the layer's transparency."})},
        }

    RETURN_TYPES = ()
    FUNCTION = "save"
    OUTPUT_NODE = True
    CATEGORY = CATEGORY

    def save(self, images, mask=None):
        folder, filename, counter, subfolder, _ = folder_paths.get_save_image_path(
            "weekend-studio", self.output_dir, images[0].shape[1], images[0].shape[0])
        results = []
        for b, image in enumerate(images):
            pixels = np.clip(255.0 * image.cpu().numpy(), 0, 255).astype(np.uint8)
            img = Image.fromarray(pixels)
            if mask is not None:
                m = mask if mask.dim() == 2 else mask[min(b, mask.shape[0] - 1)]
                alpha = Image.fromarray(np.clip(255.0 * m.cpu().numpy(), 0, 255).astype(np.uint8))
                if alpha.size != img.size:
                    alpha = alpha.resize(img.size, Image.BILINEAR)
                img.putalpha(alpha)
            name = f"{filename}_{counter:05}_.png"
            # No workflow in the PNG's metadata (Save Image embeds it): these bytes leave the machine,
            # and the workflow is the user's own.
            img.save(os.path.join(folder, name), compress_level=4)
            results.append({"filename": name, "subfolder": subfolder, "type": self.type})
            counter += 1
        return {"ui": {"images": results}}


NODE_CLASS_MAPPINGS = {
    "WeekendImageIn": WeekendImageIn,
    "WeekendMaskIn": WeekendMaskIn,
    "WeekendOutput": WeekendOutput,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "WeekendImageIn": "Weekend Image In",
    "WeekendMaskIn": "Weekend Mask In",
    "WeekendOutput": "Weekend Output",
}
