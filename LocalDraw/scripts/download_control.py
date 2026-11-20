"""Pinned, resumable ControlNet download; never runs on studio startup."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from comic_workflow import engine as e
from comic_workflow.paths import ROOT
from comic_workflow.downloader import download

if __name__ == "__main__":
    cfg = e.read(ROOT / "config/control-model.json")
    download(
        ROOT / "models/controlnet",
        6,
        url=cfg["url"],
        size=cfg["size"],
        sha=cfg["sha256"],
        name=cfg["name"],
    )
