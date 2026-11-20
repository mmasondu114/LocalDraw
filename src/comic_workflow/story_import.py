"""Import complete, externally authored storyboards without model calls."""

import copy
import json
import re
import tempfile
from pathlib import Path

from . import engine as e
from .repository import LOCK, safe_id

MAX_BYTES = 5_000_000
FORMAT = "local-draw-story-v1"


def _pairs(items):
    result = {}
    for key, value in items:
        e.need(key not in result, f"JSON 字段重复：{key}")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError(f"JSON 不支持数值 {value}")


def read_document(document):
    if isinstance(document, str):
        e.need(len(document.encode("utf-8")) <= MAX_BYTES, "剧本 JSON 不能超过 5 MB")
        try:
            document = json.loads(document.lstrip("\ufeff"), object_pairs_hook=_pairs,
                                  parse_constant=_invalid_constant)
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON 格式错误：第 {exc.lineno} 行，第 {exc.colno} 列：{exc.msg}") from exc
    e.need(isinstance(document, dict), "请选择包含 brief、canon、pages 的整部剧本 JSON")
    try:
        size = len(json.dumps(document, ensure_ascii=False, allow_nan=False).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise ValueError("剧本必须是有效 JSON，不能包含非有限数值") from exc
    e.need(size <= MAX_BYTES, "剧本 JSON 不能超过 5 MB")
    e.need(set(document) <= {"format", "brief", "canon", "pages"},
           "此入口只接受 brief、canon、pages（可附 format）；请勿选择单页、提示词文件或作品备份")
    e.need({"brief", "canon", "pages"} <= set(document), "整部剧本必须包含 brief、canon、pages")
    e.need(document.get("format", FORMAT) == FORMAT, "不支持的剧本 JSON 版本")
    e.need(isinstance(document["pages"], list) and document["pages"], "pages 必须是按顺序排列的非空页面数组")
    return copy.deepcopy(document)


def _schema(stage, value, path):
    try:
        e.schema(stage, value)
    except e.jsonschema.ValidationError as exc:
        location = path + "".join(f"[{x}]" if isinstance(x, int) else f".{x}" for x in exc.absolute_path)
        raise ValueError(f"{location}：{exc.message}") from exc


def prepare(document, library):
    doc = read_document(document)
    _schema("brief", doc["brief"], "brief")
    _schema("canon", doc["canon"], "canon")
    e.need(doc["brief"]["title"].strip(), "brief.title：作品标题不能为空")
    e.need(len(doc["pages"]) == doc["brief"]["page_count"], "pages：实际页数与 brief.page_count 不一致")
    e.validate_canon(doc["canon"])
    for collection in ("characters", "scenes", "props"):
        for item in doc["canon"][collection]:
            safe_id(item["id"])
    for n, page in enumerate(doc["pages"], 1):
        _schema("page", page, f"pages[{n - 1}]")
        e.need(page["id"] == f"P{n:03}" and page["number"] == n,
               f"pages[{n - 1}]：页号须为 P{n:03}、number 须为 {n}，不能重复、跳号或乱序")

    brief = copy.deepcopy(doc["brief"])
    # Resolve assets only from the local library, never from imported paths or snapshots.
    brief.pop("character_refs", None)
    local = {card["name"]: card for card in library.listing()}
    refs = [copy.deepcopy(local[c["name"]]) for c in doc["canon"]["characters"] if c["name"] in local]
    if refs:
        brief["character_refs"] = refs
    pages = {page["id"]: copy.deepcopy(page) for page in doc["pages"]}
    p = {
        "version": 1, "workflow": "direct", "brief": brief,
        "canon": copy.deepcopy(doc["canon"]), "options": None,
        "outline": {
            "selected_option": "direct", "premise": brief["direction"],
            "beats": [panel["purpose"] for page in pages.values() for panel in page["panels"]],
            "ending": doc["pages"][-1]["panels"][-1]["main_instant"],
            "must_include_mapping": [], "unknowns": [],
        },
        "plan": {"pages": [
            {"id": page["id"], "number": page["number"],
             "scene_ids": list(dict.fromkeys(panel["scene_id"] for panel in page["panels"])),
             "character_ids": list(dict.fromkeys(
                 [c["character_id"] for panel in page["panels"] for c in panel["characters"]]
                 + [t["speaker_id"] for t in page["lettering"] if t["speaker_id"]])),
             "panel_count": len(page["panels"]), "purpose": page["panels"][0]["purpose"],
             "beat": "\n".join(panel["main_instant"] for panel in page["panels"]),
             "hook": page["page_turn_note"], "reveal": "",
             "turn_after": e.turn_after(brief, page["number"]), "constraints": [page["layout"]]}
            for page in pages.values()]},
        "pages": pages,
        "provenance": "用户导入的完整 JSON 剧本与文字分镜；未调用模型生成或改写",
    }
    # Validate per page first so cross-reference and continuity errors identify the page.
    e.validate_plan(p["plan"], p)
    start = p["canon"]["initial_state"]
    for page in pages.values():
        try:
            e.validate_page(page, p, start)
        except ValueError as exc:
            raise ValueError(f"{page['id']}：{exc}") from exc
        start = page["state_out"]
    warnings = e.validate_project(p, complete=True)
    p["script"] = brief["title"] + "\n\n" + "\n\n".join(e.render_page(page, p) for page in pages.values())
    p["script_source"] = {"mode": "json_import", "original": copy.deepcopy(doc),
                          "assumptions": [], "changes": []}
    preview_hash = e.digest({"document": doc, "character_refs": refs})
    return doc, p, warnings, preview_hash


def preview(document, library):
    _, p, warnings, preview_hash = prepare(document, library)
    bound = {c["name"] for c in p["brief"].get("character_refs", [])}
    return {
        "preview_hash": preview_hash, "title": p["brief"]["title"],
        "page_count": len(p["pages"]), "panel_count": sum(len(page["panels"]) for page in p["pages"].values()),
        "reading_direction": p["brief"]["reading_direction"],
        "characters": [{"id": c["id"], "name": c["name"], "local_match": c["name"] in bound}
                       for c in p["canon"]["characters"]],
        "pages": list(p["pages"].values()), "warnings": warnings,
    }


def save_import(repo, library, request):
    e.need(request.get("confirm") is True, "请先预览，再点击导入为新作品")
    key = request.get("import_key")
    e.need(isinstance(key, str) and re.fullmatch(r"[a-f0-9]{32}", key), "导入标识无效，请重新选择文件")
    with LOCK:
        doc, p, _, preview_hash = prepare(request.get("document"), library)
        e.need(request.get("preview_hash") == preview_hash, "文件或同名角色卡已变化，请重新预览后导入")
        pid = "story-" + key
        destination = repo.folder(pid)
        if destination.exists():
            record_path = destination / "story-import" / "receipt.json"
            e.need(record_path.is_file() and e.read(record_path)["preview_hash"] == preview_hash,
                   "导入标识已用于另一份内容，请重新选择文件")
            repo.get(pid)
            return {"project": pid, "reused": True}
        created = e.stamp()
        receipt = {"format": FORMAT, "preview_hash": preview_hash, "created": created}
        p["last_change"] = {"reason": "导入完整 JSON 剧本", "time": created}
        # A nested staging directory is invisible to Repository.listing(). Publish only
        # after the project, source and opening workspace have all been written.
        staging_root = repo.root / ".story-imports"
        staging_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="pending-", dir=staging_root) as temp:
            folder = Path(temp) / "project"
            e.save(folder / "story-import" / "source.json", doc)
            e.save(folder / "story-import" / "receipt.json", receipt)
            e.save(folder / "workspace.json", {"stage": "page", "target": "P001", "views": {}, "updated": 1})
            e.save(folder / "project.json", p)
            folder.rename(destination)
        return {"project": pid, "reused": False}
