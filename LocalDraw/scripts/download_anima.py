"""Download the pinned Anima 3.8B v1.1 bundle and encoders with SHA256 verification."""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from comic_workflow.downloader import download

if __name__ == "__main__":
    log = (ROOT / "data/anima-migration/model-download.log").open("a", encoding="utf-8", buffering=1)
    sys.stdout = log
    sys.stderr = log
    manifest = json.loads((ROOT / "config/anima-model.json").read_text(encoding="utf-8"))
    for f in manifest["files"]:
        for attempt in range(5):
            try:
                download(ROOT / f["directory"], 6, f["url"].replace("huggingface.co", "hf-mirror.com"),
                         f["size"], f["sha256"], f["name"], fallback_urls=[f["url"]], timeout=30)
                break
            except Exception as exc:
                print("Retrying component:", type(exc).__name__, flush=True)
                if attempt == 4:
                    raise
                time.sleep(5)
    print("All Anima components verified.", flush=True)
