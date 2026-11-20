"""Manual opt-in live acceptance: vision, story-first and six reviewed views."""

import base64
import copy
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))
from comic_workflow import engine as e, creative
from comic_workflow.repository import Repository
from comic_workflow.server import Application
from comic_workflow.model import generate
from comic_workflow.reviewed_drawing import reviewed_batch

out = (
    Path(sys.argv[1])
    if len(sys.argv) > 1
    else root / "data/test-results/features-live" / e.stamp()
)
repo = Repository(out / "projects")
app = Application(repo)
report = (
    e.read(out / "report.json")
    if (out / "report.json").exists()
    else {
        "directory": str(out),
        "source": "本机真实模型技术测试，不是用户角色或用户确认作品",
    }
)


def update(text):
    print(text, flush=True)


def save():
    e.save(out / "report.json", report)


try:
    if len(sys.argv) > 1:
        import json

        draft = {
            "payload": json.loads(
                e.read(out / "story/response.json")["choices"][0]["message"]["content"]
            ),
            "mode": "structure",
            "original": "续接技术验收原始请求见 story/request.json",
            "metrics": e.read(out / "story/metrics.json"),
        }
        draft["payload"]["brief"]["character_refs"] = app.characters.listing()
        app.prepare_language(vision=True)
    else:
        image = next(
            (root / "data/projects/acceptance-demo/images").glob("a413*/1.png")
        )
        upload = app.characters.upload(
            "data:image/png;base64," + base64.b64encode(image.read_bytes()).decode()
        )
        app.prepare_language(vision=True)
        character = creative.character_draft(
            app.characters,
            {
                "name": "验收林晴",
                "mode": "image",
                "image_id": upload["image_id"],
                "personality": "24 岁原创成年女性，温和、细心、少言，习惯先观察后行动，不夸大感情。",
            },
            out / "character",
            update,
        )
        saved = app.characters.save(
            "验收林晴", character["profile"], character["source"], upload["image_id"]
        )
        report["vision_character"] = character
        # Deliberately incompatible description: proves the reject path is exercised on a real image.
        review = creative.review_image(
            {
                "main_instant": "空荡室内只有一辆红色自行车，没有人物",
                "characters": [],
                "drawing_description": "空房间中央一辆红色自行车，画面没有人物",
            },
            "empty room, red bicycle, no people",
            image,
            "只有自行车",
            [],
            out / "mismatch-review",
            update,
        )
        report["mismatch_review"] = review
        e.need(review["verdict"] == "reject", "明显不符测试未被拒绝，需检查审核能力")
        save()
        draft = creative.story_draft(
            app.characters,
            {
                "text": "/验收林晴 在雨后的书店门口，蹲下看一只白色纸船从水沟缓缓漂走。她没有碰纸船，静静微笑。只画这个安静的瞬间，没有其他人。",
                "mode": "structure",
                "preferences": "技术测试，只需一页一格，不加对白，保留所有明确事件。",
            },
            out / "story",
            update,
        )
    pid = report.get("project") or creative.create_story(repo, app.characters, draft)
    report["project"] = pid
    report["story"] = {
        "metrics": draft["metrics"],
        "title": draft["payload"]["brief"]["title"],
    }
    p = repo.get(pid)
    save()
    if not p.get("plan"):
        plan, metrics = generate(
            p, "plan", "", "技术验收：一页一格，保持原文。", out / "plan", update
        )
        c = repo.propose(pid, "plan", "", plan, e.digest(p))
        p = repo.accept(pid, c["id"])
    page, metrics = generate(
        p,
        "page",
        "P001",
        "技术验收：保留文本唯一瞬间。",
        out / ("page-" + e.stamp()),
        update,
    )
    c = repo.propose(pid, "page", "P001", page, e.digest(p))
    p = repo.accept(pid, c["id"])
    report["page"] = {"panels": len(page["panels"]), "layout_rows": page["layout_rows"]}
    save()
    report["drawing"] = reviewed_batch(
        app,
        pid,
        {"target": "P001-K01", "count": 6, "mode": "angles"},
        update,
        "six-view-live",
    )
    report["status"] = "passed"
    save()
finally:
    app.model.stop()
    app.comfy.stop()
    save()
