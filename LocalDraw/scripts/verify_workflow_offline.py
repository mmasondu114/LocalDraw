"""Exercise the real HTTP/job/storage pipeline with simulated model responses.

Uses a separate project copy. Never launches inference or alters the source work.
"""

import argparse
import copy
import hashlib
import json
import sys
import threading
import time
import urllib.request
from collections import Counter
from contextlib import ExitStack
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from PIL import Image
from comic_workflow import engine as e, comfyui, layout
from comic_workflow.character_binding import identity_hash
from comic_workflow.repository import Repository
from comic_workflow.server import Application, make_handler


def verify(project_id):
    source_repo = Repository()
    source_folder = source_repo.folder(project_id)
    source = source_repo.get(project_id)
    root = source_repo.root.parent / "test-results" / ("offline-flow-" + e.stamp())
    root.mkdir(parents=True)
    report = {"mode": "simulated inference; real HTTP/jobs/storage", "checks": {}}
    events = []

    def source_digest():
        digest = hashlib.sha256()
        for path in sorted(source_folder.rglob("*")):
            if path.is_file():
                digest.update(str(path.relative_to(source_folder)).encode())
                # Streaming keeps preservation checks bounded in RAM.
                with path.open("rb") as stream:
                    while chunk := stream.read(1024 * 1024):
                        digest.update(chunk)
        return digest.hexdigest()

    before = source_digest()
    repo = Repository(root / "projects")
    pid = "offline-copy"
    initial = copy.deepcopy(source)
    initial["pages"] = {}
    initial["drawing_prompts"] = {}
    e.save(repo.folder(pid) / "project.json", initial)

    def require(condition, description):
        report["checks"][description] = bool(condition)
        if not condition:
            raise AssertionError(description)

    with ExitStack() as stack:
        stack.enter_context(patch("comic_workflow.jobs.DATA", root))
        stack.enter_context(
            patch(
                "subprocess.Popen",
                side_effect=AssertionError(
                    "Offline verification cannot launch processes"
                ),
            )
        )
        stack.enter_context(
            patch.object(
                comfyui,
                "api",
                side_effect=AssertionError("Offline verification cannot call ComfyUI"),
            )
        )
        app = Application(repo)
        app.model = Mock()
        app.comfy = Mock()
        app.prepare_language = lambda **kw: events.append(
            "vision-load" if kw.get("vision") else "writing-load"
        )
        app.comfy.start.side_effect = lambda: events.append("drawing-load")

        def generated(p, stage, target, instruction, folder, update):
            events.append(stage + ":" + target)
            update(
                {
                    "runtime": {
                        "phase": "离线模拟",
                        "source": "fixture",
                        "completed": 1,
                        "total": 1,
                    }
                }
            )
            if stage == "audit":
                return {"verdict": "pass", "issues": [], "reviewed_scope": target}, {}
            return copy.deepcopy(source["pages"][target]), {"offline_fixture": True}

        def prepared(app, pid, target, jid, update):
            p = repo.get(pid)
            panel = next(
                x
                for x in p["pages"][target.split("-")[0]]["panels"]
                if x["id"] == target
            )
            p.setdefault("drawing_prompts", {})[target] = {
                "panel_hash": e.digest(panel),
                "identity_hash": identity_hash(p, panel),
                "payload": {"framing": {"offline_fixture": True}},
            }
            e.save(repo.folder(pid) / "project.json", p)

        draws = 0
        reviewed = Counter()

        def rendered(
            repo,
            pid,
            target,
            update,
            checkpoint,
            count,
            mode,
            loras,
            indices,
            repairs,
            review_required,
        ):
            nonlocal draws
            draws += 1
            events.append("draw:" + target + ":" + str(indices[0]))
            if draws == 1:
                raise TimeoutError("Injected single-image failure")
            panel = next(
                x
                for x in repo.get(pid)["pages"][target.split("-")[0]]["panels"]
                if x["id"] == target
            )
            bid = f"fixture-{draws:04}"
            image = {
                "id": bid + "-1",
                "file": bid + "/1.png",
                "view_index": indices[0] + 1,
                "positive": "OFFLINE TEST PLACEHOLDER",
                "review": {"verdict": "pending"},
            }
            folder = repo.folder(pid) / "images" / bid
            folder.mkdir(parents=True)
            Image.new("RGB", (64, 96), (40, 80 + draws % 140, 120)).save(
                folder / "1.png"
            )
            batch = {
                "id": bid,
                "panel_id": target,
                "panel_hash": e.digest(panel),
                "status": "done",
                "images": [image],
                "candidate_indices": indices,
            }
            index = comfyui.drawing_state(repo, pid)
            index["batches"].append(batch)
            e.save(repo.folder(pid) / "images/index.json", index)
            e.save(folder / "batch.json", batch)
            return batch

        def reviewed_image(panel, positive, image_path, *args):
            key = image_path.parent.name
            reviewed[key] += 1
            events.append("review:" + key)
            if key == "fixture-0003" and reviewed[key] == 1:
                raise ConnectionError("Injected review interruption")
            return {
                "verdict": "reject" if key == "fixture-0002" else "pass",
                "reason": "OFFLINE simulated verdict",
                "repair": "fixture correction",
            }

        stack.enter_context(
            patch("comic_workflow.storyboard_tools.generate", side_effect=generated)
        )
        stack.enter_context(
            patch("comic_workflow.drawing_tools.prepare", side_effect=prepared)
        )
        stack.enter_context(patch.object(comfyui, "run_batch", side_effect=rendered))
        stack.enter_context(
            patch("comic_workflow.creative.review_image", side_effect=reviewed_image)
        )
        http = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{http.server_port}"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        def request(path, body=None, binary=False):
            req = urllib.request.Request(
                base + path,
                data=json.dumps(body).encode() if body else None,
                headers={"X-Studio-Token": app.token},
            )
            with opener.open(req, timeout=30) as response:
                return response.read() if binary else json.load(response)

        def action(name, **kw):
            return request("/api/action", {"action": name, "project": pid, **kw})

        def finish(jid):
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                result = request("/api/job?id=" + jid)
                if result["status"] != "running":
                    require(
                        result["status"] == "done", "job completed: " + result["label"]
                    )
                    return result
                time.sleep(0.05)
            raise TimeoutError("Offline fixture timed out")

        try:
            require(
                request("/api/bootstrap")["token"] == app.token, "HTTP session works"
            )
            book = finish(action("storyboard_all", base_hash=e.digest(initial))["job"])
            require(
                repo.get(pid) == initial,
                "whole-book draft does not change confirmed content",
            )
            bundle = book["result"]["bundle"]
            action("accept_storyboard_bundle", bundle=bundle)
            p = repo.get(pid)
            require(
                p["pages"] == source["pages"],
                "all pages confirmed together with original dialogue",
            )
            drawing = finish(action("draw_all", base_hash=e.digest(p))["job"])
            first_review = next(
                i for i, event in enumerate(events) if event.startswith("review:")
            )
            total = sum(len(x["panels"]) for x in p["pages"].values()) * 16
            require(
                sum(x.startswith("draw:") for x in events[:first_review]) == total,
                "all initial slots processed before first review",
            )
            require(
                draws == total + 2, "only failed slot and rejected slot redrawn at tail"
            )
            require(
                reviewed["fixture-0003"] == 2,
                "review interruption retries same saved image",
            )
            require(
                not drawing["result"]["unresolved"],
                "16 accepted candidates per panel after retries",
            )
            require(
                events.count("drawing-load") == 2 and events.count("vision-load") == 2,
                "models loaded per phase, not per image",
            )
            index = comfyui.drawing_state(repo, pid)
            require(
                not index["selections"], "no automatic selection in drawing pipeline"
            )
            require(
                sum(
                    i["review"]["verdict"] == "reject"
                    for b in index["batches"]
                    for i in b["images"]
                )
                == 1,
                "rejected original retained as rejected",
            )
            for page in p["pages"].values():
                for panel in page["panels"]:
                    good = [
                        (b, i)
                        for b in index["batches"]
                        if b["panel_id"] == panel["id"]
                        for i in b["images"]
                        if i["review"]["verdict"] == "pass"
                    ]
                    require(len(good) == 16, "16 candidates: " + panel["id"])
                    b, image = good[0]
                    require(
                        request(
                            f"/api/image?id={pid}&bundle={b['id']}&file=1.png",
                            binary=True,
                        ).startswith(b"\x89PNG"),
                        "image endpoint: " + panel["id"],
                    )
                    action("select_image", target=panel["id"], image=image["id"])
                composed = action(
                    "compose", target=page["id"], layout=layout.default_layout(page)
                )
                require(
                    request(
                        f"/api/layout?id={pid}&bundle={composed['bundle']}&file=page.png",
                        binary=True,
                    ).startswith(b"\x89PNG"),
                    "page export: " + page["id"],
                )
            workspace = {
                "stage": "drawing",
                "target": "P006",
                "updated": time.time() * 1000,
                "views": {"drawing:": {"scroll": 1520, "instruction": "离线验收草稿"}},
            }
            action("save_workspace", state=workspace)
            require(
                request("/api/workspace?id=" + pid)["views"] == workspace["views"],
                "workspace roundtrip",
            )
            report.update(
                initial_slots=total,
                drawing_calls=draws,
                saved_images=sum(len(b["images"]) for b in index["batches"]),
                drawing_job=drawing["id"],
                storyboard_job=book["id"],
                pages=len(p["pages"]),
            )
        finally:
            http.shutdown()
            http.server_close()
            require(
                source_digest() == before, "all source project file bytes unchanged"
            )
            e.save(root / "events.json", events)
            e.save(root / "report.json", report)
    print(
        e.encoded(
            {
                "report": str(root / "report.json"),
                "passed": len(report["checks"]),
                "failed": [k for k, v in report["checks"].items() if not v],
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project")
    verify(parser.parse_args().project)
