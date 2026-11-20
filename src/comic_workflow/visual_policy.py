"""Explicit, logged staging choices for a fully covered character face."""

import copy
import re


def apply_visual_policy(page, project):
    result = copy.deepcopy(page)
    covered = set(project.get("visual_policy", {}).get("fully_covered_faces", []))
    names = {c["id"]: c["name"] for c in project["canon"]["characters"]}
    changes = []
    for panel in result["panels"]:
        actors = [c for c in panel["characters"] if c["character_id"] in covered]
        if not actors:
            continue
        before = copy.deepcopy(panel)
        for actor in actors:
            mood = re.split(r"[（(]", actor["expression"])[0].split(
                "以头部和肩部姿态表达"
            )[0]
            if re.search(r"眼|眉|嘴|唇|笑|面部|五官", mood):
                mood = "当前情绪"
            actor["expression"] = (
                f"{mood}以头部和肩部姿态表达；兜帽和黑色面罩完全遮蔽面部，不露五官"
            )
            if "进食动作发生在画外" in actor["action"]:
                pass
            elif re.search(r"吃|咬|吞|咀嚼|入口|摘.*面罩|取下.*面罩", actor["action"]):
                actor["action"] = (
                    "背对镜头，原文进食动作发生在画外；以手部和食物状态表达同一事件，面罩遮蔽五官"
                )
            elif re.search(r"眼|眉|嘴|唇|笑", actor["action"]):
                actor["action"] = re.sub(
                    r"[^。；]*[眼眉嘴唇笑][^。；]*[。；]?",
                    "以头部和肩部姿态回应；",
                    actor["action"],
                )
        if re.search(r"眼|眉|嘴|唇|面部|五官", panel["composition"]):
            panel["composition"] = "；".join(
                names[c["character_id"]] + "：" + c["position"]
                for c in panel["characters"]
            )
        # Rebuild only the neutral image description from the structured instant.
        # Do not carry the rejected facial detail forward into the drawing prompt.
        panel["drawing_description"] = "；".join(
            [panel["shot"], panel["angle"], panel["composition"], panel["background"]]
            + [
                names[c["character_id"]] + "：" + c["action"] + "，" + c["expression"]
                for c in panel["characters"]
            ]
        )
        if panel != before:
            changes.append(
                {
                    "panel": panel["id"],
                    "before": before,
                    "after": copy.deepcopy(panel),
                    "reason": "已记录的完全遮脸取景策略；原文及对白不改",
                }
            )
    return result, changes
