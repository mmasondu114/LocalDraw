"""Pinned download catalog. Reading availability never downloads or loads a model."""

import json
from .paths import ROOT


def catalog():
    return json.loads((ROOT / "config/comparison-models.json").read_text(encoding="utf-8"))


def available():
    cfg = catalog()
    result = []
    for model in cfg["models"]:
        path = ROOT / cfg["directory"] / model["name"]
        result.append({**model, "installed": path.is_file() and path.stat().st_size == model["size"]})
    return result
