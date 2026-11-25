"""Character LoRA routing shared by every drawing entry."""

import copy
from . import engine as e
from .character_binding import panel_characters

MAX_LORAS = 5  # Four character weights plus one shared anime style.


def binding(item):
    """An explicit empty ID means global style; absent ID retains legacy names."""
    if "character_id" in item:
        return {"character_id": item["character_id"], "character": item.get("character", ""),
                **({"scope": "global"} if item.get("scope") == "global" else {})}
    names = item.get("characters") or ([item["character"]] if item.get("character") else [])
    return {"names": names if item.get("scope", "characters") == "characters" else []}


def targets(item, project, panel):
    # Resource cards can own a style/tool/collection without turning that resource
    # into a person or restricting it to panels containing the resource card.
    if item.get("scope") == "global":
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
