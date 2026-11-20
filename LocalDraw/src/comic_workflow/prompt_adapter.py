"""Model-specific tag dialects around a shared, detailed scene description.

No inference or weight training happens here. Source descriptions and image history
are immutable; compilation only affects the next submitted image workflow.
"""

import copy
from pathlib import PurePosixPath

VERSION = 1
PROFILES = {
    "anima-3.8b-v1.1.safetensors": {
        "id": "anima38", "name": "Anima 3.8B v1.1",
        "quality": ["masterpiece", "best quality"],
        "negative": ["worst quality", "low quality"], "position": "prefix",
        "source": "https://huggingface.co/lylogummy/Anima-3.8B",
    },
    "animagine-xl-4.0-opt.safetensors": {
        "id": "animagine4", "name": "Animagine XL 4.0 Opt",
        "quality": ["masterpiece", "high score", "great score", "absurdres"],
        "negative": ["low quality", "worst quality", "low score", "bad score"],
        "position": "tail",
        "source": "https://huggingface.co/cagliostrolab/animagine-xl-4.0",
    },
    "animagine-xl-3.1.safetensors": {
        "id": "animagine31", "name": "Animagine XL 3.1",
        "quality": ["masterpiece", "best quality", "very aesthetic", "absurdres"],
        "negative": ["low quality", "worst quality", "lowres"],
        "position": "tail",
        "source": "https://huggingface.co/cagliostrolab/animagine-xl-3.1",
    },
    "noobai-xl-v1.1.safetensors": {
        "id": "noobai11", "name": "NoobAI XL 1.1",
        "quality": ["masterpiece", "best quality", "absurdres", "highres"],
        "negative": ["low quality", "worst quality", "lowres"],
        "position": "prefix",
        "source": "https://huggingface.co/Laxhar/noobai-XL-1.1",
    },
    "illustrious-xl-v2.0.safetensors": {
        "id": "illustrious2", "name": "Illustrious XL 2.0 STABLE",
        "quality": ["masterpiece", "best quality"],
        "negative": ["low quality", "worst quality"],
        "position": "tail",
        "source": "https://huggingface.co/OnomaAIResearch/Illustrious-XL-v2.0",
        "note": "项目的保守标签配置，尚未完成本机效果验证，非官方专用模板。",
    },
}


def profile(checkpoint=None):
    name = PurePosixPath(str(checkpoint or "").replace("\\", "/")).name.casefold()
    result = copy.deepcopy(PROFILES.get(name, {
        "id": "generic", "name": "未指定或未收录的绘图模型",
        "quality": [], "negative": [], "position": "tail",
        "note": "不猜模型训练标签，不自动替换质量词。",
    }))
    result["checkpoint"] = checkpoint or ""
    result["adapter_version"] = VERSION
    result["writing_policy"] = (
        "先按原文和可见角色卡写清英文画面说明与必要事实，再用英文视觉标签和简短关系短语表达。"
        "Qwen 不添加模型专属评分词；提交绘图时由适配器统一添加。"
        "详细是明确谁、在哪里、在做什么、与什么接触；不靠重复形容词或添加原文没有的物体变长。"
    )
    return result


def split_tags(text):
    """Do not split a comma inside CLIP weighting or character-name parentheses."""
    result, start, depth = [], 0, 0
    for i, char in enumerate(text):
        if char in "([":
            depth += 1
        elif char in ")]":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            result.append(text[start:i].strip())
            start = i + 1
    result.append(text[start:].strip())
    return [part for part in result if part]


def compile_prompt(positive, negative, checkpoint):
    dialect = profile(checkpoint)
    if dialect["id"] == "generic":
        return positive, negative, dialect
    quality = {tag.casefold() for p in PROFILES.values() for tag in p["quality"]}
    # Only replace recognized quality/score tags; never prune action, costume,
    # placement, era, artist, rating, LoRA trigger, or negative identity constraints.
    content = [tag for tag in split_tags(positive) if tag.casefold() not in quality]
    neg = [tag for tag in split_tags(negative) if tag.casefold() not in {"low score", "bad score"}]
    tags = dialect["quality"] + content if dialect["position"] == "prefix" else content + dialect["quality"]
    for tag in dialect["negative"]:
        if tag.casefold() not in {x.casefold() for x in neg}:
            neg.append(tag)
    return ", ".join(tags), ", ".join(neg), dialect


def detailed(prompt):
    return (prompt or {}).get("payload", {}).get("visual_plan", {}).get("version") == VERSION
