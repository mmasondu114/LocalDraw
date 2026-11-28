"""Transactional project operations, immutable proposals and explicit acceptance."""

import copy
import difflib
import re
import threading
import uuid
import tempfile
import shutil
from contextlib import contextmanager
from pathlib import Path

from . import engine as e
from .paths import PROJECTS, ROOT

LOCK = threading.RLock()
STAGES = ["brief", "canon", "options", "outline", "plan", "page", "audit"]
PROMPT_WORKSPACE_ID = "prompt-workspace"


def safe_id(value):
    e.need(
        isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value),
        "无效标识",
    )
    return value


def stage_value(p, stage, target=""):
    if stage == "drawing_prompt":
        return p.get("drawing_prompts", {}).get(target, {}).get("payload")
    if stage == "page":
        return p["pages"].get(target)
    if stage == "panel":
        page = p["pages"].get(target.split("-")[0], {})
        panel = next((x for x in page.get("panels", []) if x["id"] == target), None)
        return (
            {
                "panel": panel,
                "lettering": [x for x in page["lettering"] if x["panel_id"] == target],
            }
            if panel
            else None
        )
    return p.get(stage)


def differences(old, new):
    return "\n".join(
        difflib.unified_diff(
            e.encoded(old).splitlines(),
            e.encoded(new).splitlines(),
            fromfile="当前已确认",
            tofile="待确认候选",
            lineterm="",
        )
    )


class Repository:
    def __init__(self, root=PROJECTS):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._temporary = tempfile.TemporaryDirectory(prefix="local-draw-session-")
        self.transient_root = Path(self._temporary.name)
        self.transient_ids = set()

    def create_transient(self):
        pid = "tmp-" + uuid.uuid4().hex
        self.transient_ids.add(pid)
        return pid

    def release_transient(self, pid):
        with LOCK:
            e.need(pid in self.transient_ids, "临时会话不存在")
            path = self.transient_root / safe_id(pid)
            e.need(path.resolve().is_relative_to(self.transient_root.resolve()), "无效会话目录")
            if path.exists():
                shutil.rmtree(path)
            self.transient_ids.discard(pid)

    def prompt_workspace_dir(self):
        return self.root.parent / "prompt-workspace"

    def has_prompt_workspace(self):
        return (self.prompt_workspace_dir() / "project.json").is_file()

    def folder(self, pid):
        if pid == PROMPT_WORKSPACE_ID:
            return self.prompt_workspace_dir()
        if isinstance(pid, str) and pid.startswith("tmp-"):
            e.need(pid in self.transient_ids, "临时出图已结束，请重新输入提示词")
            return self.transient_root / safe_id(pid)
        path = self.root / safe_id(pid)
        from .task_control import local
        e.need(not (path / "deleted.json").exists() or getattr(local, "project", None) == pid,
               "作品已在回收站，请先恢复")
        return path

    @contextmanager
    def task_access(self, pid):
        """Only the owning background task may keep using a trashed work."""
        from .task_control import local
        previous = getattr(local, "project", None)
        local.project = safe_id(pid)
        try:
            yield
        finally:
            local.project = previous

    def trash(self):
        return sorted(
            [e.read(path) for path in self.root.glob("*/deleted.json")],
            key=lambda item: item["deleted_at"], reverse=True,
        )

    def delete(self, pid, base_hash):
        """Hide a project atomically; retain every file in place for restoration."""
        with LOCK:
            p = self.get(pid)
            e.need(e.digest(p) == base_hash, "作品已变化，请刷新后再删除")
            folder = self.folder(pid)
            record = {"id": pid, "title": p["brief"]["title"], "deleted_at": e.stamp()}
            e.save(folder / "deleted.json", record)
            return record

    def restore_deleted(self, pid):
        with LOCK:
            folder = self.root / safe_id(pid)
            marker = folder / "deleted.json"
            e.need(marker.is_file(), "作品不在回收站")
            e.need((folder / "project.json").is_file(), "作品文件缺失，无法恢复")
            marker.unlink()
            return {"project": pid}

    def delete_many(self, items):
        """Validate the complete selection before moving any work to trash."""
        with LOCK:
            e.need(isinstance(items, list) and 0 < len(items) <= 500, "请选择 1–500 个作品")
            e.need(all(isinstance(item, dict) for item in items), "无效作品列表")
            ids = [safe_id(item.get("id")) for item in items]
            e.need(len(set(ids)) == len(ids), "作品列表重复")
            for pid, item in zip(ids, items):
                e.need(not pid.startswith("tmp-") and pid != PROMPT_WORKSPACE_ID, "临时出图不属于作品库")
                e.need(e.digest(self.get(pid)) == item.get("base_hash"), "作品已变化，请刷新后再删除")
            records = []
            try:
                for pid, item in zip(ids, items):
                    records.append(self.delete(pid, item["base_hash"]))
            except Exception:
                for record in records:
                    (self.root / record["id"] / "deleted.json").unlink(missing_ok=True)
                raise
            return {"deleted": records}

    def get(self, pid):
        return e.read(self.folder(pid) / "project.json")

    def listing(self):
        result = []
        for path in sorted(self.root.glob("*/project.json")):
            if (path.parent / "deleted.json").exists():
                continue
            p = e.read(path)
            result.append(
                {
                    "id": path.parent.name,
                    "title": p["brief"]["title"],
                    "version": p["version"],
                    "hash": e.digest(p),
                    "quick_sample": bool(p.get("quick_sample")),
                    "provenance": p.get("provenance", "未标记来源"),
                    "pages": len(p["pages"]),
                }
            )
        return result

    def create(self, brief, sample=False):
        with LOCK:
            pid = "story-" + uuid.uuid4().hex[:12]
            if sample:
                p = e.read(ROOT / "config/example.json")
                p["provenance"] = "人工编排结构测试样例；不是本地模型输出"
            else:
                e.schema("brief", brief)
                p = {
                    "brief": brief,
                    "canon": None,
                    "options": None,
                    "outline": None,
                    "plan": None,
                    "pages": {},
                    "provenance": "用户创作项目；具体生成记录见候选与版本历史",
                }
            p["version"] = 1
            e.validate_project(p)
            e.save(self.folder(pid) / "project.json", p)
            return pid

    def draft(self, pid, stage, target, payload, base_hash):
        with LOCK:
            e.need(stage in e.SCHEMAS, "未知阶段")
            e.need(base_hash == e.digest(self.get(pid)), "项目已变化，请刷新后重做草稿")
            name = safe_id(stage + "-" + (target or "main"))
            e.save(
                self.folder(pid) / "drafts" / (name + ".json"),
                {
                    "stage": stage,
                    "target": target,
                    "payload": payload,
                    "base_hash": base_hash,
                },
            )

    def drafts(self, pid):
        return [e.read(f) for f in sorted((self.folder(pid) / "drafts").glob("*.json"))]

    def propose(
        self, pid, stage, target, payload, base_hash, source="手动编辑", metadata=None
    ):
        with LOCK:
            p = self.get(pid)
            e.need(base_hash == e.digest(p), "候选基于旧版本，请刷新后重新审核")
            q = e.merged(p, stage, target, payload)
            cid = uuid.uuid4().hex
            candidate = {
                "id": cid,
                "stage": stage,
                "target": target,
                "payload": payload,
                "base_hash": base_hash,
                "before_payload": stage_value(p, stage, target),
                "source": source,
                "created": e.stamp(),
                "warnings": e.validate_project(q),
                "metadata": metadata or {},
                "diff": differences(stage_value(p, stage, target), payload),
            }
            e.save(self.folder(pid) / "candidates" / (cid + ".json"), candidate)
            return candidate

    def candidates(self, pid):
        p = self.get(pid)
        result = []
        for path in sorted(
            (self.folder(pid) / "candidates").glob("*.json"), reverse=True
        ):
            c = e.read(path)
            decisions = self.folder(pid) / "review-decisions" / (c["id"] + ".json")
            c["review_decisions"] = e.read(decisions) if decisions.exists() else {}
            c["stale"] = c["base_hash"] != e.digest(p)
            if not c["stale"]:
                try:
                    e.merged(p, c["stage"], c["target"], c["payload"])
                except (ValueError, e.jsonschema.ValidationError) as exc:
                    c["invalid_reason"] = str(exc)
            result.append(c)
        return sorted(result, key=lambda c: c["created"], reverse=True)

    def commit(self, pid, q, base_hash, reason):
        """The atomic project file is the commit point; history is saved first."""
        with LOCK:
            p = self.get(pid)
            e.need(e.digest(p) == base_hash, "项目已变化，拒绝覆盖新版本")
            e.validate_project(q)
            q = copy.deepcopy(q)
            q["version"] = p["version"] + 1
            q["last_change"] = {"reason": reason, "time": e.stamp()}
            e.save(self.folder(pid) / "history" / f"v{p['version']:06}.json", p)
            e.save(self.folder(pid) / "project.json", q)
            return q

    def accept(self, pid, cid):
        with LOCK:
            c = e.read(self.folder(pid) / "candidates" / (safe_id(cid) + ".json"))
            e.need(c["stage"] != "audit", "审核仅保存报告，不修改原稿")
            p = self.get(pid)
            e.need(c["base_hash"] == e.digest(p), "候选已过期，请重新生成或编辑后提交")
            q = e.merged(p, c["stage"], c["target"], c["payload"])
            return self.commit(
                pid,
                q,
                c["base_hash"],
                {
                    "candidate": cid,
                    "stage": c["stage"],
                    "target": c["target"],
                    "source": c["source"],
                },
            )

    def history(self, pid):
        return [
            {
                "id": f.stem,
                "version": (p := e.read(f))["version"],
                "last_change": p.get("last_change"),
            }
            for f in sorted((self.folder(pid) / "history").glob("*.json"), reverse=True)
        ]

    def restore(self, pid, revision, base_hash):
        old = e.read(self.folder(pid) / "history" / (safe_id(revision) + ".json"))
        return self.commit(pid, old, base_hash, {"restore": revision})

    def fork(self, pid, reset_from):
        with LOCK:
            seq = ["brief", "canon", "options", "outline", "plan", "pages"]
            e.need(reset_from in seq, "无效分支起点")
            p = copy.deepcopy(self.get(pid))
            for key in seq[seq.index(reset_from) :]:
                if key != "brief":
                    p[key] = {} if key == "pages" else None
            p.pop("drawing_prompts", None)
            p["version"] = 1
            p["last_change"] = {
                "forked_from": pid,
                "base_hash": e.digest(self.get(pid)),
                "reset_from": reset_from,
            }
            p["brief"]["title"] += " · 分支"
            e.validate_project(p)
            new_id = "story-" + uuid.uuid4().hex[:12]
            e.save(self.folder(new_id) / "project.json", p)
            return new_id

    def export(self, pid):
        with LOCK:
            p = self.get(pid)
            e.need(p.get("canon"), "请先确认角色与世界观")
            # Every export is an immutable snapshot. A partial export never changes older bundles.
            bundle = f"v{p['version']:06}-{uuid.uuid4().hex[:8]}"
            folder = self.folder(pid) / "exports" / bundle
            e.export(p, folder)
            e.save(folder / "props.json", p["canon"]["props"])
            e.save(
                folder / "manifest.json",
                {
                    "version": p["version"],
                    "project_sha256": e.digest(p),
                    "complete": len(p["pages"]) == p["brief"]["page_count"],
                    "provenance": p.get("provenance"),
                    "created": e.stamp(),
                },
            )
            return {
                "bundle": bundle,
                "files": [x.name for x in sorted(folder.iterdir())],
            }
