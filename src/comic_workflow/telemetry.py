"""Measured runtime events: streamed llama.cpp responses and ComfyUI WebSocket."""

import json
import time
import urllib.request
import urllib.error
import urllib.parse
import statistics
import datetime
from pathlib import Path

import websocket

from . import engine as e
from .paths import DATA


def profile_key(body):
    schema = body.get("response_format", {}).get("json_schema", {})
    name = schema.get("name", "text")
    if name == "creative":
        props = schema.get("schema", {}).get("properties", {})
        name = (
            "story"
            if "story" in props
            else "character"
            if "identity" in props
            else "review"
        )
    return name


def samples(key):
    path = DATA / "performance" / (key + ".json")
    if path.exists():
        return e.read(path)
    return []


def record_sample(key, sample):
    e.save(DATA / "performance" / (key + ".json"), (samples(key) + [sample])[-30:])


def drawing_durations():
    values = [x["seconds"] for x in samples("drawing-panel") if x.get("seconds")]
    if values:
        return values
    for root in [DATA / "projects", DATA / "test-results/features-live"]:
        for path in sorted(root.glob("**/drawing-review.json"), reverse=True)[:30]:
            try:
                report = e.read(path)
                project = next(
                    parent
                    for parent in path.parents
                    if (parent / "project.json").exists()
                )
                batch = e.read(project / "images" / report["batches"][0] / "batch.json")
                start = datetime.datetime.strptime(
                    batch["created"], "%Y%m%d-%H%M%S-%f"
                ).timestamp()
                duration = path.stat().st_mtime - start
                if 10 < duration < 18000:
                    values.append(duration)
            except (OSError, ValueError, KeyError, IndexError, StopIteration):
                pass
    return values


def output_target(body):
    key = profile_key(body)
    values = [x["tokens"] for x in samples(key) if x.get("tokens")]
    if not values:
        # Reuse real previous responses rather than an invented token target.
        roots = [
            DATA / "creative",
            DATA / "test-results/features-live",
            DATA / "test-results/lora-live",
        ]
        for root in roots:
            for path in sorted(root.glob("**/request.json"), reverse=True)[:60]:
                try:
                    if profile_key(e.read(path)) == key:
                        metrics = e.read(path.parent / "metrics.json")
                        tokens = (metrics.get("usage") or {}).get("completion_tokens")
                        if tokens:
                            values.append(tokens)
                except (OSError, ValueError, KeyError):
                    pass
    return statistics.median(values[-20:]) if values else None


def emit(update, phase, source, **values):
    update(
        {
            "runtime": {
                "phase": phase,
                "source": source,
                "updated_at": time.time(),
                **values,
            }
        }
    )


def stream_body(body):
    from .paths import settings
    if settings().get("model_backend") == "strata":
        body = {**body, "strata_checkpoint": False}
    return {
        **body,
        "stream": True,
        "stream_options": {"include_usage": True},
        "return_progress": True,
        "timings_per_token": True,
    }


def chat_stream(url, body, timeout, update, logdir=None):
    """Reassemble the same chat response, rejecting partial/disconnected streams."""
    body = stream_body(body)
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    content, reasoning = [], []
    usage, timings, finish = None, {}, None
    started, last_emit = time.monotonic(), 0
    source = "Strata" if body.get("model", "").startswith("qwen3.8-flash-next") else "llama.cpp"
    runtime = {
        "phase": "等待模型接收输入",
        "source": source,
        "updated_at": time.time(),
    }
    update({"runtime": runtime})
    chars = 0
    output_tokens = None
    output_started = False
    expected = output_target(body)
    done = False
    trace = None
    if logdir:
        Path(logdir).mkdir(parents=True, exist_ok=True)
        trace = (Path(logdir) / "runtime-events.jsonl").open("w", encoding="utf-8")

    def publish(force=False):
        nonlocal last_emit
        now = time.monotonic()
        if not force and now - last_emit < 0.25:
            return
        last_emit = now
        update({"runtime": dict(runtime)})
        if trace:
            trace.write(json.dumps(runtime, ensure_ascii=False) + "\n")
            trace.flush()

    try:
        with opener.open(request, timeout=timeout) as response:
            for raw in response:
                e.need(
                    time.monotonic() - started <= timeout,
                    "模型生成超时，未应用部分输出",
                )
                line = raw.decode("utf-8").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    done = True
                    break
                chunk = json.loads(data)
                e.need(
                    not chunk.get("error"), "模型流返回错误：" + str(chunk.get("error"))
                )
                if chunk.get("usage"):
                    usage = chunk["usage"]
                    output_tokens = usage.get("completion_tokens", output_tokens)
                if chunk.get("timings"):
                    timings = chunk["timings"]
                    output_tokens = timings.get("predicted_n", output_tokens)
                prompt = chunk.get("prompt_progress")
                if prompt and not output_started:
                    runtime = {
                        "phase": "处理输入",
                        "source": source,
                        "completed": prompt["processed"],
                        "total": prompt["total"],
                        "unit": "输入 token",
                        "cached": prompt.get("cache", 0),
                        "updated_at": time.time(),
                    }
                    actual = prompt["processed"] - prompt.get("cache", 0)
                    if actual > 0:
                        runtime["remaining_seconds"] = max(
                            0,
                            (prompt["total"] - prompt["processed"])
                            * prompt.get("time_ms", 0)
                            / 1000
                            / actual,
                        )
                for choice in chunk.get("choices", []):
                    delta = choice.get("delta", {})
                    text = delta.get("content") or ""
                    thought = delta.get("reasoning_content") or ""
                    if text or thought:
                        output_started = True
                        content.append(text)
                        reasoning.append(thought)
                        chars += len(text)
                    if choice.get("finish_reason") is not None:
                        finish = choice["finish_reason"]
                if output_started:
                    runtime = {
                        "phase": "生成输出",
                        "source": source,
                        "output_tokens": output_tokens,
                        "characters": chars,
                        "tokens_per_second": timings.get("predicted_per_second"),
                        "output_limit": body.get("max_tokens"),
                        "updated_at": time.time(),
                    }
                    if (
                        expected
                        and output_tokens is not None
                        and runtime["tokens_per_second"]
                        and runtime["tokens_per_second"] > 0
                    ):
                        runtime["remaining_seconds"] = (
                            expected - output_tokens
                        ) / runtime["tokens_per_second"]
                        runtime["estimate_basis"] = "同类任务历史输出长度与当前实际速度"
                publish(
                    force=finish is not None
                    or bool(prompt and prompt.get("processed") == prompt.get("total"))
                )
        e.need(done and finish is not None, "模型连接在完整输出前断开，未应用部分结果")
        runtime.update(
            phase="输出接收完成，校验结构",
            updated_at=time.time(),
            output_tokens=output_tokens,
        )
        publish(force=True)
        if finish == "stop" and output_tokens:
            record_sample(
                profile_key(body),
                {"tokens": output_tokens, "seconds": time.monotonic() - started},
            )
        return {
            "choices": [
                {
                    "index": 0,
                    "finish_reason": finish,
                    "message": {
                        "role": "assistant",
                        "content": "".join(content),
                        "reasoning_content": "".join(reasoning),
                    },
                }
            ],
            "usage": usage,
            "timings": timings,
        }
    except urllib.error.HTTPError as exc:
        raise ValueError(
            f"模型服务返回 {exc.code}：{exc.read().decode(errors='replace')[:1500]}"
        ) from exc
    finally:
        if trace:
            trace.close()


NODE_NAMES = {
    "IPAdapterModelLoader": "加载角色参考模块",
    "CLIPVisionLoader": "加载参考图编码器",
    "IPAdapterAdvanced": "读取角色参考图并绑定画面区域",
    "LoadImage": "读取角色参考图",
    "CheckpointLoaderSimple": "加载绘图权重",
    "LoraLoader": "加载 LoRA",
    "CLIPTextEncode": "编码画面描述",
    "EmptyLatentImage": "准备画布",
    "KSampler": "采样绘图",
    "VAEDecode": "解码成图片",
    "SaveImage": "保存图片",
}


class ComfyProgress:
    def __init__(self, endpoint, client_id):
        url = urllib.parse.urlparse(endpoint)
        e.need(
            url.scheme == "http" and url.hostname in ("127.0.0.1", "localhost"),
            "实时进度只连接本机 ComfyUI",
        )
        self.socket = None
        self.error = None
        self.step_samples = {}
        try:
            self.socket = websocket.create_connection(
                urllib.parse.urlunparse(
                    url._replace(
                        scheme="ws",
                        path="/ws",
                        query=urllib.parse.urlencode({"clientId": client_id}),
                    )
                ),
                timeout=1,
                http_no_proxy=["127.0.0.1", "localhost"],
            )
            self.socket.settimeout(0.15)
        except Exception as exc:
            self.error = f"实时进度连接失败：{exc}"

    def poll(self, prompt_id, workflow):
        if self.error:
            message, self.error = self.error, None
            return {
                "phase": "实时进度不可用，正在查询最终结果",
                "detail": message,
                "source": "ComfyUI",
                "updated_at": time.time(),
            }
        if not self.socket:
            time.sleep(0.1)
            return None
        try:
            raw = self.socket.recv()
            if not raw:
                raise ConnectionError("实时连接已关闭")
            if not isinstance(raw, str):
                return None  # Binary previews are not progress events.
            event = json.loads(raw)
            data = event.get("data", {})
            if data.get("prompt_id") != prompt_id:
                return None
            kind = event.get("type")
            node_id = str(data.get("node", ""))
            node_type = workflow.get(node_id, {}).get("class_type", "")
            state = {
                "source": "ComfyUI",
                "prompt_id": prompt_id,
                "node": node_id,
                "node_type": node_type,
                "updated_at": time.time(),
            }
            if kind == "progress":
                history = self.step_samples.setdefault(node_id, [])
                value = data["value"]
                if history and value <= history[-1][1]:
                    history.clear()
                history.append((time.monotonic(), value))
                del history[:-8]
                result = {
                    **state,
                    "phase": workflow.get(node_id, {}).get("_meta", {}).get("title") or NODE_NAMES.get(node_type, "执行节点"),
                    "completed": data["value"],
                    "total": data["max"],
                    "unit": "采样步" if node_type == "KSampler" else "步",
                }
                if value >= data["max"]:
                    result["remaining_seconds"] = 0
                elif len(history) >= 2:
                    result["remaining_seconds"] = (
                        (history[-1][0] - history[0][0])
                        / (value - history[0][1])
                        * (data["max"] - value)
                    )
                    result["estimate_basis"] = (
                        "最近采样步骤的实测速度，不含首次加载时间"
                    )
                return result
            if kind == "executing":
                self.step_samples.pop(node_id, None)
                return {
                    **state,
                    "phase": workflow.get(node_id, {}).get("_meta", {}).get("title") or NODE_NAMES.get(node_type, node_type or "节点执行完成"),
                }
            if kind in ["execution_error", "execution_interrupted"]:
                return {
                    **state,
                    "phase": "绘图执行失败",
                    "detail": data.get("exception_message", kind),
                }
            if kind == "execution_start":
                return {**state, "phase": "开始执行绘图工作流"}
            if kind == "execution_cached":
                return {
                    **state,
                    "phase": "复用已缓存节点",
                    "detail": f"复用 {len(data.get('nodes', []))} 个节点",
                }
        except websocket.WebSocketTimeoutException:
            pass
        except Exception as exc:
            self.close()
            return {
                "phase": "实时进度已断开，正在查询最终结果",
                "detail": str(exc),
                "source": "ComfyUI",
                "updated_at": time.time(),
            }
        return None

    def close(self):
        if self.socket:
            self.socket.close()
            self.socket = None
