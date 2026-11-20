"""Explicit regions for direct drawing; never infer or move a character's area."""

from . import engine as e
from .lora_binding import region_valid, overlaps, separate_regions
import re


def scene_prompt(text, items):
    """Explicit regional cast owns the subject count; each region owns one person."""
    girls = sum(bool(re.search(r"\b1girl\b", c["prompt"], re.I)) for c in items)
    boys = sum(bool(re.search(r"\b1boy\b", c["prompt"], re.I)) for c in items)
    text = re.sub(r"\b(?:solo|[1-9]\s*(?:people|girls?|boys?|others?)|two people|three people|four people)\b", "", text, flags=re.I)
    count = []
    if girls + boys == len(items):
        if girls: count.append(str(girls) + "girl" + ("s" if girls > 1 else ""))
        if boys: count.append(str(boys) + "boy" + ("s" if boys > 1 else ""))
    else: count.append(str(len(items)) + "people")
    return ", ".join(count + [text.strip(" ,")])


def camera_context(text):
    """Copy explicit framing into local encodings so all people share a scale."""
    tags = re.findall(r"\b(?:medium shot|upper body|cowboy shot|full body|close[- ]up|eye level|from above|from below|standing|sitting|seated)\b", text, re.I)
    if any(t.casefold()=="medium shot" for t in tags):
        tags.append("(upper body:1.2)")
    return ", ".join(dict.fromkeys(tags))


def validate(items):
    e.need(len(items) >= 2 and all(region_valid(c.get("region")) for c in items),
           "分区绘制需要至少两个角色，并为每个角色指定画面位置")
    for i, item in enumerate(items):
        e.need(item["prompt"].strip(), "分区绘制需要填写每个角色的外观提示词")
        e.need(not any(overlaps(item["region"], other["region"]) for other in items[i + 1:]),
               "角色画面位置不能重叠，请调整后再分区绘制")


def apply(graph, spec, cfg, character_passes=None):
    if not spec.get("regional_characters"):
        return []
    items = spec["characters"]
    if len(items) < 2 or not separate_regions(c.get("region") for c in items):
        return []
    validate(items)
    layers = {(p['name'], tuple(p['region'])) for p in (character_passes or [])}
    complete = all((c['name'], tuple(c['region'])) in layers for c in items)
    # Keep the scene global; character appearances are encoded separately.
    graph["2"]["inputs"]["text"] = scene_prompt(spec["text"], items)
    graph["3"]["inputs"]["text"] += ", extra person, duplicate person, crowd, multiple views, collage, split screen, fused bodies, extra limbs, malformed hands, distorted background"
    if cfg.get('regional_lora_hooks') and (complete or (
            cfg.get('regional_lora_backend') in ('bv_sdxl', 'native_masked') and character_passes)):
        # The hooks path supplies each person's conditioning during the same
        # denoising process; avoid adding a second, unhooked regional cast.
        return [{'name':c['name'],'prompt':c['prompt'],'region':c['region']} for c in items]
    clip = graph["2"]["inputs"]["clip"]
    camera = camera_context(spec["text"])
    combined = ["2",0]
    regions = []
    for i, item in enumerate(items):
        base = 600 + i * 10
        encode, blank, area, mask, conditioned, merge = [str(base + j) for j in range(6)]
        x1, y1, x2, y2 = item["region"]
        x, y = round(cfg["width"] * x1), round(cfg["height"] * y1)
        width = max(1, round(cfg["width"] * x2) - x)
        height = max(1, round(cfg["height"] * y2) - y)
        text = item["prompt"] + (", " + camera if camera else "")
        graph[encode] = {"class_type": "CLIPTextEncode", "inputs": {"text": text, "clip": clip}}
        graph[blank] = {"class_type": "SolidMask", "inputs": {
            "value": 0.0, "width": cfg["width"], "height": cfg["height"]}}
        graph[area] = {"class_type": "SolidMask", "inputs": {"value": 1.0, "width": width, "height": height}}
        graph[mask] = {"class_type": "MaskComposite", "inputs": {
            "destination": [blank, 0], "source": [area, 0], "x": x, "y": y, "operation": "add"}}
        graph[conditioned] = {"class_type": "ConditioningSetMask", "inputs": {
            "conditioning": [encode, 0], "mask": [mask, 0], "strength": 1.0, "set_cond_area": "mask bounds"}}
        graph[merge] = {"class_type": "ConditioningCombine", "inputs": {
            "conditioning_1": combined, "conditioning_2": [conditioned, 0]}}
        combined = [merge, 0]
        regions.append({"name": item["name"], "prompt": item["prompt"], "region": item["region"]})
    graph["5"]["inputs"]["positive"] = combined
    return regions
