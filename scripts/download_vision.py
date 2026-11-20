"""Download only the matching Qwen visual projector, with resume and SHA-256."""

import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))
from comic_workflow.downloader import download

if __name__ == "__main__":
    cfg = json.loads((root / "config/vision-model.json").read_text())
    download(
        root / cfg["directory"], 6, cfg["url"], cfg["size"], cfg["sha256"], cfg["name"]
    )
