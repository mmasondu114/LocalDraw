"""Actual ComfyUI test: requires completed writing test and downloaded model."""

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))
from comic_workflow import engine as e
from comic_workflow.repository import Repository
from comic_workflow.comfyui import run_batch, drawing_state, select_image, api
from comic_workflow.layout import compose, default_layout

folder = root / "data/test-results/live"
repo = Repository(folder / "projects")
pid = (folder / "project-id.txt").read_text().strip()
report = {
    "status": "running",
    "source": "ComfyUI 真实生成；自动化只检查系统，不代替用户审美确认",
    "batches": [],
    "gpu_used_mib": [],
}
stop = threading.Event()


def monitor():
    while not stop.wait(2):
        try:
            report["gpu_used_mib"].append(
                int(
                    subprocess.check_output(
                        [
                            "nvidia-smi",
                            "--query-gpu=memory.used",
                            "--format=csv,noheader,nounits",
                        ],
                        text=True,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    ).strip()
                )
            )
        except Exception:
            pass


if __name__ == "__main__":
    threading.Thread(target=monitor, daemon=True).start()
    try:
        for i in range(1, 5):
            target = f"P001-K{i:02}"
            existing = [
                b
                for b in drawing_state(repo, pid)["batches"]
                if b["panel_id"] == target and b["status"] == "done"
            ]
            if existing:
                continue
            start = time.monotonic()
            batch = run_batch(repo, pid, target, lambda msg: print(msg, flush=True))
            report["batches"].append(
                {
                    "id": batch["id"],
                    "panel_id": target,
                    "seconds": round(time.monotonic() - start, 2),
                }
            )
            e.save(folder / "drawing-report.json", report)
        report["status"] = "generated_12_candidates_pending_visual_selection"
    except Exception as exc:
        report.update(status="failed", error=str(exc))
        raise
    finally:
        stop.set()
        report["peak_gpu_used_mib"] = max(report["gpu_used_mib"], default=None)
        e.save(folder / "drawing-report.json", report)
