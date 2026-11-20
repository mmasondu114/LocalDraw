"""Build a new drawing task from ordered, individually reviewed prompt snapshots."""
from . import direct_imports, engine as e, manual_drawing, prompt_exchange
from .repository import LOCK, safe_id


def source(app, kind, key):
    safe_id(key)
    if kind == "queue":
        item = app.night_queue.preview(key)
        panels = sorted(item["panels"], key=lambda row: row["panel_id"])
        return {"title": item["label"], "panels": [
            {"panel_id": p["panel_id"], "page_id": p["panel_id"].partition("-")[0],
             "positive": p["positive"], "negative": p["negative"]} for p in panels]}
    e.need(kind == "project", "请选择作品或队列任务")
    with LOCK:
        project = app.repo.get(key)
        saved = manual_drawing.saved_texts(app.repo, key, project)
        rows = [{"page_id": page["id"], "panel_id": panel["id"], "summary": panel["main_instant"],
                 "aspect_ratio": panel.get("aspect_ratio", "3:2"),
                 "positive": saved.get(panel["id"], {}).get("positive", ""),
                 "negative": saved.get(panel["id"], {}).get("negative", "")}
                for page, panel in prompt_exchange.panels(project)]
    e.need(rows, "这部作品还没有分镜，请先完成分镜")
    return {"title": project["brief"]["title"], "panels": rows}


def save(app, request):
    sources = request.get("sources")
    e.need(isinstance(sources, list) and len(sources) >= 2, "请至少选择两个来源")
    rows, seen, titles = [], set(), []
    page = 0
    for src in sources:
        e.need(isinstance(src, dict) and src.get("kind") in ("project", "queue"), "来源格式错误")
        origin = (src["kind"], safe_id(src.get("id")))
        e.need(origin not in seen, "同一来源只能添加一次")
        seen.add(origin)
        title, panels = src.get("title"), src.get("panels")
        e.need(isinstance(title, str) and title.strip() and isinstance(panels, list) and panels, "来源缺少名称或画格")
        titles.append(title)
        previous_page, number = None, 0
        source_panels = set()
        for panel in panels:
            e.need(isinstance(panel, dict), "来源画格格式无效")
            if panel.get("skipped") is True:
                continue
            e.need(panel.get("approved") is True, "请逐格审核后再保存")
            key, original_page = panel.get("panel_id"), panel.get("page_id")
            e.need(all(isinstance(v, str) and v for v in (key, original_page)), "来源画格编号无效")
            e.need(key not in source_panels, f"{title} 画格重复：{key}")
            source_panels.add(key)
            positive, negative = panel.get("positive"), panel.get("negative")
            e.need(isinstance(positive, str) and positive.strip() and isinstance(negative, str)
                   and max(len(positive), len(negative)) <= 20000, f"{title} · {key} 提示词无效")
            if original_page != previous_page:
                page += 1
                number = 0
                previous_page = original_page
            number += 1
            rows.append({"source": f"P{page:03}-K{number:02}", "positive": positive, "negative": negative,
                         "aspect_ratio": panel.get("aspect_ratio", "3:2"),
                         "origin": {"kind": origin[0], "id": origin[1], "title": title, "panel_id": key}})
    e.need(1 <= len(rows) <= 1000, "请保留 1–1000 格通过审核的提示词")
    title = " + ".join(titles)
    with LOCK:
        return direct_imports.save_panel_work(app.repo, app.characters, rows, request.get("import_key"),
                                              title, e.digest(rows))
