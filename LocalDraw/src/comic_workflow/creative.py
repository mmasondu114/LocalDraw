"""Story-first writing and character extraction."""

import base64
import copy
import json
import time
from pathlib import Path
from . import engine as e
from .characters import CARD, CharacterLibrary, DETAILS
from .model import endpoint, request_json, count_messages
from .telemetry import chat_stream, stream_body
from .paths import DATA, settings
from .repository import LOCK, safe_id
from .schemas import obj, arr, S, enum, BRIEF, CANON, OUTLINE
from .output_language import DEFAULT_OUTPUT_POLICY

STORY_BRIEF = copy.deepcopy(BRIEF)
STORY_BRIEF["properties"].pop("character_refs")
STORY_BRIEF["required"].append("panel_mode")
STORY = obj(
    {
        "story": S,
        "brief": STORY_BRIEF,
        "canon": CANON,
        "outline": OUTLINE,
        "assumptions": arr(S),
        "changes": arr(S),
    }
)


def structured(
    prompt,
    context,
    schema,
    logdir,
    update=lambda text: None,
    image=None,
    max_tokens=5500,
    language_policy=DEFAULT_OUTPUT_POLICY,
):
    from .task_control import checkpoint
    checkpoint()
    prompt = prompt + "\n\n" + language_policy
    cfg = settings()
    text = e.encoded(context)
    e.need(
        len(text) <= 24000, "本次文本较长，请按章节拆分（最多 24000 字符含角色资料）"
    )
    content = text
    images = (
        image if isinstance(image, list) else [("本次图片", image)] if image else []
    )
    if image:
        content = [{"type": "text", "text": text}]
        for label, path in images:
            content += [
                {"type": "text", "text": label},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/png;base64,"
                        + base64.b64encode(Path(path).read_bytes()).decode()
                    },
                },
            ]
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": content},
    ]
    count = count_messages(messages) if cfg.get("model_backend") == "strata" else len(
        request_json(
            endpoint() + "/tokenize", {"content": prompt + text, "add_special": True}
        )["tokens"]
    )
    e.need(
        count + max_tokens + 1024 + 1024 * len(images) <= cfg["context_size"],
        "上下文不足，请缩短故事或减少本次角色数量",
    )
    body = {
        "model": cfg.get("model_name", "comic-local"),
        "messages": messages,
        "temperature": 0.35,
        "max_tokens": max_tokens,
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "creative", "strict": True, "schema": schema},
        },
    }
    if cfg.get("model_backend") == "strata":
        body["reasoning_effort"] = "none"
    body = stream_body(body)
    e.save(Path(logdir) / "request.json", body)
    update("AI 正在处理，完成后可查看并修改")
    started = time.monotonic()
    r = chat_stream(
        endpoint() + "/v1/chat/completions",
        body,
        cfg["model_timeout_seconds"],
        update,
        logdir,
    )
    e.save(Path(logdir) / "response.json", r)
    choice = r["choices"][0]
    e.need(
        choice["finish_reason"] == "stop",
        f"模型未完成草稿（结束原因：{choice['finish_reason']}，输出上限 {max_tokens} token）；"
        "原文保留，未应用任何内容",
    )
    payload = json.loads(choice["message"]["content"])
    e.jsonschema.validate(payload, schema)
    metrics = {
        "seconds": round(time.monotonic() - started, 3),
        "usage": r.get("usage"),
        "vision": bool(image),
        "image_count": len(images),
    }
    e.save(Path(logdir) / "metrics.json", metrics)
    return payload, metrics


def character_draft(library, a, logdir, update):
    mode = a["mode"]
    e.need(mode in ["image", "text"], "请选择建档方式")
    personality = str(a.get("personality", "")).strip()
    text = str(a.get("text", "")).strip()
    e.need(
        personality if mode == "image" else len(text) >= 10, "请提供性格或角色文字设定"
    )
    image = library.image_path(a["image_id"]) if mode == "image" else None
    prompt = (
        "你是漫画角色资料编辑。生成详细且可绘制的英文角色卡，每个字段用具体英文填写，不知道写 Unknown。"
        "图片模式：只把图片可见的发型、脸、眼睛、服装分层、配饰、颜色和姿态记入 observed；被遮挡部位不补造。"
        "性格、身份、年龄、经历仅依据用户文字，不能从长相推断。observed 是图像观察而非已验证原作事实。"
        "角色卡区分长期特征与当前姿态：body 只写体型比例，不能把参考图的动作、表情、水滴或背景当成永久设定；这些临时观察只放 observed。"
        "文字模式：展开用户给出的外貌、服装、性格、说话习惯、愿望、弱点、背景和关系，建议补充写入 suggested，"
        "不得把你新增的设定说成原作事实。保留用户确定的所有特征，不替换角色。"
        "引用已有作品时不凭记忆补原作事实，unknowns 列出不清楚之处。输出只为待确认草稿。"
    )
    card_schema = copy.deepcopy(CARD)
    if mode == "text":
        prompt += "纯文字建档须形成可直接画角色的详细英文卡片，不要只把原文拆成几个词。每项至少 12 个字符；衣着、性格、口吻至少 24 个字符。缺失内容可以明确写 ‘Unknown; the user has not provided this detail.’，如给创作补充建议必须同时列入 suggested，不能当原作事实。"
        for key in DETAILS:
            card_schema["properties"][key] = {
                "type": "string",
                "minLength": 24 if key in ["clothing", "personality", "voice"] else 12,
            }
    card, metrics = structured(
        prompt,
        {
            "mode": mode,
            "name": a.get("name"),
            "personality": personality,
            "user_material": text,
        },
        card_schema,
        logdir,
        update,
        image,
        3500,
    )
    return {
        "profile": card,
        "source": {
            "mode": mode,
            "text": text,
            "personality": personality,
            "model": "Qwen3.6-27B + F16 visual projector" if image else "Qwen3.6-27B",
        },
        "image_id": a.get("image_id"),
        "metrics": metrics,
    }


def story_source_blocks(original):
    """Keep source text verbatim, including blank lines and repeated dialogue."""
    blocks = []
    for line in original.splitlines(keepends=True):
        if not line.strip() and blocks:
            blocks[-1] += line
        else:
            blocks.append(line)
    return blocks


def assemble_story(blocks, sections):
    """The model chooses heading positions; it cannot delete or rewrite prose."""
    starts = [section["start_block"] for section in sections]
    e.need(
        bool(starts)
        and all(type(n) is int and 1 <= n <= len(blocks) for n in starts)
        and starts[0] == 1
        and starts == sorted(set(starts)),
        "场景分组编号无效，未应用草稿，请重新整理",
    )
    headings = {section["start_block"]: section["heading"].strip() for section in sections}
    e.need(all(headings.values()), "场景标题不能为空，未应用草稿")
    return "".join(
        (f"\n\n[{headings[n]}]\n" if n in headings else "") + block
        for n, block in enumerate(blocks, 1)
    ).lstrip("\n")


def story_generation_schema(block_count=None):
    """Bound generated character metadata without restricting the source story.

    Round-trip instead of deepcopy: the shared schemas reuse the same string
    objects, so editing one field must not constrain unrelated story fields.
    """
    schema = json.loads(json.dumps(STORY))
    character = schema["properties"]["canon"]["properties"]["characters"]["items"]["properties"]
    for field in ("source_notes", "verified_facts", "adaptation_choices", "unknowns", "sample_lines", "taboos"):
        character[field].update(maxItems=3)
        character[field]["items"] = {"type": "string", "maxLength": 240}
    for field in ("appearance", "voice", "motivation"):
        character[field]["maxLength"] = 500
    if block_count is not None:
        # Constrained decoding can only choose real, short source references.
        # It cannot continue the unbounded "10, 14, 18, ..." failure string.
        character["source_notes"]["items"] = enum(
            *(f"Source block {n}" for n in range(1, block_count + 1))
        )
    return schema


def story_draft(library, a, logdir, update):
    original = str(a.get("text", "")).strip()
    e.need(5 <= len(original) <= 12000, "请粘贴 5–12000 字符故事，较长作品按章节处理")
    mode = a.get("mode", "structure")
    e.need(mode in ["structure", "expand", "continue"], "未知写作方式")
    if "character_names" in a:
        names = a["character_names"]
        e.need(isinstance(names, list) and all(isinstance(n, str) for n in names), "请选择角色库中的角色")
        cards = {card["name"]: card for card in library.listing()}
        e.need(all(n in cards for n in names), "选中的角色已变化，请刷新角色列表")
        refs = [cards[name] for name in dict.fromkeys(names)]
    else:
        refs = library.resolve(original)
    prompt = (
        "你是英文漫画编剧。用户只粘贴故事，不填写繁琐表格。根据 mode 整理为完整英文剧本："
        "structure 保留原事件，只整理表达；expand 在原剧情内补动作、对白和情绪；continue 保留原故事并续写。"
        "story 是完整可读剧本，场景描述使用英文，新写对白、旁白和拟声默认使用简体中文，用户明确指定其他语言时遵从用户，不能只给提纲。必须遵守用户文本，不改指定角色、结局和关键事件。"
        "逐段保留动作顺序、对白与说话人、心理和情绪变化、人物动机、因果关系、时间地点、道具状态、伏笔和呼应；"
        "不得用概述代替具体过程，不得因为建议页数少而删减情节。"
        "使用所选角色卡中的姓名和资料；角色姓名不能改，资料为用户确认版本。"
        "brief 自动提取标题、题材、方向、必须出现事件和角色资料；panel_mode=flexible，每页允许大小不同的格子。"
        "用户没指定页数时按完整情节容量合理建议 1–80 页，不把长故事硬压成几页；panels_per_page=5 仅为节奏参考。reading_direction 默认 LTR，page_mode 默认 single_page，first_story_page_side=right。"
        "canon 只自动抽取剧本已经需要的人物(C001)、场景(S001)、道具(O001)和 initial_state 状态字典；"
        "world_rules 留空，不单独构建世界观，不生成多个剧情方案。人物口吻、外观必须与引用卡一致。"
        "outline.selected_option=direct，outline 是此剧本的内部情节索引，不要求用户另确认。"
        "assumptions 用英文列出你替用户作的选择；changes 用英文列出扩写或续写的新增内容。未知原作事实不得编造。"
    )
    prompt += (
        "角色资料字段只记简短、明确的原文事实，不推测原作外貌；无依据留空或写 Unknown。"
        "每个角色的 source_notes、verified_facts、adaptation_choices、unknowns、sample_lines、taboos "
        "各最多三项，每项最多 240 字符；appearance、voice、motivation 各最多 500 字符。"
        "避免重复同一信息；这些是辅助角色索引，完整剧情保留在正文和情节索引中。"
    )
    schema = story_generation_schema()
    context = {
        "mode": mode,
        "user_story": original,
        "characters": refs,
        "preferences": a.get("preferences", ""),
    }
    if mode == "structure":
        blocks = story_source_blocks(original)
        # Do not spend the output budget reproducing (and compressing) the story.
        # Source blocks are assembled locally; only headings and metadata are generated.
        schema = story_generation_schema(len(blocks))
        schema["properties"].pop("story")
        schema["required"].remove("story")
        schema["properties"]["sections"] = arr(obj({
            "start_block": {"type": "integer", "minimum": 1, "maximum": len(blocks)},
            "heading": {"type": "string", "minLength": 1, "maxLength": 80},
        }), 1)
        schema["properties"]["sections"]["maxItems"] = len(blocks)
        schema["required"].append("sections")
        context.pop("user_story")
        context["source_blocks"] = blocks
        prompt += (
            "本次为 structure 保真整理：不输出 story，不重写正文。source_blocks 是完整原文段落数组，编号从 1 开始，"
            "程序会逐字保留所有段落，只在 sections 指定的位置添加场景标题。"
            "sections 每项只给 start_block 和 heading；第一项从 1 开始，后续编号严格递增，"
            "每组涵盖起始编号到下一组之前的所有段落，最后一组涵盖余下全文。"
            "heading 必须是英文；仅描述原文已有的场景，不编造时间地点，不把每一句都拆成场景。"
            "brief、canon、outline 必须从全部原文提取，不能只读开头或用概要替代正文。"
            "outline.beats 按顺序详细记录每个事件的起因、行动、对白信息、结果和伏笔；"
            "must_include 与 must_include_mapping 覆盖原文明确要求和后续分镜不能丢失的细节。"
            "changes 留空；场景分组和页数建议写在 assumptions。"
            f"source_blocks 一共 {len(blocks)} 段。每个角色的 source_notes 只选最多三个最有代表性的真实来源，"
            "每项严格使用 Source block N 格式，N 是实际段落编号；不要连续列举编号，不要重复引用。"
        )
    canon_result = None
    if mode == "structure":
        # Large casts can fill a whole response even without repetition. Give
        # canon and story indexing separate budgets, both with the full source.
        update("正在提取角色与场景资料（第 1/2 步）")
        canon_schema = obj({"canon": schema["properties"].pop("canon")})
        schema["required"].remove("canon")
        canon_result, canon_metrics = structured(
            prompt + "本次只输出 canon 角色与场景资料，其他字段将在下一步处理。",
            context,
            canon_schema,
            Path(logdir) / "canon",
            update,
            max_tokens=9000,
        )
        update("正在整理情节索引与场景分组（第 2/2 步）")
        prompt += "本次只输出 brief、outline、assumptions、changes、sections；角色与场景资料已单独提取，不要重复输出 canon。"
    payload, metrics = structured(
        prompt,
        context,
        schema,
        logdir,
        update,
        max_tokens=9000,
    )
    if mode == "structure":
        payload.update(canon_result)
        metrics = {
            **metrics,
            "seconds": round(canon_metrics["seconds"] + metrics["seconds"], 3),
            "calls": {"canon": canon_metrics, "story": metrics},
            "usage": {
                key: sum((m.get("usage") or {}).get(key, 0) for m in (canon_metrics, metrics))
                for key in ("prompt_tokens", "completion_tokens", "total_tokens")
            },
        }
        e.save(Path(logdir) / "metrics.json", metrics)
        sections = payload.pop("sections")
        payload["story"] = assemble_story(blocks, sections)
        payload["changes"] = ["Preserved the full source text and its order; added scene headings and extracted character, scene, and plot details."]
        e.save(Path(logdir) / "source-sections.json", {"sections": sections, "block_count": len(blocks)})
    payload["brief"]["panel_mode"] = "flexible"
    payload["brief"]["character_refs"] = refs
    payload["outline"]["selected_option"] = "direct"
    for character in payload["canon"]["characters"]:
        if character["name"].startswith("/") and character["name"][1:] in {
            c["name"] for c in refs
        }:
            character["name"] = character["name"][1:]
    return {"payload": payload, "original": original, "mode": mode, "metrics": metrics,
            "explicit_characters": "character_names" in a}


def create_story(repo, library, result):
    payload = copy.deepcopy(result["payload"])
    for character in payload["canon"]["characters"]:
        if character["name"].startswith("/") and character["name"][1:] in {
            c["name"] for c in payload["brief"].get("character_refs", [])
        }:
            character["name"] = character["name"][1:]
    no_refs = copy.deepcopy(payload)
    no_refs["brief"].pop("character_refs", None)
    e.jsonschema.validate(no_refs, STORY)
    e.need(payload["story"].strip(), "剧本不能为空")
    p = {
        "workflow": "direct",
        "script": payload["story"],
        "brief": payload["brief"] if result.get("explicit_characters") else library.freeze_brief(payload["brief"]),
        "canon": payload["canon"],
        "options": None,
        "outline": payload["outline"],
        "plan": None,
        "pages": {},
        "version": 1,
        "provenance": "用户确认剧本；角色和场景由 AI 从剧本整理",
        "script_source": {
            "original": result.get("original", ""),
            "mode": result.get("mode"),
            "assumptions": payload["assumptions"],
            "changes": payload["changes"],
        },
    }
    e.validate_project(p)
    # Known library characters must survive the automatic extraction with their real names.
    names = {c["name"] for c in p["canon"]["characters"]}
    for card in p["brief"].get("character_refs", []):
        e.need(card["name"] in names, f"剧本缺少引用角色 {card['name']}，请修正草稿")
    import uuid

    pid = "story-" + uuid.uuid4().hex[:12]
    with LOCK:
        e.save(repo.folder(pid) / "project.json", p)
        e.save(repo.folder(pid) / "script-drafts" / "accepted.json", result)
    return pid
