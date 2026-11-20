"""Local SDXL reference conditioning. Reference files and regional masks are logged."""

import shutil
import re
from PIL import Image
from . import engine as e
from .paths import ROOT, DATA
from .character_binding import panel_characters
from .characters import CharacterLibrary
from .framing import explicit_full_body


def white_reference_background(path):
    """Inspect unchanged image corners; this is pixel processing, not model inference."""
    with Image.open(path) as image:
        image = image.convert('RGB')
        corners = ((0, 0), (image.width - 1, 0), (0, image.height - 1),
                   (image.width - 1, image.height - 1))
        return all(min(image.getpixel(point)) >= 248 for point in corners)


def references_for_panel(project, panel, framing=None):
    config_path = ROOT / "config/reference-models.json"
    if not config_path.exists() or not e.read(config_path).get("enabled"):
        return []
    cfg = e.read(config_path)
    # The direct-prompt entry has no structured framing payload. Honor its
    # explicit full-body request with the same reference limits used by plans.
    direct_text = project.get('prompt_input', {}).get('text', '') if project.get('quick_sample') else ''
    constrained_framing = bool(framing) or explicit_full_body(direct_text)
    # The shorter portrait-reference schedule was validated for one/two people.
    # Applying it to an already verified larger standing scene washed out its
    # background. Keep that scene's original configured schedule.
    larger_standing_scene = (not framing and len(panel['characters']) > 2
                             and explicit_full_body(direct_text)
                             and re.search(r'\bstanding\b|站立|站着', direct_text, re.I)
                             and not re.search(r'\b(sitting|seated|kneeling|crouching)\b|坐着|跪', direct_text, re.I))
    references = []
    positions = {c["character_id"]: c["position"] for c in panel["characters"]}
    # Direct drawing can reference one person's silhouette while changing
    # another person's outfit. Old projects retain their all-selected behavior.
    reference_choices = {
        item["name"]: item
        for item in project.get("prompt_input", {}).get("characters", [])
    } if project.get("quick_sample") else {}
    for character in panel_characters(project, panel):
        choice = reference_choices.get(character["name"], {})
        if choice.get("use_reference", True) is False:
            continue
        subject = next(
            (
                s
                for s in (framing or {}).get("subjects", [])
                if s["character_id"] == character["id"]
            ),
            None,
        )
        # A full-body portrait is not a hand reference. Without an explicit
        # user-approved crop, omit image conditioning instead of guessing a crop.
        if subject and subject["visible"] == "hands":
            continue
        card = character.get("saved_character", {})
        image_id = card.get("reference_image_id")
        if not image_id:
            continue
        image = CharacterLibrary().image_path(image_id)
        e.need(image.is_file(), f"{character['name']} 的参考图不存在")
        target = DATA / "comfy-input" / "characters" / image.name
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() or target.read_bytes() != image.read_bytes():
            shutil.copyfile(image, target)
        references.append(
            {
                "character_id": character["id"],
                "name": character["name"],
                "image": "characters/" + image.name,
                "image_hash": e.digest(image.read_bytes().hex()),
                "white_background": white_reference_background(image),
                "position": positions[character["id"]],
                "weight": min(cfg["weight"], 0.3) if constrained_framing else cfg["weight"],
                "end_at": min(cfg["end_at"], 0.65) if constrained_framing and not larger_standing_scene else cfg["end_at"],
                "start_at": cfg.get('start_at', 0.0),
                "region": subject["region"] if subject else choice.get("region"),
            }
        )
    return references


def apply_references(graph, references, cfg):
    if not references:
        return graph
    graph["100"] = {
        "class_type": "CLIPVisionLoader",
        "inputs": {"clip_name": "CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors"},
    }
    graph["101"] = {
        "class_type": "IPAdapterModelLoader",
        "inputs": {"ipadapter_file": "ip-adapter-plus_sdxl_vit-h.safetensors"},
    }
    model = graph["5"]["inputs"]["model"]
    width, height = cfg["width"], cfg["height"]
    # The author's image_negative suppresses white margins in the reviewed
    # seated-pair and opt-in joint standing recipes. Original reference pixels
    # are preserved, and references with colored backgrounds are excluded.
    scene_meta = graph.get('290', {}).get('_meta', {})
    seated_pair = (scene_meta.get('pose') == 'seated'
                   and len(scene_meta.get('characters', [])) == 2
                   and len(references) == 2)
    joint_standing = (scene_meta.get('pose') == 'standing'
                      and scene_meta.get('scene_share', 0.) > 0.
                      and len(scene_meta.get('characters', [])) >= 3
                      and len(references) == len(scene_meta.get('characters', [])))
    joint_interaction = (scene_meta.get('interaction') in ('hugging', 'mutual_pointing')
                         and scene_meta.get('scene_share', 0.) > 0.
                         and len(scene_meta.get('characters', [])) == 2
                         and len(references) == 2)
    white_negative = ((seated_pair or joint_standing or joint_interaction)
                      and all(reference.get('white_background') for reference in references))
    if white_negative:
        graph['reference-white-background'] = {'class_type': 'EmptyImage', 'inputs': {
            'width': 512, 'height': 512, 'batch_size': 1, 'color': 16777215}}
    # Position hints provide soft attention regions; they are not a segmentation guarantee.
    for i, reference in enumerate(references):
        base = 110 + i * 10
        image_id, adapter_id = str(base), str(base + 1)
        graph[image_id] = {
            "class_type": "LoadImage",
            "inputs": {"image": reference["image"]},
        }
        inputs = {
            "model": model,
            "ipadapter": ["101", 0],
            "clip_vision": ["100", 0],
            "image": [image_id, 0],
            "weight": reference["weight"],
            "weight_type": "linear",
            "combine_embeds": "concat",
            "start_at": reference.get('start_at', 0.0),
            "end_at": reference["end_at"],
            "embeds_scaling": "V only",
        }
        if scene_meta.get('interaction') == 'hugging' and 'regional_hug_reference_start' in cfg:
            start = cfg['regional_hug_reference_start']
            e.need(type(start) in (int, float) and 0. <= start < inputs['end_at'], '拥抱参考图介入时间无效')
            inputs['start_at'] = max(inputs['start_at'], start)
        if white_negative:
            inputs['image_negative'] = ['reference-white-background', 0]
        if reference.get("region"):
            x1, y1, x2, y2 = reference["region"]
            x, y = round(width * x1), round(height * y1)
            rw, rh = max(1, round(width * (x2 - x1))), max(1, round(height * (y2 - y1)))
            blank, region, merged = [str(base + k) for k in (2, 3, 5)]
            graph[blank] = {
                "class_type": "SolidMask",
                "inputs": {"value": 0.0, "width": width, "height": height},
            }
            graph[region] = {
                "class_type": "SolidMask",
                "inputs": {"value": 1.0, "width": rw, "height": rh},
            }
            graph[merged] = {
                "class_type": "MaskComposite",
                "inputs": {
                    "destination": [blank, 0],
                    "source": [region, 0],
                    "x": x,
                    "y": y,
                    "operation": "add",
                },
            }
            inputs["attn_mask"] = [merged, 0]
        elif len(references) > 1:
            pos = reference["position"]
            if "左" in pos and "右" not in pos:
                x, fraction = 0, 0.6
            elif "右" in pos and "左" not in pos:
                x, fraction = int(width * 0.4), 0.6
            else:
                x, fraction = int(width * i / len(references)), 1 / len(references)
            region_width = min(width - x, int(width * fraction))
            blank, region, feather, merged = [str(base + k) for k in (2, 3, 4, 5)]
            graph[blank] = {
                "class_type": "SolidMask",
                "inputs": {"value": 0.0, "width": width, "height": height},
            }
            graph[region] = {
                "class_type": "SolidMask",
                "inputs": {"value": 1.0, "width": region_width, "height": height},
            }
            graph[feather] = {
                "class_type": "FeatherMask",
                "inputs": {
                    "mask": [region, 0],
                    "left": 32,
                    "right": 32,
                    "top": 0,
                    "bottom": 0,
                },
            }
            graph[merged] = {
                "class_type": "MaskComposite",
                "inputs": {
                    "destination": [blank, 0],
                    "source": [feather, 0],
                    "x": x,
                    "y": 0,
                    "operation": "add",
                },
            }
            inputs["attn_mask"] = [merged, 0]
        contact_mask = scene_meta.get('contact_masks', {}).get(reference.get('name'))
        if contact_mask:
            inputs['attn_mask'] = contact_mask
        graph[adapter_id] = {"class_type": "IPAdapterAdvanced", "inputs": inputs}
        model = [adapter_id, 0]
    if graph.get('271', {}).get('class_type') == 'BV Regional SDXL Attention':
        graph['291'] = {'class_type': 'LocalDrawRegionalReferenceAttention', 'inputs': {
            'regional_model': ['271', 0], 'reference_model': model}}
        model = ['291', 0]
    graph["5"]["inputs"]["model"] = model
    return graph
