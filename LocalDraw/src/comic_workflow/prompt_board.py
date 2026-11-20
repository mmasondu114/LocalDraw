"""Storyboard ownership for prompt-generated candidates and composed pages."""
import json
import re
import uuid

from . import comfyui, direct_imports, engine as e, layout, quick_sample
from .image_size import validate_ratio
from .repository import LOCK, safe_id


def create(app, request):
    title, text = request.get("title"), request.get("storyboard")
    e.need(isinstance(title, str) and 0 < len(title.strip()) <= 120, "请填写 1–120 字的标题")
    e.need(isinstance(text, str) and 0 < len(text.strip()) <= 200000, "请填写创作分镜，最多 20 万字")
    if text.lstrip().startswith(("[", "{")):
        document = json.loads(text)
        items = document.get("panels", [document]) if isinstance(document, dict) else document
    else:
        items = []
        for line in text.strip().splitlines():
            if not line.strip():
                continue
            match = re.match(r"^\s*(P\d+\s*[- ]\s*K\d+)\s*(?:[|｜:：]\s*|\s+)(.*)$", line, re.I)
            description = match[2].strip() if match else line.strip()
            ratio = re.match(r"^(\d+[:：]\d+)(?:\s*[|｜]\s*|\s+)(.+)$", description)
            items.append({"id": match[1] if match else None, "aspect_ratio": ratio[1].replace("：", ":") if ratio else "3:2",
                          "description": ratio[2] if ratio else description})
    e.need(isinstance(items, list) and 1 <= len(items) <= 1000, "请填写 1–1000 格分镜")
    rows = []
    page, number = 1, 0
    for i, item in enumerate(items, 1):
        e.need(isinstance(item, dict), f"第 {i} 格需要编号和分镜内容")
        raw_id = item.get("id") or item.get("panel_id")
        if raw_id is not None:
            match = re.fullmatch(r"P(\d{1,3})\s*[- ]\s*K(\d{1,2})", str(raw_id).strip(), re.I)
            e.need(match, f"第 {i} 条编号无效，例如 P001-K01")
            pair = tuple(map(int, match.groups()))
            e.need(pair == (page, number + 1) or (number and pair == (page + 1, 1)), f"{raw_id} 编号重复或跳号，请按页格顺序填写")
            page, number = pair
        else:
            if number == 9:
                page, number = page + 1, 0
            number += 1
        e.need(number <= 9, "每页最多 9 格，请另起一页")
        desc = item.get("description") or item.get("drawing_description") or item.get("main_instant")
        e.need(isinstance(desc, str) and desc.strip(), f"第 {i} 格缺少分镜内容")
        ratio = item.get("aspect_ratio", "3:2")
        validate_ratio(ratio)
        rows.append({"source": f"P{page:03}-K{number:02}", "description": desc.strip(), "aspect_ratio": ratio})
    with LOCK:
        return direct_imports.save_panel_work(app.repo, app.characters, rows, request.get("import_key"),
                                              title.strip(), e.digest([title.strip(), rows]), board=True)


def sample_input(repo, pid, request):
    if request.get("board_input"):
        return e.read(repo.folder(pid) / "prompt-inputs" / (safe_id(request["board_input"]) + ".json"))
    return repo.get(pid)


def submit(app, pid, request, queued):
    project = app.repo.get(pid)
    e.need(project.get("prompt_board"), "请先创建提示词分镜作品")
    target = request.get("target")
    panel = next((p for page in project["pages"].values() for p in page["panels"] if p["id"] == target), None)
    e.need(panel, "粘贴的提示词编号不属于本作品，请核对页格编号")
    ratio = request.get("aspect_ratio", "storyboard")
    sample = quick_sample.prepare_sample(app.repo, {**request, "aspect_ratio": panel["aspect_ratio"] if ratio == "storyboard" else ratio})
    sample.update(board_target=target, board_panel_hash=e.digest(panel), transient=False)
    key = uuid.uuid4().hex
    frozen = {"action": "prompt_batch", "board_input": key, "run_key": key, "base_hash": e.digest(sample),
              "checkpoint": sample["prompt_input"]["checkpoint"], "candidate_count": sample["prompt_input"]["count"]}
    path = app.repo.folder(pid) / "prompt-inputs" / (key + ".json")
    e.save(path, sample)
    try:
        label = project["brief"]["title"] + " · " + target
        if queued:
            return app.night_queue.add(pid, app.night_queue.custom_label(request.get("task_name")) or label, frozen)
        return {"job": app.jobs.submit(label, lambda update, jid: quick_sample.run(app, pid, frozen, update, jid),
                                       pid, request=frozen)}
    except Exception:
        path.unlink(missing_ok=True)
        raise


def unselect(repo, pid, target, image_id):
    with LOCK:
        e.need(repo.get(pid).get("prompt_board"), "请选择提示词分镜作品")
        index = comfyui.drawing_state(repo, pid)
        selected = index["selections"].get(target)
        e.need(selected and selected["image"]["id"] == image_id, "采用的图片已变化，请刷新")
        del index["selections"][target]
        e.save(repo.folder(pid) / "images/index.json", index)
    return {}


def page_state(repo, pid):
    project = repo.get(pid)
    e.need(project.get("prompt_board"), "请选择提示词分镜作品")
    folder = repo.folder(pid)
    selected = comfyui.drawing_state(repo, pid)["selections"]
    pages = []
    for page in project["pages"].values():
        missing, ids = [], []
        for panel in page["panels"]:
            pick = selected.get(panel["id"])
            if not pick or pick["panel_hash"] != e.digest(panel) or not (folder / "images" / pick["image"]["file"]).is_file():
                missing.append(panel["id"])
            else:
                ids.append(pick["image"]["id"])
        signature = e.digest([page, ids])
        saved = folder / "board-layouts" / (page["id"] + ".json")
        last = e.read(saved) if saved.exists() else {}
        pages.append({"id": page["id"], "missing": missing, "signature": signature,
                      "preset": last.get("preset", "story"), "fit": last.get("fit", "contain"),
                      "bundle": last.get("bundle") if not missing and last.get("signature") == signature else None})
    return pages


def compose(repo, pid, request):
    with LOCK:
        pages = page_state(repo, pid)
        missing = [key for page in pages for key in page["missing"]]
        e.need(not missing, "尚未采用图片：" + "、".join(missing))
        page = next((p for p in pages if p["id"] == request.get("target")), None)
        e.need(page and page["signature"] == request.get("signature"), "采用记录已更新，请重新检查")
        preset, fit = request.get("preset"), request.get("fit")
        e.need(fit in ("cover", "contain"), "请选择有效的图片适配方式")
        chosen = layout.default_layout(repo.get(pid)["pages"][page["id"]], preset)
        for rect in chosen["panels"].values():
            rect["fit"] = fit
        result = layout.compose(repo, pid, page["id"], chosen)
        e.save(repo.folder(pid) / "board-layouts" / (page["id"] + ".json"),
               {"signature": page["signature"], "preset": preset, "fit": fit, "bundle": result["bundle"]})
        return result
