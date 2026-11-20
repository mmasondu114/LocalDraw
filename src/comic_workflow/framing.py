"""Visible-area contracts and reproducible spatial guidance for SDXL."""

import re
from PIL import Image, ImageDraw
from .installed_assets import models as model_directory
from . import engine as e
from .paths import ROOT, DATA
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


def explicit_full_body(text):
    """Recognize requested full-body framing without treating exclusions as requests."""
    full = r'\bfull[ -]body\b|\bhead[ -]to[ -]toe\b|全身|从头到脚'
    text = re.sub(r'(?:\b(?:not|no|without|avoid)\s+(?:a\s+)?|不要|不画|不是|不需要|避免|非)\s*(?:' + full + r')', '', text, flags=re.I)
    if explicit_upper_body(text) or re.search(r'\b(?:headshot|close[ -]up|hands?[ -]only)\b|特写', text, re.I):
        return False
    return bool(re.search(full, text, re.I))


def explicit_upper_body(text):
    upper = r'\b(?:waist[ -]up|upper[ -]body|half[ -]body)\b|半身|腰部以上'
    text = re.sub(r'(?:\b(?:not|no|without|avoid)\s+(?:a\s+)?|不要|不画|不是|不需要|避免|非)\s*(?:' + upper + r')', '', text, flags=re.I)
    return bool(re.search(upper, text, re.I))


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
        # Full costume anchors previously turned hand-only closeups into portraits.
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


def guide_image(payload, width, height):
    frame = payload.get("framing")
    if not frame or not (frame["guide_lines"] or frame["guide_ellipses"]):
        return None
    digest = e.digest({"framing": frame, "size": [width, height]})[:24]
    target = DATA / "comfy-input/guides" / (digest + ".png")
    target.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (width, height), "black")
    draw = ImageDraw.Draw(img)
    stroke = max(3, round(min(width, height) / 160))
    for line in frame["guide_lines"]:
        draw.line(
            [(round(x * (width - 1)), round(y * (height - 1))) for x, y in line],
            fill="white",
            width=stroke,
            joint="curve",
        )
    for x1, y1, x2, y2 in frame["guide_ellipses"]:
        draw.ellipse(
            (x1 * width, y1 * height, x2 * width, y2 * height),
            outline="white",
            width=stroke,
        )
    img.save(target)
    return "guides/" + target.name


def apply_control(graph, guide, strength=None, control_type="hed/pidi/scribble/ted"):
    if not guide:
        return
    cfg = e.read(ROOT / "config/control-model.json")
    path = model_directory(ROOT) / "controlnet" / cfg["name"]
    e.need(
        path.is_file() and path.stat().st_size == cfg["size"],
        "构图控制模型未安装完整，请运行 09-download-control.bat",
    )
    graph["200"] = {
        "class_type": "ControlNetLoader",
        "inputs": {"control_net_name": cfg["name"]},
    }
    graph["201"] = {
        "class_type": "SetUnionControlNetType",
        "inputs": {"control_net": ["200", 0], "type": control_type},
    }
    graph["202"] = {"class_type": "LoadImage", "inputs": {"image": guide}}
    graph["203"] = {
        "class_type": "ControlNetApplyAdvanced",
        "inputs": {
            "positive": graph["5"]["inputs"]["positive"],
            "negative": graph["5"]["inputs"]["negative"],
            "control_net": ["201", 0],
            "image": ["202", 0],
            "strength": cfg["strength"] if strength is None else strength,
            "start_percent": 0.0,
            "end_percent": cfg["end_percent"],
        },
    }
    graph["5"]["inputs"].update(positive=["203", 0], negative=["203", 1])


def apply_local_repair(graph, original, mask, instruction, blend_mask=None):
    """Denoise the mask and composite original pixels back outside it."""
    graph["210"] = {"class_type": "LoadImage", "inputs": {"image": original}}
    graph["211"] = {"class_type": "LoadImage", "inputs": {"image": mask}}
    graph["212"] = {
        "class_type": "ImageToMask",
        "inputs": {"image": ["211", 0], "channel": "red"},
    }
    graph["213"] = {
        "class_type": "VAEEncodeForInpaint",
        "inputs": {
            "pixels": ["210", 0],
            "vae": ["1", 2],
            "mask": ["212", 0],
            "grow_mask_by": 8,
        },
    }
    graph["5"]["inputs"].update(latent_image=["213", 0], denoise=0.8)
    graph["2"]["inputs"]["text"] = instruction + ", " + graph["2"]["inputs"]["text"]
    graph["214"] = {
        "class_type": "ImageCompositeMasked",
        "inputs": {
            "destination": ["210", 0],
            "source": ["6", 0],
            "x": 0,
            "y": 0,
            "resize_source": False,
            "mask": ["212", 0],
        },
    }
    graph["7"]["inputs"]["images"] = ["214", 0]
    if blend_mask:
        graph['215'] = {'class_type': 'LoadImage', 'inputs': {'image': blend_mask}}
        graph['216'] = {'class_type': 'ImageToMask', 'inputs': {'image': ['215',0], 'channel': 'red'}}
        graph['214']['inputs']['mask'] = ['216',0]
