"""Download the two SDXL image-reference components with verified resume."""

from pathlib import Path
import sys
import json
import subprocess

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))
from comic_workflow.downloader import download  # noqa: E402 - standalone BAT entry


def install_nodes(cfg):
    destination = root / "runtime/custom_nodes/ComfyUI_IPAdapter_plus"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        subprocess.run(
            ["git", "clone", cfg["node_repository"], str(destination)], check=True
        )
        subprocess.run(
            ["git", "-C", str(destination), "checkout", cfg["node_revision"]],
            check=True,
        )
    revision = subprocess.check_output(
        ["git", "-C", str(destination), "rev-parse", "HEAD"], text=True
    ).strip()
    if revision != cfg["node_revision"]:
        raise RuntimeError("参考图节点版本与配置不符，保留现有目录，请检查后重试")


if __name__ == "__main__":
    cfg = json.loads(
        (root / "config/reference-models.json").read_text(encoding="utf-8")
    )
    install_nodes(cfg)
    for model in cfg["models"]:
        download(
            root / model["directory"],
            6,
            model["url"],
            model["size"],
            model["sha256"],
            model["name"],
        )
