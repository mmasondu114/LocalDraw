"""Authorized unattended script -> pages -> drawings, with durable checkpoints."""

import copy
import re
from . import engine as e
from .model import generate


def script_pages(script):
    headings = list(
        re.finditer(r"(?m)^\s*#{1,4}\s*第[一二三四五六七八九十百\d]+页[^\n]*", script)
    )
    e.need(headings, "自动流程需要剧本中的分页标题，未发现可识别分页")
    result = {}
    for index, match in enumerate(headings):
        text = script[
            match.start() : headings[index + 1].start()
            if index + 1 < len(headings)
            else len(script)
        ]
        text = re.split(r"\*\*【编剧备注】", text)[0].strip()
        panels = re.split(r"\*\*Panel\s+\d+\*\*", text, flags=re.I)[1:]
        e.need(1 <= len(panels) <= 9, f"第 {index + 1} 页未识别到 1–9 个画格")
        result[f"P{index + 1:03}"] = {
            "title": match.group().lstrip("# "),
            "text": text,
            "panels": [x.strip() for x in panels],
        }
    return result


def prepare_project(repo, pid):
    p = repo.get(pid)
    if p.get("automation_source_hash") == e.digest(p.get("script", "")):
        return p
    e.need(not p["pages"], "已有确认分镜，不能自动重排；请使用分支")
    q = copy.deepcopy(p)
    sources = script_pages(p.get("script", ""))
    q["script_pages"] = sources
    q["brief"]["page_count"] = len(sources)
    q["brief"]["panel_mode"] = "flexible"
    q["automation_source_hash"] = e.digest(q["script"])
    q["automation_policy"] = (
        "用户授权自动完成至候选选图；角色卡外观优先。完全遮脸角色通过手部、背影或画外动作表现进食，五官不显露，不改变事件对白。"
    )
    q["visual_policy"] = {
        "fully_covered_faces": [
            c["id"]
            for c in q["canon"]["characters"]
            if "遮蔽面部" in c.get("appearance", "")
            or "完全遮脸" in c.get("appearance", "")
        ]
    }
    scenes = [x["id"] for x in q["canon"]["scenes"]]
    cast = [x["id"] for x in q["canon"]["characters"]]
    pages = []
    for n, (key, source) in enumerate(sources.items(), 1):
        pages.append(
            {
                "id": key,
                "number": n,
                "scene_ids": scenes,
                "character_ids": cast,
                "panel_count": len(source["panels"]),
                "purpose": source["title"],
                "beat": "按本页原文逐格发生，不合并其他页事件",
                "hook": "",
                "reveal": "",
                "turn_after": e.turn_after(q["brief"], n),
                "constraints": [
                    "保留本页逐格原文中的事件、说话者和对白顺序",
                    "角色身份和外貌以保存角色卡为准",
                ],
            }
        )
    q["plan"] = {"pages": pages}
    e.validate_project(q)
    return repo.commit(
        pid,
        q,
        e.digest(p),
        "按用户授权自动制作；依剧本正文六页逐格保留，角色卡外观优先",
    )


def preserve_source_lettering(payload, source, characters):
    page = copy.deepcopy(payload)
    names = {c["name"].lstrip("/"): c["id"] for c in characters}
    texts = []
    for panel, block in zip(page["panels"], source["panels"], strict=True):
        order = 0
        for match in re.finditer(
            r"(?m)^\s*\*\*([^*]+)\*\*([^：:\n]*)[：:]\s*(.+)$", block
        ):
            name, annotation, words = match.groups()
            name = name.lstrip("/")
            if name not in names and name not in ["音效", "旁白", "拟声"]:
                continue
            order += 1
            kind = (
                "dialogue"
                if name in names
                else "narration"
                if name == "旁白"
                else "sfx"
            )
            if kind == "sfx":
                words = re.split(r"[（(]", words)[0].strip(" *")
            else:
                words = (
                    words.strip()
                    .removeprefix("“")
                    .removesuffix("”")
                    .removeprefix("「")
                    .removesuffix("」")
                )
            old = next(
                (
                    t
                    for t in page["lettering"]
                    if t["panel_id"] == panel["id"] and t["order"] == order
                ),
                {},
            )
            position = old.get(
                "bubble_position", "上方留白，按阅读顺序排列，不遮挡人物"
            )
            annotation = annotation.strip()
            # A draft may already contain this source note from an earlier pass.
            # Keep the supplied placement intact instead of appending it again.
            if annotation and annotation not in position:
                position += "；" + annotation
            texts.append(
                {
                    "id": f"{panel['id']}-T{order:02}",
                    "panel_id": panel["id"],
                    "kind": kind,
                    "speaker_id": names.get(name),
                    "text": words,
                    "bubble_position": position,
                    "order": order,
                }
            )
    page["lettering"] = texts
    return page


def run_automatic(app, pid, update, jid):
    folder = app.repo.folder(pid) / "runs" / jid
    result = {
        "source": "用户授权按网页剧本自动制作，最终图片仍待用户选择",
        "pages": [],
        "started": e.stamp(),
    }
    try:
        p = prepare_project(app.repo, pid)
        result["source_hash"] = p["automation_source_hash"]
        e.save(folder / "automatic.json", result)
        if any(page["id"] not in p["pages"] for page in p["plan"]["pages"]):
            app.prepare_language(update=update)
        for number, page in enumerate(p["plan"]["pages"]):
            p = app.repo.get(pid)
            e.need(
                e.digest(p["script"]) == result["source_hash"],
                "原剧本运行中发生变化，已停止",
            )
            if page["id"] in p["pages"]:
                continue
            update(
                {
                    "message": f"自动文字分镜：第 {number + 1}/{len(p['plan']['pages'])} 页",
                    "progress": {
                        "completed": number,
                        "total": len(p["plan"]["pages"]),
                        "unit": "页分镜",
                    },
                    "runtime": None,
                }
            )
            payload, metrics = generate(
                p,
                "page",
                page["id"],
                "严格按 source_page.panels 对应原文生成，每个原文格对应一个格。原文对白保留，不重写剧情。"
                "脸部被遮蔽的博士，不增加可见眼睛嘴部；有表情或进食用体态、手部特写及画外动作传达。"
                "drawing_description 只写约80–160字可见画面，禁止重复废话。",
                folder / "pages" / page["id"],
                update,
            )
            payload = preserve_source_lettering(
                payload, p["script_pages"][page["id"]], p["canon"]["characters"]
            )
            metrics["source_lettering_preserved"] = True
            # AI audit is a report, never an approval claim. Structural checks already ran.
            proposed = e.merged(p, "page", page["id"], payload)
            try:
                audit, am = generate(
                    proposed,
                    "audit",
                    page["id"],
                    "只核对本页原文对应事件、对白、身份和状态，不改稿。",
                    folder / "audits" / page["id"],
                    update,
                )
                metrics.update(continuity_review=audit, continuity_metrics=am)
            except Exception as exc:
                metrics["continuity_review"] = {
                    "verdict": "unavailable",
                    "reason": str(exc),
                }
            c = app.repo.propose(
                pid,
                "page",
                page["id"],
                payload,
                e.digest(p),
                "用户授权自动流程生成分镜",
                metrics,
            )
            app.repo.accept(pid, c["id"])
            result["pages"].append(
                {
                    "page": page["id"],
                    "candidate": c["id"],
                    "audit": metrics["continuity_review"]["verdict"],
                }
            )
            e.save(folder / "automatic.json", result)
        update({"message": "文字分镜已保存，请核对提示词后手动出图", "progress": None, "runtime": None})
        result["drawing"] = {"unresolved": [], "batches": []}
        result["status"] = "ready_for_prompt_confirmation"
        return result
    except Exception as exc:
        result.update(status="failed", error=str(exc))
        raise
    finally:
        app.model.stop()
        app.comfy.stop()
        result["finished"] = e.stamp()
        result["models_unloaded"] = not app.model.status()["ready"]
        e.save(folder / "automatic.json", result)
