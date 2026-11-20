"""Resolve existing model files for read-only inspection and inference."""
import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=16)
def models(root):
    installation = root / "config/installed-runtime.json"
    if not installation.is_file():
        return root / "models"
    return root / Path(json.loads(installation.read_text(encoding="utf-8-sig"))["models"])


def read_path(root, value):
    """Resolve a configured asset path without redirecting any download or write."""
    path = Path(value)
    if path.parts and path.parts[0] == "models":
        return models(root).joinpath(*path.parts[1:])
    return root / path
