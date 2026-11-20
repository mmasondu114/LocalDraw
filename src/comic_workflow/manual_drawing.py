"""Literal, user-confirmed drawing with no language-model or review stage."""
import copy
import uuid
from . import engine as e, comfyui, prompt_exchange as exchange
from .batch_drawing import story_fingerprint
from .character_binding import identity_hash
from .repository import LOCK

DEFAULT_COUNT = 2


def preview(repo, pid, request):
    project = repo.get(pid)
    e.need(request.get("base_hash") == e.digest(project), "作品已变化，请刷新后核对提示词")
    pairs = exchange.panels(project)
    targets = request.get("targets")
    if request.get("target"):
        targets = [request["target"]]
    if targets is not None:
        e.need(isinstance(targets, list) and targets and all(isinstance(t, str) for t in targets)
               and len(set(targets)) == len(targets) and set(targets) <= {p["id"] for _, p in pairs}, "画格不存在")
    else:
        plan = (project.get("plan") or {}).get("pages", [])
        e.need(plan and all(p["id"] in project["pages"] for p in plan), "请先确认全部分镜")
    selections = comfyui.drawing_state(repo, pid)["selections"]
    imported = saved_texts(repo, pid, project)
    rows, skipped = [], []
    for page, panel in pairs:
        key = panel["id"]
        if targets is not None and key not in targets:
            continue
        if not request.get("force") and selections.get(key, {}).get("panel_hash") == e.digest(panel):
            skipped.append(key)
            continue
        payload = imported.get(key, {})
        rows.append({"page_id":page["id"], "panel_id":key, "panel_hash":e.digest(panel),
                     "summary":panel.get("main_instant", ""), "positive":payload.get("positive", ""),
                     "negative":payload.get("negative", "")})
    from .prompt_preflight import with_render_plan
    return {"panels":rows, "skipped":skipped, "storyboard_hash":story_fingerprint(project),
            **with_render_plan(repo, pid, project, rows, request.get("checkpoint"))}


def saved_texts(repo, pid, project):
    fingerprint = story_fingerprint(project)
    bundle = exchange.latest_bundle(repo, pid) or {}
    imported = {r["panel_id"]: r for r in bundle.get("panels", [])} if bundle.get("storyboard_hash") == fingerprint else {}
    path = repo.folder(pid) / "manual-prompts.json"
    manual = e.read(path) if path.exists() else {}
    if manual.get("storyboard_hash") == fingerprint:
        for row in manual.get("panels", []):
            key = row["panel_id"]
            if key not in imported or row.get("edited", manual.get("created", "")) >= bundle.get("created", ""):
                imported[key] = row
    for _, panel in exchange.panels(project):
        key = panel["id"]
        saved = project.get("drawing_prompts", {}).get(key, {})
        if key not in imported and saved.get("panel_hash") == e.digest(panel) and saved.get("identity_hash") == identity_hash(project, panel):
            imported[key] = saved.get("payload", {})
    return imported


def prepare_request(repo, pid, request):
    expected = preview(repo, pid, request)
    e.need(request.get("confirm_prompts") is True, "请先核对逐格提示词，再确认出图；不会自动生成或改写")
    rows = request.get("prompt_rows")
    e.need(isinstance(rows, list) and rows, "请填写或导入要绘制的提示词")
    e.need(all(isinstance(r, dict) for r in rows), "提示词格式无效")
    keys = [r.get("panel_id") for r in rows]
    e.need(keys == [r["panel_id"] for r in expected["panels"]], "出图范围已变化，请重新核对")
    for row, original in zip(rows, expected["panels"]):
        e.need(row.get("panel_hash") == original["panel_hash"], "分镜已变化，请重新核对提示词")
        e.need(all(isinstance(row.get(k), str) and len(row[k]) <= 20000 for k in ("positive", "negative"))
               and row["positive"].strip(), row["panel_id"] + " 缺少有效提示词，请手动填写或导入")
    bundle = {"id":uuid.uuid4().hex, "created":e.stamp(), "project_id":pid,
              "storyboard_hash":expected["storyboard_hash"], "panels":copy.deepcopy(rows)}
    # Snapshot the confirmed text without replacing the user's imported file.
    e.save(repo.folder(pid) / "prompt-imports" / (bundle["id"] + ".json"), bundle)
    frozen = exchange.prepare_request(repo, pid, {**request, "bundle":bundle["id"], "targets":keys,
                                                "candidate_count":request.get("candidate_count", DEFAULT_COUNT)})
    frozen["skipped"] = expected["skipped"]
    save_confirmed_prompts(repo, pid, bundle)
    return frozen


def save_confirmed_prompts(repo, pid, bundle):
    path = repo.folder(pid) / "manual-prompts.json"
    with LOCK:
        previous = e.read(path) if path.exists() else {}
        retained = {}
        if previous.get("storyboard_hash") == bundle["storyboard_hash"]:
            for row in previous["panels"]:
                retained[row["panel_id"]] = {**row, "edited": row.get("edited", previous["created"])}
        for row in bundle["panels"]:
            retained[row["panel_id"]] = {**row, "edited": bundle["created"]}
        e.save(path, {**bundle, "panels": list(retained.values())})


def draw_project(app, pid, request, update, jid):
    frozen = prepare_request(app.repo, pid, request)
    result = exchange.draw_imported(app, pid, frozen, update, jid)
    return {**result, "unresolved":[], "skipped":frozen["skipped"]}
