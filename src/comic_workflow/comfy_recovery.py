"""Reconcile only this project's saved Comfy submissions before retrying."""

import copy
import time
import urllib.parse
import urllib.request
from PIL import Image
from . import engine as e, comfyui
from .repository import LOCK


def recover_batch(repo, pid, batch, cancel_running=False):
    batch = copy.deepcopy(batch)
    folder = repo.folder(pid) / "images" / batch["id"]
    job = batch.get("active_prompt")
    queue = comfyui.api("/queue")
    entries = queue.get("queue_running", []) + queue.get("queue_pending", [])
    detail = batch.get("active_hand_detail")
    if detail and detail.get("status") in {"running", "uncertain"}:
        passes = detail.get("passes", [])
        state = passes[-1] if passes else {}
        hand_job = state.get("prompt_id")
        if not hand_job and state.get("submission_pending"):
            owned = [x for x in entries if len(x) > 3 and x[3].get("client_id") == state.get("client_id")]
            recent = comfyui.api("/history?max_items=200")
            matching = [key for key, record in recent.items() if len(record.get("prompt", [])) > 3
                        and record["prompt"][3].get("client_id") == state.get("client_id")]
            hand_job = owned[-1][1] if owned else matching[-1] if matching else None
            if not hand_job:
                return batch
        if hand_job:
            hand_record = comfyui.api("/history/" + hand_job).get(hand_job)
            hand_running = any(x[1] == hand_job for x in entries)
            if not hand_record and hand_running:
                if cancel_running:
                    comfyui.api("/queue", {"delete": [hand_job]})
                    comfyui.api("/interrupt", {"prompt_id": hand_job})
                return batch
            if hand_record:
                e.save(folder / "recovered-hand-history.json", hand_record)
        # Recovery deliberately retains the base image; it never submits another detail pass.
        detail.update(status="failed", error="任务已核对结束，恢复时保留增强前原图，未自动重画")
        batch.get("active_candidate", {})["hand_detail"] = copy.deepcopy(detail)
        for candidate in batch.get("images", []):
            if candidate["id"] == batch.get("active_candidate", {}).get("id"):
                candidate["hand_detail"] = copy.deepcopy(detail)
    if not job:
        owned = [
            x for x in entries if len(x) > 3 and x[3].get("client_id") == batch["id"]
        ]
        if owned:
            job = owned[-1][1]
        else:
            recent = comfyui.api("/history?max_items=200")
            match = [
                (key, value)
                for key, value in recent.items()
                if len(value.get("prompt", [])) > 3
                and value["prompt"][3].get("client_id") == batch["id"]
            ]
            if match:
                job = match[-1][0]
    if not job:
        return batch
    batch["active_prompt"] = job
    record = comfyui.api("/history/" + job).get(job)
    running = any(x[1] == job for x in entries)
    if not record and running and cancel_running:
        # The installed server supports prompt-specific interruption. Never use a global interrupt.
        comfyui.api("/queue", {"delete": [job]})
        comfyui.api("/interrupt", {"prompt_id": job})
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            record = comfyui.api("/history/" + job).get(job)
            queue = comfyui.api("/queue")
            running = any(
                x[1] == job
                for x in queue.get("queue_running", []) + queue.get("queue_pending", [])
            )
            if record or not running:
                break
            time.sleep(0.25)
    if not record and running:
        return batch
    if record:
        e.save(folder / "recovered-history.json", record)
    if record and record.get("status", {}).get("status_str") != "error":
        descriptors = [
            image
            for output in record.get("outputs", {}).values()
            for image in output.get("images", [])
        ]
        candidate = batch.get("active_candidate")
        if not descriptors or not candidate:
            # Old versions may lack the metadata required to import the exact result.
            return batch
        if not any(i["id"] == candidate["id"] for i in batch["images"]):
            query = urllib.parse.urlencode(descriptors[0])
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(
                comfyui.config()["endpoint"] + "/view?" + query, timeout=30
            ) as response:
                data = response.read(40_000_001)
            e.need(len(data) <= 40_000_000, "恢复图片过大")
            file = repo.folder(pid) / "images" / candidate["file"]
            file.write_bytes(data)
            with Image.open(file) as image:
                image.verify()
            batch["images"].append({**candidate, "prompt_id": job})
        batch["status"] = "done"
        batch["recovered"] = True
    else:
        # Known prompt no longer in queue and no successful result: safe to retry that slot.
        batch["status"] = "failed"
        batch["error"] = "已核对原任务停止或执行失败，允许在队尾重试"
    batch["submission_pending"] = False
    with LOCK:
        index = comfyui.drawing_state(repo, pid)
        index["batches"] = [
            batch if b["id"] == batch["id"] else b for b in index["batches"]
        ]
        e.save(repo.folder(pid) / "images/index.json", index)
        e.save(folder / "batch.json", batch)
    return batch
