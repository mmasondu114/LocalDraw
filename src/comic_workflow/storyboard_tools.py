"""Whole-book drafts and explicit per-item application with conflict checks."""

import copy
import json
from jsonschema import ValidationError
from . import engine as e
from .model import generate
from .repository import LOCK, safe_id, stage_value
from .task_control import checkpoint, TaskCancelled

PAGE_INSTRUCTION = "保留原剧情和对白；每格单一瞬间，页面格子大小有变化。"


def load_checkpoint(repo, pid, request):
    """Rebuild context from validated saved pages without asking the model again."""
    p = repo.get(pid)
    e.need(e.digest(p) == request["base_hash"], "作品已变化，不能接续旧任务；已保存草稿保留")
    folder = repo.folder(pid) / "storyboard-drafts" / safe_id(request["bundle_id"])
    file = folder / "bundle.json"
    if not file.exists():
        e.need(not request.get("resume_existing"), "找不到断点文件，已停止；不会从头重做")
        return p, folder, None
    bundle = e.read(file)
    e.need(bundle["base_hash"] == request["base_hash"], "断点与作品版本不符")
    e.need(bundle["status"] not in {"applied", "cancelled"}, "这份草稿已确认或已结束，不能继续")
    if not p.get("plan") and bundle.get("plan"):
        p = e.merged(p, "plan", "", bundle["plan"])
    e.need(not bundle["pages"] or p.get("plan"), "断点缺少分页计划，不能继续")
    if p.get("plan"):
        keys = [x["id"] for x in p["plan"]["pages"]]
        e.need(set(bundle["pages"]).issubset(keys), "断点包含未知页面")
        for key in keys:
            if key in bundle["pages"]:
                e.need(key not in p["pages"], "已确认页与断点重复，拒绝覆盖")
                p = e.merged(p, "page", key, bundle["pages"][key]["payload"])
    return p, folder, bundle


def restore_jobs(repo, jobs):
    """Upgrade restart records, including old jobs that only saved draft bundles."""
    for file in jobs.root.glob("*.json"):
        job = e.read(file)
        if job.get("label") != "生成整部分镜" or job["status"] not in {"interrupted", "paused"}:
            continue
        try:
            with repo.task_access(job["project"]):
                request = copy.deepcopy(job.get("request"))
                if not request:
                    folder = repo.folder(job["project"]) / "storyboard-drafts" / safe_id(job["id"])
                    bundle = e.read(folder / "bundle.json")
                    request = bundle.get("request")
                    if not request:
                        # Old versions logged the original user context on each page call.
                        instruction = None
                        for log in sorted(folder.glob("*/0/request.json")):
                            body = e.read(log)
                            if body.get("response_format", {}).get("json_schema", {}).get("name") != "page":
                                continue
                            for message in body.get("messages", []):
                                if message.get("role") != "user":
                                    continue
                                try:
                                    value = json.loads(message["content"]).get("instruction")
                                except (ValueError, TypeError, AttributeError):
                                    continue
                                if isinstance(value, str) and value.startswith(PAGE_INSTRUCTION):
                                    instruction = value[len(PAGE_INSTRUCTION):]
                                    break
                            if instruction is not None:
                                break
                        e.need(instruction is not None, "旧任务缺少原始要求，不能可靠续作；草稿仍保留")
                        request = {"action": "storyboard_all", "base_hash": bundle["base_hash"], "instruction": instruction}
                    request = {**request, "bundle_id": bundle["id"], "resume_existing": True}
                request = {**request, "resume_existing": True}
                p, folder, bundle = load_checkpoint(repo, job["project"], request)
                # No automatic generation on startup. The user explicitly resumes.
                job.update(request=request, status="paused", message="进度已恢复，点击继续将跳过已保存页面")
                if p.get("plan"):
                    job["progress"] = {"completed": len(p["pages"]), "total": len(p["plan"]["pages"]), "unit": "页"}
                job.pop("resume_error", None)
                if bundle and bundle["status"] != "complete":
                    bundle["status"] = "paused"
                    e.save(folder / "bundle.json", bundle)
        except (ValueError, KeyError, OSError, TypeError, AttributeError, ValidationError) as exc:
            reason = exc.message if isinstance(exc, ValidationError) else str(exc)
            job.update(status="interrupted", resume_error=reason, message=reason)
        e.save(file, job)


def generate_all(app, pid, request, update, jid):
    request = {**request, "bundle_id": request.get("bundle_id", jid)}
    p, folder, saved = load_checkpoint(app.repo, pid, request)
    if saved and saved["status"] == "complete":
        total = len(p["plan"]["pages"])
        update({"message": "已读取完整草稿，无需重复生成", "progress": {
            "completed": total, "total": total, "unit": "页"}})
        return {"bundle": saved["id"]}
    bundle = saved or {
        "id": request["bundle_id"],
        "base_hash": request["base_hash"],
        "created": e.stamp(),
        "pages": {},
        "plan": p.get("plan"),
        "status": "running",
    }
    bundle.update(status="running", request=request)
    bundle.pop("error", None)
    e.save(folder / "bundle.json", bundle)
    try:
        if not bundle["pages"] and p.get("plan") and all(x["id"] in p["pages"] for x in p["plan"]["pages"]):
            bundle["status"] = "complete"
            return {"bundle": bundle["id"], "message": "已有全部分镜，直接集中编辑即可"}
        if p.get("plan"):
            update({"message": "已读取保存进度，跳过已完成页面", "progress": {
                "completed": len(p["pages"]), "total": len(p["plan"]["pages"]), "unit": "页"}})
        checkpoint()
        app.prepare_language(update=update)
        if not p.get("plan"):
            plan, _ = generate(
                p,
                "plan",
                "",
                "按原剧本安排日漫大小分格，保留阅读方向。",
                folder / "plan",
                update,
            )
            p = e.merged(p, "plan", "", plan)
            bundle["plan"] = plan
            e.save(folder / "bundle.json", bundle)
        total = len(p["plan"]["pages"])
        for n, planned in enumerate(p["plan"]["pages"]):
            checkpoint()
            e.need(
                e.digest(app.repo.get(pid)) == bundle["base_hash"],
                "生成期间正式分镜已变化，保留已有草稿并停止",
            )
            key = planned["id"]
            if key in p["pages"]:
                continue

            def emit(event):
                detail = event if isinstance(event, dict) else {"message": event}
                update(
                    {
                        **detail,
                        "message": f"整部分镜 · 第 {n + 1}/{total} 页 · "
                        + detail.get("message", ""),
                        "progress": {"completed": len(p["pages"]), "total": total, "unit": "页"},
                    }
                )

            payload, metrics = generate(
                p,
                "page",
                key,
                PAGE_INSTRUCTION + request.get("instruction", ""),
                folder / key / ("resume-" + jid) if saved else folder / key,
                emit,
            )
            if p.get("script_pages", {}).get(key):
                from .automatic import preserve_source_lettering

                payload = preserve_source_lettering(
                    payload, p["script_pages"][key], p["canon"]["characters"]
                )
            p = e.merged(p, "page", key, payload)
            bundle["pages"][key] = {"payload": payload, "metadata": metrics}
            e.save(folder / "bundle.json", bundle)
            update({"message": f"已保存第 {n + 1}/{total} 页", "progress": {
                "completed": len(p["pages"]), "total": total, "unit": "页"}})
        # Audit after all pages are drafted, while the same language model remains loaded.
        for n, (key, entry) in enumerate(bundle["pages"].items()):
            checkpoint()
            e.need(e.digest(app.repo.get(pid)) == bundle["base_hash"], "作品已变化，已保存草稿保留")
            if "continuity_review" in entry["metadata"]:
                continue
            update(
                {
                    "message": f"集中核对分镜 · {n}/{len(bundle['pages'])} 页",
                    "progress": {"completed": total, "total": total, "unit": "页"},
                    "detail_progress": {
                        "completed": n,
                        "total": len(bundle["pages"]),
                        "unit": "页已核对",
                    },
                }
            )
            try:
                audit, metrics = generate(
                    p,
                    "audit",
                    key,
                    "核对本页角色口吻、原文、单一瞬间与前后衔接，只报告不改稿。",
                    folder / "audits" / key,
                    update,
                )
                entry["metadata"].update(
                    continuity_review=audit, continuity_metrics=metrics
                )
            except Exception as exc:
                entry["metadata"]["continuity_review"] = {
                    "verdict": "unavailable",
                    "reason": str(exc),
                }
            e.save(folder / "bundle.json", bundle)
        bundle["status"] = "complete"
        update(
            {
                "message": "整部分镜草稿已生成，可集中阅读、修改和确认",
                "progress": {"completed": total, "total": total, "unit": "页"},
            }
        )
        return {"bundle": bundle["id"]}
    except TaskCancelled:
        bundle["status"] = "cancelled"
        raise
    except Exception as exc:
        bundle.update(status="failed", error=str(exc))
        raise
    finally:
        e.save(folder / "bundle.json", bundle)
        app.model.stop()


def accept_bundle(repo, pid, key):
    with LOCK:
        file = repo.folder(pid) / "storyboard-drafts" / safe_id(key) / "bundle.json"
        bundle = e.read(file)
        e.need(
            bundle["status"] == "complete" and bundle["pages"],
            "草稿尚未完整或没有新页面",
        )
        p = repo.get(pid)
        e.need(
            e.digest(p) == bundle["base_hash"],
            "正式分镜已变化，拒绝覆盖；这份草稿仍保留",
        )
        q = copy.deepcopy(p)
        if not q.get("plan"):
            q = e.merged(q, "plan", "", bundle["plan"])
        for target, page in sorted(bundle["pages"].items()):
            e.need(target not in q["pages"], "已有确认页，不允许覆盖")
            q = e.merged(q, "page", target, page["payload"])
        repo.commit(pid, q, e.digest(p), {"accept_storyboard_bundle": key})
        bundle["status"] = "applied"
        e.save(file, bundle)
        return {"applied": key}


def candidate_units(repo, pid, cid):
    c = e.read(repo.folder(pid) / "candidates" / (safe_id(cid) + ".json"))
    e.need(c["stage"] in ["page", "panel"], "此类候选不支持逐格应用")
    p = repo.get(pid)
    base = c.get("before_payload")
    if "before_payload" not in c:
        snapshots = [p] + [
            e.read(f) for f in (repo.folder(pid) / "history").glob("*.json")
        ]
        snapshot = next((s for s in snapshots if e.digest(s) == c["base_hash"]), None)
        e.need(snapshot is not None, "找不到候选基准版本")
        base = stage_value(snapshot, c["stage"], c["target"])
    current = stage_value(p, c["stage"], c["target"])
    units = []
    if not base or c["stage"] == "panel":
        units.append(
            {
                "id": "whole",
                "title": c["target"],
                "before": base,
                "after": c["payload"],
                "current": current,
            }
        )
    else:

        def part(page, key):
            return {
                "panel": next(
                    (x for x in (page or {}).get("panels", []) if x["id"] == key), None
                ),
                "lettering": [
                    x for x in (page or {}).get("lettering", []) if x["panel_id"] == key
                ],
            }

        ids = [x["id"] for x in base["panels"]]
        if ids != [x["id"] for x in c["payload"]["panels"]]:
            units.append(
                {
                    "id": "whole",
                    "title": "整页结构调整",
                    "before": base,
                    "after": c["payload"],
                    "current": current,
                }
            )
        else:
            for key in ids:
                before, after = part(base, key), part(c["payload"], key)
                if before != after:
                    units.append(
                        {
                            "id": key,
                            "title": key + " 画面与对白",
                            "before": before,
                            "after": after,
                            "current": part(current, key),
                        }
                    )

            def metadata(page):
                return {
                    k: v
                    for k, v in (page or {}).items()
                    if k not in ["panels", "lettering"]
                }

            if metadata(base) != metadata(c["payload"]):
                units.append(
                    {
                        "id": "metadata",
                        "title": "页面衔接与版式",
                        "before": metadata(base),
                        "after": metadata(c["payload"]),
                        "current": metadata(current),
                    }
                )
    decisions_path = repo.folder(pid) / "review-decisions" / (safe_id(cid) + ".json")
    decisions = e.read(decisions_path) if decisions_path.exists() else {}
    for unit in units:
        unit["decision"] = decisions.get(unit["id"], {}).get("decision", "pending")
        unit["conflict"] = unit["current"] not in [unit["before"], unit["after"]]
        unit.pop("current")
    return c, units


def decide(repo, pid, cid, unit_id, decision):
    e.need(decision in ["apply", "cancel"], "未知处理方式")
    with LOCK:
        c, units = candidate_units(repo, pid, cid)
        unit = next((x for x in units if x["id"] == unit_id), None)
        e.need(unit and unit["decision"] == "pending", "此条已处理或不存在")
        if decision == "apply":
            e.need(
                not unit["conflict"], "此格已被其他修改更新，不能覆盖；请重新生成建议"
            )
            p = repo.get(pid)
            if unit_id == "whole":
                q = e.merged(p, c["stage"], c["target"], unit["after"])
            elif unit_id == "metadata":
                payload = copy.deepcopy(p["pages"][c["target"]])
                payload.update(unit["after"])
                q = e.merged(p, "page", c["target"], payload)
            else:
                q = e.merged(p, "panel", unit_id, unit["after"])
            repo.commit(
                pid,
                q,
                e.digest(p),
                {"candidate": cid, "unit": unit_id, "decision": "apply"},
            )
        file = repo.folder(pid) / "review-decisions" / (safe_id(cid) + ".json")
        decisions = e.read(file) if file.exists() else {}
        decisions[unit_id] = {"decision": decision, "time": e.stamp()}
        e.save(file, decisions)
        return {"decision": decision}
