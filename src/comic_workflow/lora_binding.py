"""Character LoRA routing and masked passes shared by every drawing entry."""

import copy
import math
from . import engine as e
from .character_binding import panel_characters

MAX_LORAS = 5  # Four character weights plus one shared anime style.


def binding(item):
    """An explicit empty ID means global style; absent ID retains legacy names."""
    if "character_id" in item:
        return {"character_id": item["character_id"], "character": item.get("character", ""),
                **({"scope":"global"} if item.get('scope')=='global' else {})}
    names = item.get("characters") or ([item["character"]] if item.get("character") else [])
    return {"names": names if item.get("scope", "characters") == "characters" else []}


def targets(item, project, panel):
    # Resource cards can own a style/tool/collection without turning that resource
    # into a person or restricting it to panels containing the resource card.
    if item.get('scope') == 'global':
        return None
    owner = binding(item)
    cast = panel_characters(project, panel)
    if "character_id" in owner:
        key = owner["character_id"]
        if not key:
            return None  # global style
        return [c for c in cast if key == c.get("saved_character", {}).get("id")
                or key == "project:" + c["id"]]
    if not owner["names"]:
        return None
    # Exact names only for old records, never fuzzy matching or prefix matching.
    return [c for c in cast if c["name"] in owner["names"]]


def route(loras, project, panel, labels=None, prefs=None):
    from .comfyui import lora_file_key

    result = []
    settings = {lora_file_key(i["file"]): i for i in (prefs or {}).get("items", [])}
    labels = {lora_file_key(name): label for name, label in (labels or {}).items()}
    for original in loras:
        item = copy.deepcopy(original)
        item["file"] = lora_file_key(item["file"])
        # Shared saved bindings take priority at all entry points, including old clients.
        label = labels.get(item["file"], {})
        owner = label if "character_id" in label else settings.get(item["file"], item)
        if "character_id" not in owner and not owner.get("characters") and not owner.get("character"):
            owner = item
        matched = targets(owner, project, panel)
        if matched == []:
            continue  # absent characters must never leak into this panel
        item.pop("target_character_ids", None)
        if "character_id" in owner:
            item.update(binding(owner))
        if matched is not None:
            e.need(len(matched) == 1, "一个角色 LoRA 只能关联一个角色，请重新指定：" + item["file"])
            item.update(binding(owner))
            item["target_character_ids"] = [matched[0]["id"]]
            item["character"] = matched[0]["name"]
        result.append(item)
    return result


def region_valid(region):
    return (isinstance(region, list) and len(region) == 4
            and all(type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 1 for v in region)
            and region[0] < region[2] and region[1] < region[3])


def overlaps(a, b):
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


def separate_regions(regions):
    """Whether optional positions can support independent regional rendering."""
    regions = list(regions)
    return (all(region_valid(r) for r in regions)
            and not any(overlaps(a, b) for i, a in enumerate(regions) for b in regions[i + 1:]))


def repair_owner(project, panel, owner):
    """A crossed limb belongs to the selected person, independent of position."""
    cast = panel_characters(project, panel)
    matched = [c for c in cast if c['id'] == owner] if isinstance(owner, str) else []
    e.need(len(matched) == 1, '修复角色必须是当前画面中的角色')
    return matched[0]


def plan(loras, project, panel, payload, owner=None):
    """Use regional passes when possible; otherwise let the full scene place the cast."""
    if owner is not None:
        selected = repair_owner(project, panel, owner)
        # Use the exact edit mask, retaining each LoRA's binding metadata.
        return copy.deepcopy([l for l in loras if not l.get('target_character_ids')
                              or l['target_character_ids'] == [selected['id']]]), []
    cast = panel_characters(project, panel)
    roles = [l for l in loras if l.get("target_character_ids")]
    if not roles or len(cast) <= 1:
        return loras, []
    subjects = {s["character_id"]: s for s in (payload.get("framing") or {}).get("subjects", [])}
    anchors = {a["character_id"]: a["tags"] for a in payload.get("character_anchors", [])}
    selected = {c["name"]: c for c in project.get("prompt_input", {}).get("characters", [])}
    for c in cast:
        if selected.get(c["name"], {}).get("region"):
            subjects[c["id"]] = {"region": selected[c["name"]]["region"]}
            anchors[c["id"]] = selected[c["name"]]["prompt"]
    regions = {c["id"]: subjects.get(c["id"], {}).get("region") for c in cast}
    if not separate_regions(regions.values()):
        return loras, []
    passes = []
    for c in cast:
        owned = [l for l in roles if l["target_character_ids"] == [c["id"]]]
        if not owned:
            continue
        region = regions[c["id"]]
        tags = anchors.get(c["id"], "")
        e.need(bool(tags.strip()), c["name"] + " 缺少独立的外观提示词")
        passes.append({"character_id": c["id"], "name": c["name"], "region": region,
                       "positive": tags, "loras": owned})
    return [l for l in loras if not l.get("target_character_ids")], passes


def regional_cast(project, panel, payload):
    """Include every explicitly placed person, including people without a LoRA."""
    subjects = {s['character_id']: s for s in (payload.get('framing') or {}).get('subjects', [])}
    anchors = {a['character_id']: a['tags'] for a in payload.get('character_anchors', [])}
    choices = {c['name']: c for c in project.get('prompt_input', {}).get('characters', [])}
    roles = []
    for character in panel_characters(project, panel):
        choice = choices.get(character['name'], {})
        roles.append({'name': character['name'],
                      'prompt': choice.get('prompt') or anchors.get(character['id'], ''),
                      'region': choice.get('region') or subjects.get(character['id'], {}).get('region')})
    from .regional_prompt import validate
    validate(roles)
    return roles


def apply_passes(graph, passes, globals_, cfg, seed, negative, add_triggers=True, characters=None):
    """Route whole-image regional backends or explicit legacy local repairs.

    The joint backends share one latent and decode. Legacy repair branches
    start from the checkpoint and restrict their output to the repair area.
    """
    if passes and cfg.get('regional_lora_hooks') and '212' not in graph:
        if cfg.get('regional_lora_backend') == 'native_masked':
            from .native_regional import apply
            apply(graph, passes, cfg, negative, add_triggers, characters, globals_)
            return
        if cfg.get('regional_lora_backend') == 'bv_sdxl':
            from .bv_regional import apply
            apply(graph, passes, cfg, negative, add_triggers, characters, globals_)
            return
        from .regional_lora import apply
        apply(graph, passes, cfg, negative, add_triggers)
        return
    previous = graph["7"]["inputs"]["images"]
    # Character features belong in their regional encodings; keep only the scene
    # as common context for a cropped single-person refinement.
    scene = graph["2"]["inputs"]["text"]
    for spec in passes:
        scene = scene.replace(spec["positive"], "")
    import re
    scene = re.sub(r"\b(?:[2-9]\s*(?:people|girls?|boys?|others?)|two people|multiple (?:people|characters)|couple|duo|solo)\b", "", scene, flags=re.I)
    from .regional_prompt import camera_context
    camera = camera_context(scene)
    for n, spec in enumerate(passes):
        base = 300 + n * 40
        def node(offset, kind, **inputs):
            key = str(base + offset)
            graph[key] = {"class_type": kind, "inputs": inputs}
            return [key, 0]
        model, clip = ["1", 0], ["1", 1]
        for i, lora in enumerate(globals_ + spec["loras"]):
            model = node(i, "LoraLoader", model=model, clip=clip, lora_name=lora["file"],
                         strength_model=lora["weight"], strength_clip=lora["clip_weight"])
            clip = [model[0], 1]
        text = "solo, one person, " + spec["positive"] + (", " + camera if camera else "")
        if add_triggers:
            text = ", ".join([l.get("trigger_words", "") for l in globals_ + spec["loras"]] + [text])
        pos = node(10, "CLIPTextEncode", text=text + ", " + scene, clip=clip)
        neg = node(11, "CLIPTextEncode", text=negative + ", duplicate person, extra person, two people, multiple views, collage, split screen, extra limbs, fused bodies, malformed hands", clip=clip)
        w, h = cfg["width"], cfg["height"]
        x1, y1, x2, y2 = spec["region"]
        x, y, right, bottom = round(w*x1), round(h*y1), round(w*x2), round(h*y2)
        blank = node(12, "SolidMask", value=0.0, width=w, height=h)
        rectangle = node(13, "SolidMask", value=1.0, width=max(1,right-x), height=max(1,bottom-y))
        mask = node(14, "MaskComposite", destination=blank, source=rectangle, x=x, y=y, operation="add")
        if cfg.get("person_masks"):
            person = node(29, "LocalDrawPersonMask", image=previous,
                          x=x, y=y, width=max(1,right-x), height=max(1,bottom-y))
            mask = node(30, "MaskComposite", destination=mask, source=person,
                        x=0, y=0, operation="multiply")
        # Intersect repair mask so a local repair cannot alter another part of the image.
        if "212" in graph:
            mask = node(15, "MaskComposite", destination=mask, source=["212",0], x=0,y=0, operation="multiply")
        # Work on a single-person crop at SDXL's native pixel budget. Full-frame
        # sampling from one character prompt creates a second body in that region.
        cw, ch = max(1,right-x), max(1,bottom-y)
        scale = min(2.0, math.sqrt(786432 / (cw*ch)))
        tw, th = max(64,round(cw*scale/64)*64), max(64,round(ch*scale/64)*64)
        crop = node(21, "ImageCrop", image=previous, width=cw, height=ch, x=x, y=y)
        scaled = node(22, "ImageScale", image=crop, upscale_method="lanczos", width=tw, height=th, crop="disabled")
        mask_crop = node(23, "CropMask", mask=mask, width=cw, height=ch, x=x, y=y)
        mask_image = node(24, "MaskToImage", mask=mask_crop)
        mask_scale = node(25, "ImageScale", image=mask_image, upscale_method="bilinear", width=tw, height=th, crop="disabled")
        noise_mask = node(26, "ImageToMask", image=mask_scale, channel="red")
        pixels = node(16, "VAEEncode", pixels=scaled, vae=["1",2])
        latent = node(17, "SetLatentNoiseMask", samples=pixels, mask=noise_mask)
        samples = node(18, "KSampler", model=model, positive=pos, negative=neg, latent_image=latent,
                       seed=(seed+n+1) % 2**53, steps=cfg["steps"], cfg=cfg["cfg"],
                       sampler_name=cfg["sampler_name"], scheduler=cfg["scheduler"], denoise=0.38)
        graph[samples[0]]["_meta"] = {"title": "角色区域绘制：" + spec["name"]}
        image = node(19, "VAEDecode", samples=samples, vae=["1",2])
        restored = node(27, "ImageScale", image=image, upscale_method="lanczos", width=cw, height=ch, crop="disabled")
        # Full-size source preserves the existing mask intersection contract.
        source = node(28, "ImageCompositeMasked", destination=previous, source=restored,
                      x=x,y=y,resize_source=False,mask=rectangle)
        previous = node(20, "ImageCompositeMasked", destination=previous, source=source,
                        x=0,y=0,resize_source=False,mask=mask)
    graph["7"]["inputs"]["images"] = previous
