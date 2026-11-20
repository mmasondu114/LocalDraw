"""Document bridge, not a private Sudowrite API or unattended browser scraper."""

import csv
import io
import json
import uuid
from pathlib import Path
from . import engine as e
from .model import build_context
from .repository import safe_id


def package(repo, pid, stage, target, instruction):
    p = repo.get(pid)
    ctx = build_context(p, stage, target, instruction)
    prompts = Path(__file__).parent / "prompts"
    prompt = (prompts / "common.md").read_text(encoding="utf-8") + "\n"
    if stage == "panel":
        prompt += (prompts / "page.md").read_text(encoding="utf-8") + "\n"
    prompt += (prompts / f"{stage}.md").read_text(encoding="utf-8")
    packet = f"漫画阶段任务：{stage} {target}\n\n{prompt}\n\n当前资料：\n{e.encoded(ctx)}\n\n输出 JSON Schema：\n{e.encoded(e.SCHEMAS[stage])}\n\n请返回一个完整 JSON 对象；不要省略字段、页或格。"
    hid = uuid.uuid4().hex
    handoff = {
        "id": hid,
        "stage": stage,
        "target": target,
        "base_hash": e.digest(p),
        "created": e.stamp(),
        "provider": "sudowrite",
        "packet": packet,
    }
    e.save(repo.folder(pid) / "sudowrite" / (hid + ".json"), handoff)
    return handoff


def import_result(repo, pid, hid, text):
    handoff = e.read(repo.folder(pid) / "sudowrite" / (safe_id(hid) + ".json"))
    # Keep the original, including invalid or prose-only replies, for later manual editing.
    rid = uuid.uuid4().hex
    e.save(
        repo.folder(pid) / "sudowrite" / "responses" / (rid + ".json"),
        {
            "handoff": hid,
            "text": text,
            "created": e.stamp(),
            "source": "用户导入并标记为 Sudowrite 返回；未经服务端来源认证",
        },
    )
    cleaned = text.strip()
    if cleaned.startswith("```") and cleaned.endswith("```"):
        cleaned = "\n".join(cleaned.splitlines()[1:-1])
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "原文已保留，但不是完整 JSON。可在 Sudowrite 按交接资料整理，或在本地内容编辑器逐项填入后提交候选。"
        ) from exc
    return repo.propose(
        pid,
        handoff["stage"],
        handoff["target"],
        payload,
        handoff["base_hash"],
        "Sudowrite 返回（用户导入）",
        {"handoff": hid, "response": rid},
    )


def csv_text(rows):
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    writer.writerows(rows)
    return out.getvalue()


def story_bible(p):
    """Neutral documents compatible with documented text/CSV import paths."""
    c = p.get("canon") or {
        "characters": [],
        "scenes": [],
        "props": [],
        "world_rules": [],
    }
    chars = [
        [
            "Name",
            "Stable ID",
            "Appearance",
            "Voice",
            "Sources",
            "Original facts",
            "Adaptations",
            "Unknowns",
        ]
    ]
    for x in c["characters"]:
        chars.append(
            [
                x["name"],
                x["id"],
                x["appearance"],
                x["voice"],
                "；".join(x["source_notes"]),
                "；".join(x["verified_facts"]),
                "；".join(x["adaptation_choices"]),
                "；".join(x["unknowns"]),
            ]
        )
    outline = [["Chapter", "Summary"]]
    if p.get("outline"):
        outline += [
            [f"情节 {i}", beat] for i, beat in enumerate(p["outline"]["beats"], 1)
        ]
    world = [["Name", "Description"]]
    world += [[x["name"], e.encoded(x)] for x in c["scenes"]]
    world += [[x["id"], x["description"]] for x in c["props"]]
    world += [[f"规则{i}", x] for i, x in enumerate(c["world_rules"], 1)]
    return {
        "brief.txt": e.encoded(p["brief"]),
        "characters.csv": csv_text(chars),
        "outline.csv": csv_text(outline),
        "worldbuilding.csv": csv_text(world),
    }
