"""Reproducible saved rendering recipes, not newly trained LoRA weights."""

import copy
from . import engine as e

PRESETS = [
    {
        "id": "original",
        "name": "左 · 原生画风",
        "description": "保持原样：仅底模，不加 LoRA 或角色图像参考。",
        "sample": "2026091801-A.png",
        "reference": False,
        "lora": False,
    },
    {
        "id": "lora",
        "name": "中 · 方舟 LoRA",
        "description": "底模＋方舟样式 LoRA 0.7，不加图像参考。",
        "sample": "2026091801-B.png",
        "reference": False,
        "lora": True,
    },
    {
        "id": "reference",
        "name": "右 · 方舟 LoRA＋角色参考",
        "description": "底模＋方舟样式 LoRA 0.7＋角色图像参考 0.7；是组合预设，不是单独的 LoRA 文件。",
        "sample": "2026091801-C.png",
        "reference": True,
        "lora": True,
    },
]
SAMPLE_FOLDER = "20260918-145804-389075"
CHECKPOINT = "animagine-xl-4.0-opt.safetensors"
LORA = "明日方舟/明日方舟风格_AnimeOriginalArtStyleShadow2_SDXL-000100.safetensors"
PARAMETERS = {
    "steps": 28,
    "cfg": 5.0,
    "sampler_name": "euler_ancestral",
    "scheduler": "normal",
}


def get(value):
    key = (value or {}).get("art_preset", "custom")
    e.need(key in ["custom", *[x["id"] for x in PRESETS]], "未知画风预设")
    return next((copy.deepcopy(x) for x in PRESETS if x["id"] == key), None)


def signature(value):
    preset = get(value)
    return (
        e.digest(
            {
                "preset": preset,
                "parameters": PARAMETERS,
                "checkpoint": CHECKPOINT,
                "version": 1,
            }
        )
        if preset
        else None
    )


def apply(value, key):
    result = copy.deepcopy(value)
    result["art_preset"] = key
    preset = get(result)
    if not preset:
        return result
    result["checkpoint"] = CHECKPOINT
    result["mode"] = "manual" if preset["lora"] else "none"
    from .comfyui import lora_file_key
    target_lora = lora_file_key(LORA)
    if preset["lora"]:
        e.need(
            any(
                lora_file_key(x["file"]) == target_lora for x in result["items"]
            ),
            "方舟样式 LoRA 尚未安装或不兼容",
        )
    for item in result["items"]:
        item["selected"] = preset["lora"] and lora_file_key(item["file"]) == target_lora
        if item["selected"]:
            item["incompatible"] = False
            item.update(
                weight=0.7,
                scope="story",
                **({"character_id": ""} if "character_id" in item else {}),
                character="",
                characters=[],
                trigger_words="",
                confirmed=True,
            )
    return result
