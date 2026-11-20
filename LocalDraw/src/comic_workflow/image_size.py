"""Shared image sizing for direct prompts and comic panels."""

import math
import re

from . import engine as e

DEFAULT_SIZES = {
    "1:1": (1024, 1024), "2:3": (832, 1216), "3:2": (1216, 832),
    "9:16": (768, 1344), "16:9": (1344, 768),
}
RESOLUTIONS = ("default", "1024", "1536", "2048")


def validate_ratio(value):
    e.need(isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,3}:[1-9][0-9]{0,3}", value),
           "纵横比的宽和高须为 1–9999 的整数")
    width, height = map(int, value.split(":"))
    e.need(1 / 8 <= width / height <= 8, "纵横比须在 1:8 到 8:1 之间")
    return width, height


def validate_resolution(value):
    e.need(isinstance(value, str) and value in RESOLUTIONS, "请选择有效的分辨率")
    return value


def dimensions(ratio, resolution="default", *, alignment=8):
    width, height = validate_ratio(ratio)
    validate_resolution(resolution)
    divisor = math.gcd(width, height)
    normalized = f"{width // divisor}:{height // divisor}"
    if resolution == "default" and normalized in DEFAULT_SIZES:
        return DEFAULT_SIZES[normalized]
    longest = 1216 if resolution == "default" else int(resolution)
    scale = longest / max(width, height)
    # Round to the latent grid; use the same half-up rule as the browser.
    return tuple(int(math.floor(side * scale / alignment + 0.5)) * alignment for side in (width, height))


def settings(value):
    """Validate and snapshot the choices used by queued comic generation."""
    ratio = value.get("aspect_ratio", "storyboard")
    e.need(isinstance(ratio, str) and ratio in ("storyboard", *DEFAULT_SIZES), "请选择有效的漫画画格比例")
    return {"aspect_ratio": ratio, "resolution": validate_resolution(value.get("resolution", "default"))}
