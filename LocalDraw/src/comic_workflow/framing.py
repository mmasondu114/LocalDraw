"""Visible-area framing contracts shared by storyboard validation and prompt generation."""

import re
from . import engine as e
from .schemas import obj, arr, S, enum

COORD = {"type": "number", "minimum": 0, "maximum": 1}
POINT = {**arr(COORD, 2), "maxItems": 2}
BOX = {**arr(COORD, 4), "maxItems": 4}
FRAMING = obj(
    {
        "version": {"type": "integer", "const": 2},
        "camera": S,
        "forbidden": S,
        "subjects": arr(
            obj(
                {
                    "character_id": S,
                    "visible": enum("full", "upper", "head", "hands"),
                    "region": BOX,
                }
            )
        ),
        "guide_lines": {**arr({**arr(POINT, 2), "maxItems": 16}), "maxItems": 36},
        "guide_ellipses": {**arr(BOX), "maxItems": 12},
        "guide_note": S,
    }
)


def source_text(project, panel):
    page = project.get("script_pages", {}).get(panel["page_id"])
    return (
        page["panels"][panel["number"] - 1]
        if page
        else project.get("script", project["brief"]["direction"])
    )


def validate_framing(payload):
    frame = payload.get("framing")
    if not frame:
        return
    e.jsonschema.validate(frame, FRAMING)
    ids = [s["character_id"] for s in frame["subjects"]]
    e.need(
        len(ids) == len(set(ids))
        and set(ids) == {a["character_id"] for a in payload["character_anchors"]},
        "可见范围必须逐人对应角色锚点",
    )
    for box in [s["region"] for s in frame["subjects"]] + frame["guide_ellipses"]:
        e.need(box[0] < box[2] and box[1] < box[3], "构图区域坐标须从左上到右下")
    if frame["subjects"] and all(s["visible"] == "hands" for s in frame["subjects"]):
        forbidden = r"\b(hair|halo|eyes|skirt|boots|full.body|portrait|face|head)\b"
        for text in [
            payload["positive"],
            *payload["composition_variants"],
            *[a["tags"] for a in payload["character_anchors"]],
        ]:
            e.need(
                not re.search(forbidden, text, re.I),
                "手部特写正向描述混入镜头外的头脸或全身特征，请只写手套、袖口和接触动作；排除项写 negative",
            )


def apply_camera(positive, negative, payload):
    positive += ", full-bleed scene, background extends to image edges"
    negative += ", white border, blank side margins, letterbox, picture frame"
    frame = payload.get("framing")
    if not frame:
        return positive, negative
    return frame["camera"] + ", " + positive, negative + ", " + frame["forbidden"]
