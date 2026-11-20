"""Import literal storyboard and panel prompt data through preview/confirm."""

import json
import re
import tempfile
from pathlib import Path
import uuid

from . import engine as e, manual_drawing, prompt_exchange, story_import
from .batch_drawing import story_fingerprint
from .repository import LOCK


def parse(value, limit):
    if isinstance(value, str):
        e.need(len(value.encode("utf-8")) <= limit, "文件过大，请缩小文件或分批导入")
        try:
            return json.loads(value.lstrip("\ufeff"), object_pairs_hook=story_import._pairs,
                              parse_constant=story_import._invalid_constant)
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON 格式错误：第 {exc.lineno} 行，第 {exc.colno} 列") from exc
    e.need(len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")) <= limit,
           "文件过大，请缩小文件或分批导入")
    return value


def storyboard(repo, request):
    doc = parse(request.get("document"), story_import.MAX_BYTES)
    e.need(isinstance(doc, dict), "完整分镜须包含页面。逐格提示词列表请用首页的「直接导入每格提示词」。")
    if doc.get("format") == prompt_exchange.FORMAT:
        if "portable_storyboard" in doc:
            return story_import.read_document(doc["portable_storyboard"])
        base = parse(request.get("base_project"), story_import.MAX_BYTES)
        if base is None:
            try:
                base = repo.get(doc.get("project_id"))
            except FileNotFoundError:
                raise ValueError("此交换文件仅含分镜摘要，请同时选择导出时的 project.json") from None
        e.need(isinstance(base, dict) and story_fingerprint(base) == doc.get("storyboard_hash"),
               "提示词交换文件和原 project.json 的分镜版本不一致")
        expected = {p["id"]: (page["id"], p) for page, p in prompt_exchange.panels(base)}
        rows = doc.get("panels")
        e.need(isinstance(rows, list) and len(rows) == len(expected), "此交换文件只含部分画格，请使用完整分镜")
        seen = set()
        for row in rows:
            key = row.get("panel_id") if isinstance(row, dict) else None
            e.need(isinstance(key, str) and key in expected and key not in seen, "交换文件的画格编号不匹配")
            seen.add(key)
            page_id, panel = expected[key]
            board = row.get("storyboard")
            e.need(isinstance(board, dict) and row.get("page_id") == page_id
                   and board.get("action") == panel.get("main_instant", "")
                   and board.get("description") == panel.get("drawing_description", ""),
                   f"{key}：摘要已变化，请导入包含完整页面的分镜 JSON")
        doc = base
    elif "base_hash" in doc and isinstance(doc.get("pages"), dict):
        e.need(doc.get("status") in {"complete", "applied"}, "这份分镜草稿尚未完成")
        base = parse(request.get("base_project"), story_import.MAX_BYTES)
        e.need(isinstance(base, dict) and e.digest(base) == doc["base_hash"],
               "请同时选择生成这份 bundle 时的原 project.json，作品版本须完全匹配")
        pages = dict(base.get("pages", {}))
        for key, entry in doc["pages"].items():
            e.need(isinstance(entry, dict) and isinstance(entry.get("payload"), dict)
                   and entry["payload"].get("id") == key, f"{key}：草稿页面编号不匹配")
            e.need(key not in pages or pages[key] == entry["payload"], f"{key}：原作品和草稿内容冲突")
            pages[key] = entry["payload"]
        doc = {**base, "pages": pages}
    if isinstance(doc.get("pages"), dict):
        pages = doc["pages"]
        e.need(all(isinstance(page, dict) and page.get("id") == key for key, page in pages.items()),
               "作品中的页面编号和页面内容不匹配")
        e.need("brief" in doc and "canon" in doc, "作品缺少 brief 或 canon，请选择完整分镜或配对的原作品")
        doc = {"brief": doc["brief"], "canon": doc["canon"], "pages": [pages[key] for key in sorted(pages)]}
    return doc


def prompt_row(row):
    if "negative_prompt" in row and "negative" not in row:
        row = {**row, "negative": row["negative_prompt"]}
    if "visual_prompt" not in row:
        return row
    return {**row, "panel_id": row.get("panel_id", row.get("id")),
            "positive": row.get("positive", row["visual_prompt"]), "negative": row.get("negative", "")}


def prompt_preview(repo, pid, request):
    project = repo.get(pid)
    story_hash = story_fingerprint(project)
    expected = {panel["id"]: (page["id"], panel) for page, panel in prompt_exchange.panels(project)}
    e.need(expected, "该作品尚无已确认分镜，请先导入或确认分镜")
    doc = parse(request.get("document"), story_import.MAX_BYTES)
    if isinstance(doc, list):
        doc = {"panels": doc}
    e.need(isinstance(doc, dict), "提示词须为含 panels 的 JSON 或逐格提示词数组")
    e.need(doc.get("format", prompt_exchange.FORMAT) == prompt_exchange.FORMAT, "提示词文件版本不符")
    foreign = doc.get("project_id", pid) != pid
    if not foreign:
        e.need(doc.get("storyboard_hash", story_hash) == story_hash, "分镜版本已变化，请核对新分镜")
    rows = doc.get("panels")
    e.need(isinstance(rows, list) and 1 <= len(rows) <= 1000, "panels 须包含 1–1000 格")
    if foreign:
        e.need(doc.get("format") == prompt_exchange.FORMAT and isinstance(doc.get("project_id"), str),
               "提示词文件属于其他作品。跨作品映射需要含完整分镜摘要的原导出文件")
        originals = {r["panel_id"]: r["storyboard"] for r in prompt_exchange.export_storyboard(repo, pid)["panels"]}
    workspace_file = repo.folder(pid) / "workspace.json"
    drafts = e.read(workspace_file).get("panelPromptDrafts", {}) if workspace_file.exists() else {}
    saved = manual_drawing.saved_texts(repo, pid, project)
    checked, changed = {}, []
    for n, row in enumerate(rows, 1):
        e.need(isinstance(row, dict), f"第 {n} 条画格格式错误")
        row = prompt_row(row)
        key = row.get("panel_id")
        e.need(isinstance(key, str) and key in expected, f"第 {n} 条的 panel_id 不属于所选作品")
        e.need(key not in checked, f"{key}：画格编号重复")
        page, panel = expected[key]
        e.need(row.get("page_id", page) == page, f"{key}：所属页不正确")
        if foreign:
            e.need(originals[key] == row.get("storyboard"),
                   f"{key}：来源与目标作品的分镜摘要不匹配，无法按编号映射")
        positive, negative = row.get("positive"), row.get("negative")
        e.need(isinstance(positive, str) and positive.strip() and isinstance(negative, str)
               and max(len(positive), len(negative)) <= 20000,
               f"{key}：positive 须为非空文字，negative 可填空字符串；每项最多 20000 字")
        old = saved.get(key, {})
        previous = [old.get("positive", ""), old.get("negative", "")]
        draft = drafts.get(key, {})
        draft_text = [draft.get("positive", ""), draft.get("negative", "")]
        if draft.get("hash") == e.digest(panel) and draft_text != draft.get("source") and draft_text != [positive, negative]:
            raise ValueError(f"{key} 有未提交的提示词草稿，请先处理该格草稿再导入")
        if any(previous) and previous != [positive, negative]:
            changed.append(key)
        checked[key] = {"page_id": page, "panel_id": key, "panel_hash": e.digest(panel),
                        "summary": panel.get("main_instant", ""), "positive": positive, "negative": negative}
    current = prompt_exchange.latest_bundle(repo, pid)
    preview_hash = e.digest([pid, story_hash, checked, current.get("id") if current else None,
                             {k: saved.get(k) for k in checked}, {k: drafts.get(k) for k in checked}])
    return {"panels": list(checked.values()), "replace": changed, "total": len(expected),
            "mapped_from": doc.get("project_id") if foreign else None,
            "storyboard_hash": story_hash, "preview_hash": preview_hash}


def standalone_rows(request):
    doc = parse(request.get("document"), story_import.MAX_BYTES)
    rows = doc.get("panels") if isinstance(doc, dict) else doc
    e.need(isinstance(rows, list) and 1 <= len(rows) <= 1000, "请提供 1–1000 格提示词")
    result, seen = [], set()
    for number, row in enumerate(rows, 1):
        if isinstance(row, str):
            row = {"positive": row}
        e.need(isinstance(row, dict), f"第 {number} 格格式错误")
        row = prompt_row(row)
        positive, negative = row.get("positive"), row.get("negative", "")
        e.need(isinstance(positive, str) and positive.strip() and isinstance(negative, str)
               and max(len(positive), len(negative)) <= 20000, f"第 {number} 格的提示词无效，每项最多 20000 字")
        key = row.get("panel_id")
        if key is not None:
            e.need(isinstance(key, str) and key.strip() and key not in seen, f"第 {number} 格编号为空或重复")
            seen.add(key)
        result.append({"source": key, "positive": positive, "negative": negative})
    return result


def standalone_import(repo, library, request):
    rows = standalone_rows(request)
    digest = e.digest(rows)
    if request["action"] == "preview_direct_prompts":
        return {"panels": [{"panel_id": row["source"] or f"第 {n} 格", **row} for n, row in enumerate(rows, 1)],
                "total": len(rows), "replace": [], "preview_hash": digest}
    e.need(request.get("confirm") is True and request.get("preview_hash") == digest, "提示词已变化，请重新导入")
    title = "逐格出图 · " + rows[0]["positive"].strip().splitlines()[0][:24]
    return save_panel_work(repo, library, rows, request.get("import_key"), title, digest)


def save_panel_work(repo, library, rows, key, title, digest, *, board=False):
    from .quick_sample import prompt_characters, prompt_panel
    e.need(isinstance(key, str) and re.fullmatch(r"[a-f0-9]{32}", key), "导入标识无效")
    pid = "panels-" + key
    dest = repo.folder(pid)
    if dest.exists():
        receipt = e.read(dest / "prompt-imports" / "receipt.json")
        e.need(receipt["hash"] == digest, "该导入标识已用于其他提示词")
        return receipt["result"]
    pages, prompts = [], []
    last_group = None
    for row in rows:
        group = (row["source"] or "").partition("-")[0] if re.match(r"^P[0-9]+-K[0-9]+$", row["source"] or "") else ""
        if not pages or group != last_group or len(pages[-1]["panels"]) == 9:
            n = len(pages) + 1
            pages.append({"id": f"P{n:03}", "number": n, "reading_direction": "LTR", "layout": "逐格出图",
                          "page_turn_note": "", "state_in": {}, "state_out": {}, "lettering": [], "panels": []})
        last_group = group
        page = pages[-1]
        summary = row["description"] if board else row["positive"].strip().splitlines()[0][:100]
        panel = prompt_panel(summary, row.get("aspect_ratio", "3:2"), [], page["id"], len(page["panels"]) + 1)
        page["panels"].append(panel)
        prompts.append({**row, "page_id": page["id"], "panel_id": panel["id"], "panel_hash": e.digest(panel),
                        "summary": (row["source"] + " · " if row["source"] else "") + summary})
    doc = {"brief": {"title": title, "genre": "提示词出图", "direction": "按导入顺序逐格绘制",
                     "page_count": len(pages), "panels_per_page": 1, "panel_mode": "flexible",
                     "reading_direction": "LTR", "page_mode": "single_page", "first_story_page_side": "right",
                     "must_include": [], "avoid": [], "character_material": ""},
           "canon": {"characters": prompt_characters([]), "scenes": [{"id": "S001", "name": "按提示词",
                     "layout": "", "landmarks": [], "lighting": "", "axis_rule": ""}],
                     "props": [], "world_rules": [], "initial_state": {}}, "pages": pages}
    _, project, _, _ = story_import.prepare(doc, library)
    if board:
        project["prompt_board"] = True
        project["provenance"] = "用户填写的创作分镜"
    bundle = {"id": key, "created": e.stamp(), "project_id": pid, "storyboard_hash": story_fingerprint(project),
              "panels": prompts}
    result = {"project": pid, "bundle": key, "saved": len(rows), "targets": [r["panel_id"] for r in prompts]}
    with tempfile.TemporaryDirectory(dir=repo.root) as temp:
        stage = Path(temp) / "pending"
        if not board:
            e.save(stage / "prompt-imports" / (key + ".json"), bundle)
            e.save(stage / "prompt-imports" / "current.json", {"id": key})
        e.save(stage / "prompt-imports" / "receipt.json", {"hash": digest, "result": result})
        e.save(stage / "workspace.json", {"stage": "prompt" if board else "drawing", "target": "", "views": {}, "updated": 1})
        e.save(stage / "project.json", project)
        stage.rename(dest)
    return result


def handle(repo, library, request):
    action, pid = request["action"], request.get("project")
    if action in {"preview_direct_story", "save_direct_story"}:
        doc = storyboard(repo, request)
        if action == "preview_direct_story":
            return story_import.preview(doc, library)
        return story_import.save_import(repo, library, {**request, "document": doc})
    with LOCK:
        if not pid:
            return standalone_import(repo, library, request)
        result = prompt_preview(repo, pid, request)
        if action == "preview_direct_prompts":
            return result
        e.need(request.get("confirm") is True and result["preview_hash"] == request.get("preview_hash"),
               "分镜、提示词或编辑草稿已变化，请重新预览并确认")
        current = prompt_exchange.latest_bundle(repo, pid)
        rows = {}
        if current and current["storyboard_hash"] == result["storyboard_hash"]:
            for row in current["panels"]:
                rows[row["panel_id"]] = {**row, "edited": row.get("edited", current["created"])}
        now = e.stamp()
        for row in result["panels"]:
            rows[row["panel_id"]] = {**row, "edited": now}
        bundle = {"id": uuid.uuid4().hex, "created": now, "project_id": pid,
                  "storyboard_hash": result["storyboard_hash"], "panels": list(rows.values())}
        folder = repo.folder(pid) / "prompt-imports"
        e.save(folder / (bundle["id"] + ".json"), bundle)
        e.save(folder / "current.json", {"id": bundle["id"]})
        return {"project": pid, "saved": len(result["panels"]), "bundle": bundle["id"],
                "targets": [r["panel_id"] for r in result["panels"]]}
