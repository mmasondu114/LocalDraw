"""Assistant-reviewed demonstration choices, never user editorial approval."""

import copy
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))
from comic_workflow import engine as e
from comic_workflow.repository import Repository
from comic_workflow.comfyui import drawing_state, select_image, run_batch
from comic_workflow.layout import default_layout, compose

folder = root / "data/test-results/live"
repo = Repository(folder / "projects")
pid = (folder / "project-id.txt").read_text().strip()
if __name__ == "__main__":
    index = drawing_state(repo, pid)
    for target, pick in [
        ("P001-K01", 0),
        ("P001-K02", 0),
        ("P001-K03", 2),
        ("P001-K04", 2),
    ]:
        batch = next(b for b in index["batches"] if b["panel_id"] == target)
        select_image(repo, pid, target, batch["images"][pick]["id"])
    before = copy.deepcopy(drawing_state(repo, pid))
    before_project = repo.get(pid)
    first = compose(repo, pid, "P001", default_layout(before_project["pages"]["P001"]))
    e.save(
        folder / "selection-review.json",
        {
            "reviewer": "assistant",
            "status": "技术演示选图，非用户确认成稿",
            "first_layout": first,
            "issues": [
                "原图含多只纸船，不完全符合单道具状态",
                "发型存在偏差，未安装角色 LoRA 或参考图控制",
                "第二格手指动作需要修正",
                "黑白和彩色混合，正式作品需明确统一画风",
            ],
        },
    )
    prompt = copy.deepcopy(before_project["drawing_prompts"]["P001-K02"]["payload"])
    prompt["positive"] = (
        "close-up, hand only, 1hand, female hand entering from upper left, index finger touching the stern of one small origami paper boat, single white paper boat floating flat on water, top-down view, wet concrete gutter, gentle ripples, anime manga illustration, clean lineart, full color, masterpiece, high score, great score, absurdres"
    )
    prompt["negative"] = (
        "lowres, text, watermark, monochrome, greyscale, multiple boats, fleet, holding boat, lifting boat, giant finger, extra fingers, extra hands, deformed hands, detached fingers, bad anatomy"
    )
    prompt["composition_variants"] = [prompt["positive"]] * 3
    prompt["adaptation_notes"] = (
        "验收审图后的第二格提示词修订，重点约束一只船在水面上、指尖触及船尾；仅测试修正链路，不保证正确。"
    )
    c = repo.propose(
        pid,
        "drawing_prompt",
        "P001-K02",
        prompt,
        e.digest(before_project),
        "助理审图后的测试修订",
    )
    repo.accept(pid, c["id"])
    new = run_batch(repo, pid, "P001-K02", lambda s: print(s, flush=True))
    after = drawing_state(repo, pid)
    for target in ["P001-K01", "P001-K03", "P001-K04"]:
        assert before["selections"][target] == after["selections"][target]
    assert repo.get(pid)["pages"] == before_project["pages"]
    assert len(new["images"]) == 3
    reopened = Repository(folder / "projects")
    assert drawing_state(reopened, pid)["selections"] == after["selections"]
    e.save(
        folder / "redraw-report.json",
        {
            "passed": True,
            "target": "P001-K02",
            "batch": new["id"],
            "other_three_selections_unchanged": True,
            "all_text_panels_unchanged": True,
            "restart_selection_persistence": True,
            "note": "未自动选中新图；待审图。",
        },
    )
