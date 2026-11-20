"""Local GPU prompt drafts; confirmed story and imported prompts stay untouched."""

import copy
import re

from . import creative, engine as e
from .batch_drawing import story_fingerprint
from .prompt_exchange import export_storyboard
from .prompt_preflight import WRITING_GUIDANCE
from .repository import safe_id
from .task_control import checkpoint, TaskCancelled
from .paths import prompt_model


PROMPT = """Convert the supplied confirmed comic panel into standalone English image prompts.
Return only the requested JSON: panel_id, positive, negative. Do not rewrite the story.
Use adjacent panels only to understand continuity; draw ONLY current_panel.
Start with its camera framing, visible-person count and single main action.
Keep each character's position, appearance and action in a separate sentence.
Describe only body parts within the frame: no trousers or boots in a face close-up.
Preserve the current clothing, object ownership and exact prop count. An exchanged
object is ONE shared object, not one for each person. Do not invent additional cast.
Avoid broad negative terms that suppress the intended action (e.g. physical contact
for a handover). Do not put an intended character trait in the global negative.
Do not invent style exclusions such as cartoon style, anime or illustration in negative.
For negative use an empty string or only the short watermark/text exclusions permitted by the schema.
Never enumerate unrelated objects, art styles, natural phenomena, or body parts in negative.
Use concise natural English, at most 180 words for positive and 50 for negative.
Do not include dialogue text, commentary, LoRA syntax or requests to draw a comic page.
""" + "\n".join(WRITING_GUIDANCE)


def current_draft(repo, pid):
    folder = repo.folder(pid) / "local-prompts"
    if not (folder / "current.json").exists():
        return {"draft": None}
    draft_id = safe_id(e.read(folder / "current.json")["id"])
    draft = e.read(folder / draft_id / "draft.json")
    return {"draft": draft,
            "stale": draft["document"]["storyboard_hash"] != story_fingerprint(repo.get(pid))}


def panel_context(document, index):
    rows = document["panels"]
    # Preserve this panel's complete visual data; remove duplicated raw card source.
    current = copy.deepcopy(rows[index])
    for role in current["storyboard"]["characters"]:
        role["character"].get("saved_character", {}).pop("source", None)
    def summary(row):
        board = row["storyboard"]
        return {"panel_id": row["panel_id"], "action": board["action"],
                "shot": board["shot"], "description": board["description"]}
    return {"title": document["title"], "current_panel": current,
            "previous_panel": summary(rows[index - 1]) if index else None,
            "next_panel": summary(rows[index + 1]) if index + 1 < len(rows) else None}


@prompt_model()
def generate_draft(app, pid, request, update, jid):
    draft_id = safe_id(request["draft_id"])
    folder = app.repo.folder(pid) / "local-prompts" / draft_id
    path = folder / "draft.json"
    document = export_storyboard(app.repo, pid)
    e.need(document["storyboard_hash"] == request["storyboard_hash"],
           "分镜或角色已变化，请重新点击本地 Qwen 填写；已有草稿保留")
    if path.exists():
        draft = e.read(path)
        e.need(draft["document"]["storyboard_hash"] == document["storyboard_hash"], "草稿分镜版本不符")
    else:
        draft = {"id": draft_id, "created": e.stamp(), "device": "gpu",
                 "status": "running", "document": document, "completed": 0}
    draft.update(status="running", device="gpu")
    draft.pop("error", None)
    e.save(path, draft)
    e.save(folder.parent / "current.json", {"id": draft_id})
    rows = draft["document"]["panels"]
    try:
        checkpoint()
        update("正在加载本地 Qwen，使用 GPU 逐格填写提示词")
        # Reuse the standard GPU lifecycle, including freeing idle drawing weights.
        app.prepare_language(update=update)
        for index, row in enumerate(rows):
            checkpoint()
            e.need(story_fingerprint(app.repo.get(pid)) == request["storyboard_hash"],
                   "分镜或角色已变化，已停止填写；已有草稿保留，请重新生成")
            if row["positive"].strip():
                continue
            update({"message": f"Qwen 正在填写 {row['panel_id']}（{index + 1}/{len(rows)}）",
                    "progress": {"completed": draft["completed"], "total": len(rows), "unit": "格"}})
            schema = {"type": "object", "additionalProperties": False,
                      "required": ["panel_id", "positive", "negative"], "properties": {
                          "panel_id": {"type": "string", "const": row["panel_id"]},
                          "positive": {"type": "string", "minLength": 1, "maxLength": 1800, "pattern": "^[ -~]*$"},
                          "negative": {"type": "string", "enum": ["", "text, watermark", "text, watermark, speech bubbles, comic borders"]}}}
            result, _ = creative.structured(PROMPT, panel_context(document, index), schema,
                                            folder / row["panel_id"] / jid, update,
                                            max_tokens=1200, language_policy="")
            e.jsonschema.validate(result, schema)
            e.need(result["positive"].strip(), f"{row['panel_id']} 的正向提示词为空")
            e.need(not re.search(r"[\u4e00-\u9fff]", result["positive"] + result["negative"]),
                   f"{row['panel_id']} 未完整转换为英文，已有草稿保留")
            row.update(positive=result["positive"], negative=result["negative"])
            draft.update(completed=sum(bool(r["positive"].strip()) for r in rows), status="running")
            e.save(path, draft)
            update({"progress": {"completed": draft["completed"], "total": len(rows), "unit": "格"}})
        checkpoint()
        e.need(story_fingerprint(app.repo.get(pid)) == request["storyboard_hash"], "分镜已变化，请重新生成")
        draft.update(status="complete")
        e.save(path, draft)
        return {"draft_id": draft_id, "completed": len(rows),
                "completion_message": "本地提示词已填写，请核对并确认使用；尚未出图"}
    except TaskCancelled:
        draft.update(status="cancelled")
        e.save(path, draft)
        raise
    except Exception as exc:
        draft.update(status="failed", error=str(exc))
        e.save(path, draft)
        raise
