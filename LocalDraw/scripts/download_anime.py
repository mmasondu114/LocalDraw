"""Explicit user-operated downloader; never imported by server startup."""

import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))
from comic_workflow.downloader import download

if __name__ == "__main__":
    cfg = json.loads((root / "config/anime-model.json").read_text())
    try:
        download(
            root / cfg["directory"],
            6,
            cfg["url"],
            cfg["size"],
            cfg["sha256"],
            cfg["name"],
        )
    except KeyboardInterrupt:
        print("Stopped. Completed parts retained.")
        sys.exit(130)
    except Exception as exc:
        print("Download failed:", exc)
        sys.exit(1)
