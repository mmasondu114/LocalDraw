"""LocalDraw's text-only interface to Anima Wardrobe V4."""

import secrets
from functools import lru_cache

from .wardrobe_v4 import anima_v4_rules as rules


@lru_cache(maxsize=1)
def catalog():
    data = rules.characters_data()
    works = {row["en"]: row["zh"] for row in data["works"]}
    return {
        "studio_cast": True,
        "characters": [
            {"tag": row["tag"], "name": row["name"], "zh": row.get("zh", ""),
             "work": works.get(row["work"]) or row["work"]}
            for row in data["characters"]
        ],
        "styles": rules.OS.STYLE_CHOICES,
        "levels": {key: rules.level_label(key) for key in rules.LEVEL_KEYS},
    }


def preview(request):
    scope = request.get("scope", "character")
    style = request.get("style", "不限")
    level = request.get("level", "sfw")
    tag = request.get("character", "")
    seed = request.get("seed")
    if scope not in ("outfit", "character", "scene") or style not in rules.OS.STYLE_CHOICES:
        raise ValueError("请选择有效的生成范围和服装风格。")
    if level not in rules.LEVEL_KEYS:
        raise ValueError("请选择有效的内容分级。")
    if tag and not any(row["tag"] == tag for row in catalog()["characters"]):
        raise ValueError("请重新选择角色。")
    if seed is None:
        seed = secrets.randbelow(2**31)
    if type(seed) is not int or not 0 <= seed < 2**31:
        raise ValueError("种子须为 0 到 2147483647 之间的整数。")
    clothing = {"cloth_sfw", "cloth_nsfw"}
    sections = None if scope == "scene" else clothing
    if not tag and scope == "scene":
        sections = clothing | {"pose_sfw", "pose_nsfw", "camera", "scene", "lighting"}
    result = rules.draw(level=level, seed=seed, selected_roles=[tag] if tag else [],
                        locks={"role"} if not tag else None, enabled_sections=sections, outfit_style=style)
    if scope == "outfit" or not tag:
        fields = {f["key"] for s in rules.sections() if s["id"] in sections for f in s["fields"]}
        tags = rules.collect({k: v for k, v in result["state"].items() if k in fields})
        tags, _ = rules.repair(tags, result["level_num"])
        if not tags:
            raise ValueError("这次没有抽到提示词，请换一个种子。")
        return {"text": ", ".join(tags), "summary": rules.cn_summary(tags), "seed": seed}
    return {"text": result["text"], "summary": result["cn_summary"], "seed": seed}
