"""Explicit live test; never runs with offline tests or normal application startup."""

import copy
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))
from comic_workflow import engine as e
from comic_workflow.model import generate
from comic_workflow.repository import Repository

folder = root / "data/test-results/live"
repo = Repository(folder / "projects")
report = {
    "status": "running",
    "source": "本机 Qwen 真实生成；技术测试自动确认，不代表用户批准故事",
    "stages": [],
    "gpu_samples_mib": [],
}
stop = threading.Event()


def monitor():
    while not stop.wait(2):
        try:
            val = subprocess.check_output(
                [
                    "nvidia-smi",
                    "--query-gpu=memory.used",
                    "--format=csv,noheader,nounits",
                ],
                text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            report["gpu_samples_mib"].append(int(val.strip()))
        except Exception:
            pass


def main():
    if (folder / "report.json").exists():
        e.save(
            folder / "report-history" / (e.stamp() + ".json"),
            e.read(folder / "report.json"),
        )
    brief = {
        "title": "四镜验收 · 雨后纸船",
        "genre": "二次元日常漫画",
        "direction": "年轻女店员在雨后门前发现一只搁浅的纸船，把它轻轻推回水沟，目送它顺水漂走。一个人物，四个清楚的静止瞬间。",
        "page_count": 1,
        "panels_per_page": 4,
        "reading_direction": "LTR",
        "page_mode": "single_page",
        "first_story_page_side": "right",
        "must_include": ["发现搁浅纸船", "手指轻推纸船", "目送纸船漂走"],
        "avoid": ["说教", "新增人物", "超自然"],
        "character_material": "原创成年女性 C001 林晴，24岁，短棕发、棕色眼睛、白衬衫和深蓝围裙，温和少言。始终穿同一套衣服。",
    }
    resume = folder / "project-id.txt"
    if resume.exists():
        pid = resume.read_text().strip()
    else:
        pid = repo.create(brief)
        resume.parent.mkdir(parents=True, exist_ok=True)
        resume.write_text(pid)
    report["project"] = pid
    threading.Thread(target=monitor, daemon=True).start()
    try:
        for stage, target, instruction in [
            ("canon", "", "只建立必要角色、店门前一个场景、纸船一个道具。"),
            ("options", "", "三个完整方案均为一页四格日常漫画。"),
            ("outline", "", "选择方案 A，完整规划四镜结局。"),
            ("plan", "", "一页四格，不安排跨页钩子。"),
            ("page", "P001", "每格一个瞬间。可用无对白格。四格必须全数输出。"),
            (
                "panel",
                "P001-K02",
                "只改本格表情描写，使其更自然克制；保留其他字段与状态边界。",
            ),
            ("audit", "P001", "逐格检查可画性、道具和角色连续性，给出具体证据。"),
        ] + [
            (
                "drawing_prompt",
                f"P001-K{i:02}",
                "二次元漫画，清晰线稿。遵循角色资料，只保留可见画面。",
            )
            for i in range(1, 5)
        ]:
            p = repo.get(pid)
            done = (
                bool(p["pages"])
                if stage == "page"
                else target in p.get("drawing_prompts", {})
                if stage == "drawing_prompt"
                else bool(p.get(stage))
                if stage in ["canon", "options", "outline", "plan"]
                else False
            )
            if done:
                continue
            print(stage, target, flush=True)
            before = copy.deepcopy(p)
            payload, metrics = generate(
                p,
                stage,
                target,
                instruction,
                folder / "runs" / (stage + "-" + target + "-" + e.stamp()),
                lambda msg: print(msg, flush=True),
            )
            c = repo.propose(
                pid,
                stage,
                target,
                payload,
                e.digest(p),
                "Qwen 技术验收真实输出",
                metrics,
            )
            if stage != "audit":
                repo.accept(pid, c["id"])
            if stage == "panel":
                after = repo.get(pid)
                for i in [0, 2, 3]:
                    assert (
                        before["pages"]["P001"]["panels"][i]
                        == after["pages"]["P001"]["panels"][i]
                    )
                assert [
                    x
                    for x in before["pages"]["P001"]["lettering"]
                    if x["panel_id"] != target
                ] == [
                    x
                    for x in after["pages"]["P001"]["lettering"]
                    if x["panel_id"] != target
                ]
            report["stages"].append(
                {
                    "stage": stage,
                    "target": target,
                    "metrics": metrics,
                    "candidate": c["id"],
                }
            )
            e.save(folder / "report.json", report)
        e.validate_project(repo.get(pid), True)
        repo.export(pid)
        report["status"] = "passed"
    except Exception as exc:
        report.update(status="failed", error=str(exc))
        raise
    finally:
        stop.set()
        report["peak_gpu_used_mib"] = max(report["gpu_samples_mib"], default=None)
        e.save(folder / "report.json", report)


if __name__ == "__main__":
    main()
