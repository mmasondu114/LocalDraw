"""Local llama.cpp lifecycle and bounded, logged generation. No model downloads."""

import copy
from contextlib import nullcontext
import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import engine as e
from .downloader import filehash
from .paths import ROOT, DATA, settings, local_path, raw_settings
from .analysis_models import worker_headers, worker_key, release_key
from .processes import attach_exit_job, close_job, terminate_tree
from .telemetry import chat_stream, stream_body, emit
from .output_language import DEFAULT_OUTPUT_POLICY

GENERATION_LOCK = threading.Lock()


def repair_page_layout(payload, expected_ids):
    """Restore omitted trailing layout references without changing storyboard text."""
    fixed = copy.deepcopy(payload)
    e.schema("page", fixed)
    rows = fixed.get("layout_rows", [])
    actual = [panel["id"] for panel in fixed["panels"]]
    sequence = [slot["panel_id"] for row in rows for slot in row["panels"]]
    if actual != expected_ids or not rows or sequence != actual[:len(sequence)]:
        return fixed, []
    changes = []
    row_schema = e.SCHEMAS["page"]["properties"]["layout_rows"]
    max_rows = row_schema["maxItems"]
    max_columns = row_schema["items"]["properties"]["panels"]["maxItems"]
    for panel_id in actual[len(sequence):]:
        if len(rows[-1]["panels"]) >= max_columns:
            if len(rows) >= max_rows:
                return copy.deepcopy(payload), []
            rows.append({"weight": 1.0, "panels": []})
        rows[-1]["panels"].append({"panel_id": panel_id, "weight": 1.0})
        changes.append({"panel_id": panel_id, "row": len(rows), "action": "restore_layout_reference"})
    return fixed, changes


def request_json(endpoint, body=None, timeout=5):
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", **worker_headers(endpoint)},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as response:
            raw = response.read()
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        raise ValueError(
            f"模型服务返回 {exc.code}：{exc.read().decode(errors='replace')[:1500]}"
        ) from exc


def endpoint(cfg=None):
    return f"http://127.0.0.1:{(cfg or settings())['model_port']}"


def build_context(p, stage, target, instruction, drawing_checkpoint=None):
    e.need(stage in e.SCHEMAS and stage != "brief", "该阶段不支持 AI 生成")
    previous = {
        "options": "canon",
        "outline": "options",
        "plan": "outline",
        "page": "plan",
        "panel": "plan",
        "audit": "plan",
        "drawing_prompt": "plan",
    }
    if stage in previous:
        e.need(p.get(previous[stage]), "请先确认前一阶段")
    brief = copy.deepcopy(p["brief"])
    # A frozen card's raw source repeats the saved profile; keep the actual profile and version.
    for card in brief.get("character_refs", []):
        card.pop("source", None)
    ctx = {"brief": brief, "instruction": instruction, "target": target}
    from .characters import CharacterLibrary

    refs = p["brief"].get("character_refs", [])
    ctx["referenced_characters"] = CharacterLibrary().resolve(instruction, refs)
    if p.get("script") and stage == "plan":
        ctx["confirmed_script"] = p["script"]
    if stage == "canon":
        ctx["existing_canon"] = p.get("canon")
        return ctx
    ctx["canon"] = copy.deepcopy(p["canon"])
    if stage == "outline":
        ctx["options"] = p["options"]
    if stage in ["plan", "page", "panel", "audit"]:
        ctx["outline"] = p["outline"]
    if stage == "plan":
        ctx["required_page_structure"] = [
            {
                "id": f"P{n:03}",
                "number": n,
                "panel_count": p["brief"]["panels_per_page"],
                "turn_after": e.turn_after(p["brief"], n),
            }
            for n in range(1, p["brief"]["page_count"] + 1)
        ]
        ctx["turn_after_definition"] = (
            "可翻页的物理位置，不表示是否有悬念；必须逐项照抄 required_page_structure，即使最后一页也不改值。"
        )
        if p["brief"].get("panel_mode") == "flexible":
            for item in ctx["required_page_structure"]:
                item.pop("panel_count")
            ctx["panel_count_policy"] = (
                "每页自主安排 1–9 格，按节奏变化，不强制四格；使用大小不同的漫画格"
            )
    if stage in ["page", "panel", "audit", "drawing_prompt"]:
        pid = target.split("-")[0]
        e.need(pid in [x["id"] for x in p["plan"]["pages"]], "请指定已规划页号")
        n = int(pid[1:])
        if p.get("script_pages", {}).get(pid):
            ctx["source_page"] = {
                k: p["script_pages"][pid][k] for k in ["title", "panels"]
            }
            ctx["automation_policy"] = p.get("automation_policy", "")
        e.need(n == 1 or f"P{n - 1:03}" in p["pages"], "请先确认前一页")
        nearby = p["plan"]["pages"][max(0, n - 2) : n + 1]
        ctx["plan"] = nearby
        prev = p["pages"].get(f"P{n - 1:03}")
        ctx["start_state"] = prev["state_out"] if prev else p["canon"]["initial_state"]
        ctx["previous_last_panel"] = prev["panels"][-1] if prev else None
        ctx["current_page"] = p["pages"].get(pid)
        # Only neighboring cast/locations are brought into page work. State keys remain complete.
        char_ids = {c for page in nearby for c in page["character_ids"]}
        scene_ids = {s for page in nearby for s in page["scene_ids"]}
        for page in p["pages"].values():
            if abs(page["number"] - n) <= 1:
                char_ids.update(
                    c["character_id"]
                    for panel in page["panels"]
                    for c in panel["characters"]
                )
                char_ids.update(
                    t["speaker_id"] for t in page["lettering"] if t["speaker_id"]
                )
                scene_ids.update(panel["scene_id"] for panel in page["panels"])
        ctx["canon"]["characters"] = [
            c for c in p["canon"]["characters"] if c["id"] in char_ids
        ]
        ctx["canon"]["scenes"] = [
            s for s in p["canon"]["scenes"] if s["id"] in scene_ids
        ]
        ctx["canon"].pop("initial_state")
        if stage == "panel":
            e.need(ctx["current_page"], "单格重做需要已确认页面")
        if stage == "drawing_prompt":
            e.need(ctx["current_page"], "先确认页面")
            ctx["panel"] = next(
                (x for x in ctx["current_page"]["panels"] if x["id"] == target), None
            )
            e.need(ctx["panel"], "未找到目标格")
            from .character_binding import panel_characters

            ctx["state_before"] = e.panel_start_state(ctx["current_page"], ctx["panel"])
            ctx["bound_characters"] = panel_characters(p, ctx["panel"])
            ctx["brief"] = copy.deepcopy(ctx["brief"])
            ctx["brief"].pop(
                "character_refs", None
            )  # Full visible cards are bound above.
            ctx.pop("current_page")
            from .prompt_adapter import profile

            ctx["image_model"] = profile(drawing_checkpoint)
        if stage == "audit":
            e.need(ctx["current_page"], "审核需要已确认页面")
            ctx["pages"] = {pid: p["pages"][pid]}
            ctx.pop("current_page")  # Already present in pages; do not send it twice.
            ctx["structural_warnings"] = e.validate_project(p)
            ctx["scope_note"] = (
                "本次只审核当前页，结合上页末格及进入状态；未提供的页面不能断言缺少必需情节。"
            )
    return ctx


def count_messages(messages):
    if settings().get("model_backend") == "strata":
        body = {"model": settings()["model_name"],
                "messages": [m for m in messages if m["role"] != "system"],
                "system": "\n\n".join(m["content"] for m in messages if m["role"] == "system"),
                "thinking": {"type": "disabled"}}
        return request_json(endpoint() + "/v1/messages/count_tokens", body)["input_tokens"]
    formatted = request_json(
        endpoint() + "/apply-template",
        {"messages": messages, "chat_template_kwargs": {"enable_thinking": False}},
    )
    return len(
        request_json(
            endpoint() + "/tokenize",
            {"content": formatted["prompt"], "add_special": True},
        )["tokens"]
    )


def repair_lettering_numbering(payload):
    """Renumber unambiguous per-panel order without changing any words or panels."""
    fixed = copy.deepcopy(payload)
    changes = []
    for pid in dict.fromkeys(t["panel_id"] for t in fixed["lettering"]):
        texts = [t for t in fixed["lettering"] if t["panel_id"] == pid]
        orders = [t["order"] for t in texts]
        e.need(
            len(set(orders)) == len(orders), pid + " 气泡顺序重复，不能自动猜测阅读顺序"
        )
        for order, text in enumerate(sorted(texts, key=lambda t: t["order"]), 1):
            new_id = f"{pid}-T{order:02}"
            if text["order"] != order or text["id"] != new_id:
                changes.append(
                    {
                        "before": {"id": text["id"], "order": text["order"]},
                        "after": {"id": new_id, "order": order},
                    }
                )
                text.update(id=new_id, order=order)
    return fixed, changes


def budget_messages(messages, original, cfg, update):
    """Keep output capacity; discard only failed-draft history if repair won't fit."""
    budget = cfg["context_size"] - cfg["max_output_tokens"] - 1024
    token_count = count_messages(messages)
    if token_count > budget and len(messages) > len(original):
        update(
            {
                "message": "正在自动精简修正上下文，保留原始要求和错误说明",
                "runtime": None,
            }
        )
        messages = copy.deepcopy(original) + [messages[-1]]
        token_count = count_messages(messages)
    e.need(
        token_count <= budget,
        f"本阶段资料超过模型容量（输入 {token_count} token，可用 {budget}）。"
        "已确认内容保留；本次未提交生成，也未截断资料。",
    )
    return messages, token_count


def generate(p, stage, target, instruction, logdir, update=lambda text: None, *, drawing_checkpoint=None):
    from .task_control import checkpoint
    checkpoint()
    cfg = settings()
    if stage == "audit":
        cfg = {**cfg, "max_output_tokens": min(cfg["max_output_tokens"], 3500)}
    # Schema helpers reuse dictionary objects. deepcopy preserves those aliases;
    # JSON roundtrip deliberately detaches each field before adding constraints.
    generation_schema = json.loads(json.dumps(e.SCHEMAS[stage]))
    if stage == "drawing_prompt":
        generation_schema["required"].append("visual_plan")
        fields = generation_schema["properties"]
        generation_schema["properties"] = {
            "panel_id": fields["panel_id"], "visual_plan": fields["visual_plan"],
            **{k: v for k, v in fields.items() if k not in ("panel_id", "visual_plan")},
        }
        generation_schema["properties"]["composition_variants"].update(
            minItems=6, maxItems=6
        )
    if stage == "page":
        generation_schema["required"].append("layout_rows")
    if stage in ["page", "panel"]:
        panel_schema = (
            generation_schema["properties"]["panels"]["items"]
            if stage == "page" else generation_schema["properties"]["panel"]
        )
        # English needs more characters than the former Chinese description limit.
        panel_schema["properties"]["drawing_description"]["maxLength"] = 1400
    ctx = build_context(p, stage, target, instruction, drawing_checkpoint)
    if stage == "page":
        planned = next(page for page in p["plan"]["pages"] if page["id"] == target)
        expected_ids = [f"{target}-K{i:02}" for i in range(1, planned["panel_count"] + 1)]
        generation_schema["properties"]["panels"].update(
            minItems=len(expected_ids), maxItems=len(expected_ids)
        )
        ctx["required_panel_ids"] = expected_ids
        ctx["panel_count_policy"] = "严格保留这些画格；排版遗漏时修正 layout_rows，不得删除画格来迁就排版。"
    if stage == "drawing_prompt":
        from .framing import FRAMING, source_text

        generation_schema["properties"]["framing"] = copy.deepcopy(FRAMING)
        generation_schema["required"].append("framing")
        ctx["original_panel_text"] = source_text(p, ctx["panel"])
        # Background/blurred figures still belong to the confirmed panel cast.
        # Bind each slot rather than letting the model omit a secondary person
        # or substitute a role-library UUID for a canonical character ID.
        anchors = generation_schema["properties"]["character_anchors"]
        anchor_item = anchors.pop("items")
        required_ids = [c["id"] for c in ctx["bound_characters"]]
        anchors.update(minItems=len(required_ids), maxItems=len(required_ids))
        anchors["prefixItems"] = []
        for character_id in required_ids:
            item = copy.deepcopy(anchor_item)
            item["properties"]["character_id"]["const"] = character_id
            anchors["prefixItems"].append(item)
        if not required_ids:
            anchors.pop("prefixItems")
            anchors["items"] = anchor_item
        ctx["required_character_anchor_ids"] = required_ids
    if stage in ["page", "panel"]:
        ps = (
            generation_schema["properties"]["panels"]["items"]
            if stage == "page"
            else generation_schema["properties"]["panel"]
        )
        ps["properties"]["characters"]["items"]["properties"]["character_id"][
            "enum"
        ] = [c["id"] for c in p["canon"]["characters"]]
        ps["properties"]["scene_id"]["enum"] = [s["id"] for s in p["canon"]["scenes"]]
        if p["canon"]["props"]:
            ps["properties"]["props"]["items"]["properties"]["prop_id"]["enum"] = [
                o["id"] for o in p["canon"]["props"]
            ]
        else:
            ps["properties"]["props"]["maxItems"] = 0
        generation_schema["properties"]["lettering"]["items"]["properties"][
            "speaker_id"
        ]["enum"] = [None] + [c["id"] for c in p["canon"]["characters"]]
        state_schema = {
            "type": "object",
            "properties": {k: {"type": "string"} for k in ctx["start_state"]},
            "required": list(ctx["start_state"]),
            "additionalProperties": False,
        }
        if stage == "page":
            generation_schema["properties"]["state_in"] = {
                "type": "object",
                "properties": {
                    k: {"type": "string", "const": v}
                    for k, v in ctx["start_state"].items()
                },
                "required": list(ctx["start_state"]),
                "additionalProperties": False,
            }
            generation_schema["properties"]["state_out"] = state_schema
            generation_schema["properties"]["panels"]["items"]["properties"][
                "state_after"
            ] = state_schema
        else:
            generation_schema["properties"]["panel"]["properties"]["state_after"] = (
                state_schema
            )
    prompts = Path(__file__).parent / "prompts"
    prompt = (prompts / "common.md").read_text(encoding="utf-8") + "\n"
    if stage == "panel":
        prompt += (prompts / "page.md").read_text(encoding="utf-8") + "\n"
    prompt += (prompts / f"{stage}.md").read_text(encoding="utf-8")
    prompt += "\n\n" + DEFAULT_OUTPUT_POLICY
    messages = [
        {"role": "system", "content": prompt},
        {
            "role": "user",
            "content": json.dumps(ctx, ensure_ascii=False, separators=(",", ":")),
        },
    ]
    original = copy.deepcopy(messages)
    for attempt in range(2):
        run = Path(logdir) / str(attempt)
        messages, token_count = budget_messages(messages, original, cfg, update)
        body = {
            "model": cfg.get("model_name", "comic-local"),
            "messages": messages,
            "temperature": cfg["temperature"],
            "top_p": 0.8,
            "top_k": 20,
            "min_p": 0,
            "presence_penalty": 1.5,
            "max_tokens": cfg["max_output_tokens"],
            "stream": False,
            "chat_template_kwargs": {"enable_thinking": False},
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": stage,
                    "strict": True,
                    "schema": generation_schema,
                },
            },
        }
        if cfg.get("model_backend") == "strata":
            body["reasoning_effort"] = "none"
        body = stream_body(body)
        e.save(run / "request.json", body)
        update(f"模型正在生成（第 {attempt + 1} 次，输入 {token_count} token）")
        start = time.monotonic()
        response = chat_stream(
            endpoint() + "/v1/chat/completions",
            body,
            cfg["model_timeout_seconds"],
            update,
            run,
        )
        metadata = {
            "wall_seconds": round(time.monotonic() - start, 3),
            "input_tokens": token_count,
            "usage": response.get("usage"),
            "timings": response.get("timings"),
            "attempt": attempt + 1,
        }
        e.save(run / "response.json", response)
        e.save(run / "metrics.json", metadata)
        try:
            choice = response["choices"][0]
            e.need(choice["finish_reason"] == "stop", "生成被截断，不能确认为完整内容")
            payload = json.loads(choice["message"]["content"])
            if stage == "drawing_prompt":
                e.jsonschema.validate(payload, generation_schema)
            if stage in ["page", "panel"]:
                canonical = {
                    c["name"].lstrip("/"): c["id"] for c in p["canon"]["characters"]
                }
                aliases = {
                    c["id"]: canonical[c["name"].lstrip("/")]
                    for c in p["brief"].get("character_refs", [])
                    if c["name"].lstrip("/") in canonical
                }
                panels = payload["panels"] if stage == "page" else [payload["panel"]]
                reference_repairs = []
                for item in [
                    a for panel in panels for a in panel["characters"]
                ] + payload["lettering"]:
                    key = "character_id" if "character_id" in item else "speaker_id"
                    if item.get(key) in aliases:
                        before = item[key]
                        item[key] = aliases[before]
                        reference_repairs.append(
                            {"field": key, "before": before, "after": item[key]}
                        )
                if reference_repairs:
                    metadata["character_reference_repairs"] = reference_repairs
                    e.save(run / "character-reference-repairs.json", reference_repairs)
                if stage == "page" and p.get("visual_policy"):
                    from .visual_policy import apply_visual_policy

                    payload, changes = apply_visual_policy(payload, p)
                    metadata["visual_policy_repairs"] = len(changes)
                    e.save(run / "visual-policy-repairs.json", changes)
            if stage == "page":
                payload, changes = repair_page_layout(payload, expected_ids)
                if changes:
                    metadata["layout_reference_repairs"] = changes
                    e.save(run / "layout-reference-repairs.json", changes)
            try:
                e.merged(p, stage, target, payload)
            except ValueError as error:
                if stage not in ["page", "panel"] or not any(
                    text in str(error)
                    for text in ["气泡顺序重复或跳号", "文字编号与所属格不一致"]
                ):
                    raise
                payload, changes = repair_lettering_numbering(payload)
                e.merged(p, stage, target, payload)
                metadata["structural_repairs"] = changes
                e.save(run / "numbering-repairs.json", changes)
                e.save(run / "validated-payload.json", payload)
                e.save(run / "metrics.json", metadata)
            if stage == "page" and metadata.get("layout_reference_repairs"):
                e.save(run / "validated-payload.json", payload)
                e.save(run / "metrics.json", metadata)
            return payload, metadata
        except (ValueError, e.jsonschema.ValidationError) as exc:
            (run / "validation-error.txt").write_text(str(exc), encoding="utf-8")
            if attempt:
                raise
            update(
                {
                    "message": "草稿结构校验未通过，正在自动修正：" + str(exc)[:240],
                    "runtime": None,
                }
            )
            messages.append(
                {
                    "role": "assistant",
                    "content": response["choices"][0]["message"]["content"],
                }
            )
            messages.append(
                {
                    "role": "user",
                    "content": "上一次未通过校验，请依据原始资料重新输出完整 JSON，特别修正以下错误："
                    + str(exc)[:1800],
                }
            )


class ModelManager:
    def __init__(self):
        self.process = None
        self.log = None
        self.lock = threading.Lock()
        self.message = "未启动语言模型"
        self.job = None
        self.vision = False
        self.worker_port = None
        self.strata = None

    def owned_worker(self):
        return self.strata.process if self.strata is not None else self.process

    def owns_live_worker(self):
        worker = self.owned_worker()
        return worker is not None and worker.poll() is None

    def status(self):
        cfg = raw_settings() if self.process is not None and self.process.poll() is None else settings()
        if self.strata is not None:
            return self.strata.status()
        if cfg.get("model_backend") == "strata":
            from .strata_model import StrataManager
            return StrataManager(cfg).status()
        path = local_path(cfg["model_path"])
        available = path.exists() and path.stat().st_size == cfg["model_size"]
        owned = self.process is not None and self.process.poll() is None
        try:
            import socket
            with socket.create_connection(("127.0.0.1", cfg["model_port"]), timeout=0.06):
                pass
            ready = (
                request_json(endpoint(cfg) + "/health", timeout=1).get("status") == "ok"
            )
        except Exception:
            ready = False
        return {
            "file_size_ok": available,
            "ready": ready,
            "owned": owned,
            "message": self.message,
            "generating": GENERATION_LOCK.locked(),
            "model_path": str(path),
            "context_size": cfg["context_size"],
            "vision_ready": ready and owned and self.vision,
        }

    def start(self, vision=False, update=lambda message: None):
        with self.lock:
            if settings().get("model_backend") == "strata":
                e.need(not vision, "当前提示词模型未启用图片输入")
                if self.process and self.process.poll() is None:
                    self.stop()
                if self.strata is not None and any(self.strata.cfg.get(k) != settings().get(k)
                        for k in ("model_name", "context_size", "strata_profile")):
                    self.stop()
                if self.strata is None:
                    from .strata_model import StrataManager
                    self.strata = StrataManager(settings())
                self.strata.start(update)
                return
            if self.strata is not None:
                self.stop()
            if vision and self.status()["owned"] and not self.vision:
                self.stop()
            if self.status()["ready"]:
                e.need(
                    self.process is not None and self.process.poll() is None,
                    "模型端口已被外部进程占用。请先关闭外部服务，再由本界面启动，以确保配置和卸载可控",
                )
                return
            if self.process and self.process.poll() is None:
                return
            cfg = settings()
            path = local_path(cfg["model_path"])
            self.message = "正在核对模型 SHA-256（读取约 15.66 GiB）"

            def verify(path, label):
                started = time.monotonic()

                def progress(done, total):
                    self.message = (
                        f"{label}：{done / 1024**3:.2f} / {total / 1024**3:.2f} GiB"
                    )
                    emit(
                        update,
                        label,
                        "文件实际读取量",
                        completed=round(done / 1024**2, 1),
                        total=round(total / 1024**2, 1),
                        unit="MiB",
                        remaining_seconds=(time.monotonic() - started)
                        * (total - done)
                        / done
                        if done
                        else None,
                    )

                return filehash(path, progress)

            e.need(
                path.exists() and path.stat().st_size == cfg["model_size"],
                "语言模型未就绪，请打开右上角「下载模型」",
            )
            e.need(
                verify(path, "校验语言模型") == cfg["model_sha256"],
                "模型 SHA-256 不匹配，拒绝加载",
            )
            e.save(
                DATA / "verification.json",
                {"path": str(path), "sha256": cfg["model_sha256"], "time": e.stamp()},
            )
            args = [
                str(local_path(cfg["runtime_path"])),
                "-m",
                str(cfg["model_path"]),
                "--alias",
                "comic-local",
                "--host",
                "127.0.0.1",
                "--port",
                str(cfg["model_port"]),
                "--ctx-size",
                str(cfg["context_size"]),
                "--parallel",
                "1",
                "--slot-save-path",
                "data/model-slots",
                "--gpu-layers",
                "auto" if cfg.get("auto_fit_vram") else "all",
                "--device",
                "CUDA0",
                "--flash-attn",
                "on",
                "--cache-type-k",
                cfg["cache_type_k"],
                "--cache-type-v",
                cfg["cache_type_v"],
                "--batch-size",
                str(cfg["batch_size"]),
                "--ubatch-size",
                str(cfg["ubatch_size"]),
                "--jinja",
                "--reasoning",
                "off",
                "--fit",
                "on" if cfg.get("auto_fit_vram") else "off",
            ]
            if vision:
                vc = e.read(ROOT / "config/vision-model.json")
                vp = ROOT / vc["directory"] / vc["name"]
                e.need(
                    vp.is_file() and vp.stat().st_size == vc["size"],
                    "视觉模块未就绪，请打开右上角「下载模型」",
                )
                e.need(verify(vp, "校验视觉模块") == vc["sha256"], "视觉模块校验失败")
                args += [
                    "--mmproj",
                    str(Path(vc["directory"]) / vc["name"]),
                    "--image-max-tokens",
                    "1024",
                ]
            self.vision = vision
            (DATA / "model-slots").mkdir(parents=True, exist_ok=True)
            (DATA / "logs").mkdir(parents=True, exist_ok=True)
            self.log = (DATA / "logs/model-server.log").open("a", encoding="utf-8")
            log_offset = (DATA / "logs/model-server.log").stat().st_size
            e.save(DATA / "logs/model-command.json", args)
            self.worker_port = cfg["model_port"]
            self.process = subprocess.Popen(
                args,
                cwd=ROOT,
                env={**os.environ, "LLAMA_API_KEY": worker_key(self.worker_port)},
                stdout=self.log,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self.job = attach_exit_job(self.process)
            self.message = "模型加载中；失败详情见 data/logs/model-server.log"
            emit(
                update,
                "启动推理进程",
                "llama.cpp",
                detail="正在等待模型回报加载阶段；此阶段无可测总量",
            )
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                with (DATA / "logs/model-server.log").open("rb") as monitor:
                    monitor.seek(log_offset)
                    lines = (
                        monitor.read().decode("utf-8", errors="replace").splitlines()
                    )
                    log_offset = monitor.tell()
                for line in lines:
                    phase = next(
                        (
                            label
                            for marker, label in [
                                ("ggml_cuda_init", "初始化显卡"),
                                ("loading model", "载入模型权重"),
                                ("load_tensors", "读取权重张量"),
                                ("llama_context", "分配推理缓存"),
                                ("warmup", "模型预热"),
                                ("initializing, n_slots", "初始化推理会话"),
                            ]
                            if marker in line
                        ),
                        None,
                    )
                    if phase:
                        self.message = phase
                        emit(
                            update,
                            phase,
                            "llama.cpp 加载日志",
                            detail="已收到该加载阶段回报；模型未提供此阶段总量",
                        )
                e.need(
                    self.process.poll() is None,
                    "模型启动失败，请查看 data/logs/model-server.log",
                )
                if self.status()["ready"]:
                    self.message = "语言模型已就绪；绘图前点击卸载"
                    emit(
                        update,
                        "语言模型已就绪",
                        "llama.cpp 健康检查",
                        completed=1,
                        total=1,
                        unit="次加载",
                        remaining_seconds=0,
                    )
                    return
                time.sleep(1)
            self.stop()
            raise ValueError("模型加载超时，已停止本项目模型进程")

    def unload_if_idle(self):
        """Called while the owning job holds GENERATION_LOCK; only private workers qualify."""
        with self.lock:
            if not self.owns_live_worker():
                return {"status": "already_unloaded"}
            try:
                if self.strata is not None:
                    result = self.strata.unload_if_idle()
                    if result["status"] == "unloaded":
                        self.strata = None
                    return result
                slots = request_json(endpoint({"model_port": self.worker_port}) + "/slots", timeout=5)
                if not isinstance(slots, list) or not slots or any(
                    slot.get("is_processing") is not False for slot in slots
                ):
                    return {"status": "deferred", "reason": "无法确认所有模型请求已结束"}
                self.stop()
                return {"status": "unloaded", "method": "owned_llama_worker"}
            except (OSError, ValueError, TypeError, AttributeError) as exc:
                return {"status": "deferred", "reason": str(exc)}

    def clear_context(self):
        """Clear only our idle text server; persisted outputs are not session cache."""
        if self.strata is not None:
            return self.strata.clear_context()
        if self.process is None or self.process.poll() is not None:
            return {"status": "cleared", "method": "already_unloaded"}
        try:
            origin = endpoint({"model_port": self.worker_port}) if self.worker_port is not None else endpoint()
            slots = request_json(origin + "/slots", timeout=10)
            e.need(isinstance(slots, list) and slots, "未能确认文字模型会话")
            if any(s.get("is_processing") for s in slots):
                return {"status": "deferred", "reason": "文字模型仍在处理请求"}
            for slot in slots:
                result = request_json(origin + f"/slots/{int(slot['id'])}?action=erase", {}, timeout=15)
                e.need(result.get("id_slot") == slot['id'], "上下文清理结果不符")
            return {"status": "cleared", "method": "erase_slots", "slots": len(slots)}
        except Exception as exc:
            return {"status": "failed", "method": "erase_slots", "reason": str(exc)}

    def inference_context(self):
        from .paths import prompt_model
        if self.strata is not None:
            return prompt_model("page_redraw" if self.strata.cfg["context_size"] == 65536 else None)
        return nullcontext()

    def prepare_redraw(self):
        """Keep text weights resident while lending GPU cache space to drawing."""
        self.clear_context()
        if self.strata is not None:
            self.strata.reserve_for_drawing()

    def stop(self):
        if self.strata is not None:
            self.strata.stop()
            self.strata = None
        terminate_tree(self.process)
        release_key(self.worker_port)
        self.worker_port = None
        close_job(self.job)
        self.job = None
        if self.log:
            self.log.close()
            self.log = None
        self.process = None
        self.vision = False
        self.message = "本项目语言模型已卸载；可进行后续绘图"
