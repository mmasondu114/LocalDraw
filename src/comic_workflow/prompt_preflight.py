"""Explain observable prompt/cast conflicts without rewriting confirmed text."""
import os
import re
from collections import defaultdict

from .character_binding import panel_characters


WRITING_GUIDANCE = [
    "漫画默认使用第一人称 POV。从视点角色眼中描述本格，在英文 positive 中写明 first-person POV；用户或已确认分镜明确指定其他视点时按其要求。",
    "每格独立撰写，开头先写景别、角度、实际可见人数和本格唯一主要动作，再写人物外观与背景。不要给所有画格套用相同的双人开头。",
    "不要在每一格重复一大段相同的人物介绍、世界观、场景背景或画质套话。每格只保留辨认当前出镜人物所需的简短外观锚点，把篇幅留给本格独有的画面信息。",
    "每个 positive 会单独送给绘图模型，模型看不到前后格、storyboard 或本文件的说明。必要的发色、耳朵、标志物和当前服装可以简短重复；不得用‘同上’、‘延续上一格’、‘另一个人’代替完整可辨认的画面描述。",
    "只描述 storyboard.characters 中本格可见的人物；单人特写不得带入其他角色的名字或外观。对白中提及的人不等于出镜人物。",
    "每个人的名字、画面位置、发色、眼睛、耳朵、服装和动作放在同一个独立句子中。不得把两人的外观标签合并成一个全局列表。",
    "多人画面明确各人的位置和独有特征归属。只属于某人的特征不要写成全局负向词，否则会同时抑制正确角色的特征。",
    "动作写成能画出的一个瞬间：谁在画面哪侧、站着还是坐着、朝向哪里、哪只手接触什么、视线看向哪里。不要把连续多个动作塞进一张图，也不要只写‘两人互动’之类的概括。",
    "道具要写清数量和持有者；交接同一件物品时说明双方接触的是同一件，避免每人各拿一件。沿用分镜安排的服装、道具和空间关系，不擅自增添路人、分身、宠物或装饰性人物。",
    "严格落实 storyboard.shot、angle 和 aspect_ratio：特写只描述入镜部位；全身镜头明确头脚入镜；远景说明人物与环境的大小关系。依据相邻分镜的实际差异分别表达，不能只换动作末尾几个词，也不能为了多样性擅自改变已确认的镜头或剧情。",
    "Anima 系列的镜头、动作和外观统一写成清晰的英文，中文说明留在 storyboard 中；不要只翻译人物背景而留下中文动作。",
    "按本格状态描述服装与道具，不要机械复制角色卡中的默认衣服。只写可见画面，不用内心独白代替动作。",
    "dialogue 和 purpose 用于理解情节与情绪，不要要求绘图模型把对白、旁白、画格编号或解释文字画进图片，除非分镜明确要求画面内的招牌等文字。",
    "negative 只写本格需要排除的画面错误，可以为空；不要复制与本格无关的长串负向词，不要排除本格正确人物需要的特征。例如有人需要狼耳时，不得把 wolf ears 放进全图 negative。",
    "同格增加候选数量仅改变随机种子，不会自动产生不同镜头。先用少量单人、双人和不同景别的小样核对，再批量绘制。",
]


def visual_cast(project, panel):
    fields = ("identity", "face", "hair", "eyes", "body", "clothing", "accessories", "palette", "marks")
    result = []
    for person in panel_characters(project, panel):
        profile = person.get("saved_character", {}).get("profile", {})
        result.append({"id": person["id"], "name": person["name"],
                       "appearance": {k: profile[k] for k in fields if profile.get(k) and profile[k] != "unknown"}})
    return result


def _aliases(person):
    names = {person["name"].lstrip("/")}
    identity = person.get("saved_character", {}).get("profile", {}).get("identity", "")
    # Only explicit saved identity names, not guessed nicknames or appearance tags.
    for part in identity.split(","):
        name = re.sub(r"\([^)]*\)", "", part).strip()
        if name and name.casefold() != "unknown":
            names.add(name)
    return names


def _mentioned(text, alias):
    return bool(re.search(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", text, re.I)) if alias.isascii() else alias in text


def analyze(project, rows):
    """Heuristics are advisory, never proof of model failure or a write operation."""
    panels = {p["id"]: p for page in project.get("pages", {}).values() for p in page["panels"]}
    all_people = panel_characters(project, {"characters": [{"character_id": c["id"]}
        for c in project.get("canon", {}).get("characters", [])]})
    warnings, details, mixed = [], {}, []
    grouped = defaultdict(list)
    active = [r for r in rows if isinstance(r.get("positive"), str) and r["positive"].strip()]
    for row in rows:
        key = row["panel_id"]
        panel = panels.get(key)
        if not panel:
            continue
        cast = visual_cast(project, panel)
        details[key] = {"visible_characters": cast, "shot": panel.get("shot", ""), "angle": panel.get("angle", "")}
        text = row.get("positive", "")
        if not isinstance(text, str) or not text.strip():
            continue
        grouped[re.sub(r"\s+", " ", text).strip().casefold()].append(key)
        visible = {c["id"] for c in cast}
        absent = [c["name"] for c in all_people if c["id"] not in visible and any(_mentioned(text, a) for a in _aliases(c))]
        if absent:
            warnings.append({"code": "absent_character", "panels": [key],
                "message": f"{key} 的分镜未安排 {'、'.join(absent)} 出镜，但正向提示词提到了该角色；请核对人数及特征归属。"})
        if re.search(r"[\u4e00-\u9fff]", text) and re.search(r"[A-Za-z]{3,}", text):
            mixed.append(key)
    if mixed:
        warnings.append({"code": "mixed_language", "panels": mixed,
            "message": f"{len(mixed)} 格的绘图描述中英混写；请核对镜头和动作是否也已转换为所用模型适合的表达。"})
    for keys in grouped.values():
        if len(keys) > 1:
            warnings.append({"code": "identical_prompts", "panels": keys,
                "message": f"{'、'.join(keys)} 使用完全相同的正向提示词；不同的文字分镜不会另外传给绘图模型。"})
    if len(active) >= 2:
        prefix = os.path.commonprefix([r["positive"].strip() for r in active])
        if len(prefix) >= 160 and len(prefix) >= min(len(r["positive"].strip()) for r in active) * .3:
            warnings.append({"code": "shared_prefix", "panels": [r["panel_id"] for r in active],
                "message": f"这些画格的开头有 {len(prefix)} 个相同字符；请把各格独有的景别、人数和动作提前，核对是否误用了相同人物组合。"})
    return {"warnings": warnings, "panel_details": details, "unique_positive_count": len(grouped)}


def with_render_plan(repo, pid, project, rows, checkpoint=None):
    from . import comfyui, drawing_settings
    result = analyze(project, rows)
    prefs = drawing_settings.load(repo, pid)
    if not prefs:
        return result
    prefs = {**prefs, "checkpoint": checkpoint or prefs["checkpoint"]}
    labels = comfyui.lora_labels() if prefs["mode"] != "none" else {}
    result["checkpoint"] = prefs["checkpoint"]
    missing = defaultdict(list)
    # Routing depends on the visible cast, not the shot/action. Share results
    # only within this snapshot; never cache across user setting changes.
    plans = {}
    for page in project.get("pages", {}).values():
        for panel in page["panels"]:
            info = result["panel_details"].get(panel["id"])
            if info is None:
                continue
            try:
                cast_key = tuple(sorted(c["id"] for c in info["visible_characters"]))
                if cast_key not in plans:
                    plans[cast_key] = drawing_settings.loras_for_panel(prefs, project, panel, labels=labels)
                loras = plans[cast_key]
            except ValueError as exc:
                result["warnings"].append({"code": "lora_configuration", "panels": [panel["id"]], "message": str(exc)})
                continue
            info["loras"] = [{"label": l.get("label", l["file"]), "weight": l["weight"],
                              "character_ids": l.get("target_character_ids", [])} for l in loras]
            bound = {key for l in loras for key in l.get("target_character_ids", [])}
            if prefs.get("reference_character_id"):
                from . import anima_consistency
                from .characters import CharacterLibrary
                try:
                    reference = anima_consistency.selected_character_reference(project, panel,
                        prefs["checkpoint"], prefs,
                        library=CharacterLibrary(repo.root.parent / "characters"))
                    if reference:
                        info["reference"] = {k: v for k, v in reference.items() if k != "source"}
                        info["reference"]["scope"] = "whole_image"
                        bound.add(reference["character_id"])
                except ValueError as exc:
                    result["warnings"].append({"code": "reference_configuration",
                        "panels": [panel["id"]], "message": str(exc)})
            if prefs["mode"] != "none":
                for c in info["visible_characters"]:
                    if c["id"] not in bound:
                        missing[c["name"]].append(panel["id"])
    for name, keys in missing.items():
        result["warnings"].append({"code": "text_only_character", "panels": keys,
            "message": f"{name} 在当前模型下没有匹配并启用的专属 LoRA，身份依靠提示词表达（涉及 {len(keys)} 格）；建议先试画核对。"})
    return result
