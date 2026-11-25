"""Per-work drawing choices, separate from confirmed story revisions."""

from . import engine as e
from .repository import LOCK
from .lora_binding import MAX_LORAS


def validate(value):
    e.need(isinstance(value, dict), "无效绘图设置")
    from .image_size import settings
    settings(value)
    e.need(type(value.get("hand_detail", True)) is bool, "手部增强选项无效")
    if "consistency_lora" in value:
        e.need(type(value["consistency_lora"]) is bool, "一致性 LoRA 选项无效")
    e.need(isinstance(value.get("reference_character_id", ""), str), "参考图角色编号无效")
    e.need(value.get("mode") in ["none", "manual", "characters"], "请选择 LoRA 模式")
    e.need(
        isinstance(value.get("checkpoint"), str) and value["checkpoint"],
        "请选择绘图模型",
    )
    items = value.get("items", [])
    e.need(isinstance(items, list) and len(items) <= 10000, "LoRA 列表无效")
    e.need(len({x.get("file") for x in items}) == len(items), "LoRA 文件重复")
    for item in items:
        e.need(isinstance(item.get("character_id", ""), str), "角色绑定编号无效")
        e.need(isinstance(item.get("file"), str), "LoRA 文件名无效")
        e.need(
            all(
                isinstance(item.get(k, ""), str)
                for k in ["name", "character", "trigger_words"]
            ),
            "LoRA 标注须为文字",
        )
        e.need(
            isinstance(item.get("weight"), (int, float)) and 0 <= item["weight"] <= 2,
            "LoRA 权重须为 0–2",
        )
        e.need(
            type(item.get("selected")) is bool and type(item.get("confirmed")) is bool,
            "LoRA 选择无效",
        )
        e.need(
            item.get("scope", "story") in ["story", "characters"], "LoRA 应用范围无效"
        )
        names = item.get("characters", [])
        e.need(
            isinstance(names, list)
            and all(isinstance(name, str) and name for name in names),
            "请选择要应用的角色",
        )
    if value["mode"] == "manual":
        e.need(
            sum(x["selected"] and x.get("scope", "story") == "story" for x in items)
            <= MAX_LORAS,
            "整部作品最多同时应用五个 LoRA；单格叠加上限也是五个",
        )
    return value


def load(repo, pid):
    path = repo.folder(pid) / "drawing-settings.json"
    return e.read(path) if path.exists() else None


def loras_for_panel(value, project, panel, *, labels=None):
    if value["mode"] == "none":
        return []
    from .lora_binding import targets
    from .comfyui import lora_file_key, lora_labels
    labels = lora_labels() if labels is None else labels
    result = []
    items = [{**item, "file": lora_file_key(item["file"])} for item in value["items"]]
    if value["mode"] == "characters":
        known = {i["file"] for i in items}
        items += [{**label, "file":file, "selected":False, "confirmed":label.get("confirmed", False)}
                  for file, label in labels.items() if file not in known and label.get("character_id")]
    for item in items:
        from .lora_folders import matches
        if not matches(item["file"], value["checkpoint"]):
            continue
        if item.get("incompatible"):
            continue
        label = labels.get(item["file"], {})
        owner = label if "character_id" in label else item
        matched = targets(owner, project, panel)
        scope = "story" if matched is None else "characters"
        matches = bool(matched)
        chosen = (
            (matches or (item["selected"] and scope == "story"))
            if value["mode"] == "characters"
            else item["selected"] and (scope == "story" or matches)
        )
        if chosen:
            result.append(
                {
                    "file": item["file"],
                    **({"character_id": owner["character_id"], "character": owner.get("character", "")}
                       if "character_id" in owner else {"scope": scope, "characters": owner.get("characters", [owner.get("character", "")])}),
                    "label": item["name"],
                    "trigger_words": item.get("trigger_words", ""),
                    "weight": item["weight"],
                    "clip_weight": item["weight"],
                    "compatible_checkpoint": value["checkpoint"],
                    "confirmed_compatibility": item["confirmed"],
                }
            )
    e.need(
        len(result) <= MAX_LORAS,
        f"{panel['id']} 的全剧本与角色 LoRA 叠加超过五个，请在第一步减少选择",
    )
    from .lora_binding import route
    return route(result, project, panel, labels, value)


def save(repo, pid, value):
    validate(value)
    with LOCK:
        repo.get(pid)
        old = load(repo, pid)
        if old == value:
            return value
        if old is not None:
            e.save(
                repo.folder(pid) / "drawing-settings-history" / (e.stamp() + ".json"),
                old,
            )
        e.save(repo.folder(pid) / "drawing-settings.json", value)
    return value
