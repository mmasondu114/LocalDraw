"""Read safetensors headers only; never execute pickle/model code."""

import json
import struct
import re
from pathlib import Path
from .installed_assets import models as model_directory
from . import engine as e
from .paths import ROOT, DATA

_DISK_CACHE = {"path": None, "mtime_ns": None, "entries": {}}


def _is_krea2_adapter(header):
    """Recognize native and Diffusers/ModelScope Krea2 adapters (including partial-block sliders)."""
    # 1) Native ComfyUI / ai-toolkit Krea2 blocks (any block index, e.g. blocks.0 or blocks.14)
    has_gate = False
    has_wk = False
    for key, val in header.items():
        m = re.fullmatch(r"diffusion_model\.blocks\.(\d+)\.attn\.(gate|wk)\.lora_A\.weight", key)
        if not m:
            continue
        idx, mod = m.groups()
        down = val.get("shape", [])
        up = header.get(f"diffusion_model.blocks.{idx}.attn.{mod}.lora_B.weight", {}).get("shape", [])
        expected_out = 6144 if mod == "gate" else 1536
        if len(down) == 2 and down[0] > 0 and down[1] == 6144 and up == [expected_out, down[0]]:
            if mod == "gate":
                has_gate = True
            else:
                has_wk = True
        if has_gate and has_wk:
            return True
    # 2) Diffusers / ModelScope Krea2 format (transformer.text_fusion.* + transformer.img_in)
    img_a = header.get("transformer.img_in.lora_A.weight", {}).get("shape", [])
    img_b = header.get("transformer.img_in.lora_B.weight", {}).get("shape", [])
    fin_a = header.get("transformer.final_layer.linear.lora_A.weight", {}).get("shape", [])
    fin_b = header.get("transformer.final_layer.linear.lora_B.weight", {}).get("shape", [])
    if (len(img_a) == 2 and img_a[0] > 0 and img_a[1] == 64 and img_b == [6144, img_a[0]]
            and len(fin_a) == 2 and fin_a[0] > 0 and fin_a[1] == 6144 and fin_b == [64, fin_a[0]]):
        return True
    return False


def _is_moss_adapter(header):
    """Recognize native MOSS projections when publishers omit architecture tags."""
    modules = set()
    for key, value in header.items():
        match = re.fullmatch(
            r"(?:diffusion_model\.)?layers\.(\d+)\.proj\."
            r"(q|k|v|o|ffn_up_gate|ffn_down)\.lora_(A|B|down|up)\.weight", key)
        if not match:
            continue
        layer, module, direction = match.groups()
        shape = value.get("shape", [])
        if int(layer) >= 42 or len(shape) != 2:
            return False
        output_size, input_size = {"ffn_up_gate": (16384, 3072),
                                   "ffn_down": (3072, 8192)}.get(module, (3072, 3072))
        if (shape[1] != input_size if direction in {"A", "down"} else shape[0] != output_size):
            return False
        modules.add(module)
    return bool(modules & {"q", "k", "v", "o"}) and bool(modules & {"ffn_up_gate", "ffn_down"})


def _read_header_facts(path, file_size=None):
    path = Path(path)
    e.need(not Path(str(path) + '.aria2').exists() and not Path(str(path) + '.aria2__temp').exists(),
           'LoRA 尚在下载，校验完成后才能加载')
    total_size = file_size if file_size is not None else path.stat().st_size
    with path.open("rb") as f:
        raw = f.read(8)
        e.need(len(raw) == 8, "LoRA 文件不完整")
        if raw.lower().startswith((b"<!doctyp", b"<html")):
            raise ValueError("文件内容是网页，请从模型版本页重新下载 safetensors 权重")
        size = struct.unpack("<Q", raw)[0]
        e.need(
            2 <= size <= 16_000_000 and size + 8 < total_size,
            "无效 safetensors 文件头",
        )
        header = json.loads(f.read(size))
    metadata = header.pop("__metadata__", {})
    payload_size = total_size - size - 8
    for tensor in header.values():
        bounds = tensor.get("data_offsets", [])
        e.need(
            len(bounds) == 2 and 0 <= bounds[0] <= bounds[1] <= payload_size,
            "权重文件截断或偏移无效",
        )
    version = (
        metadata.get("ss_base_model_version", "")
        + " "
        + metadata.get("modelspec.architecture", "")
    )
    dimensions = sorted(
        {
            v["shape"][1]
            for k, v in header.items()
            if "attn2_to_k" in k and "lora_down" in k and len(v["shape"]) == 2
        }
    )
    base_hint = (version + " " + metadata.get("ss_network_module", "") + " " + metadata.get("base_model", "")).lower()
    if "krea2" in base_hint or "krea-2" in base_hint:
        family = "Krea2"
    elif "moss" in (version + " " + metadata.get("ss_network_module", "")).lower() or _is_moss_adapter(header):
        family = "MOSS-Image"
    elif "anima" in (version + " " + metadata.get("ss_network_module", "") + " " + metadata.get("ss_sd_model_name", "")).lower():
        family = "Anima"
    elif (
        "sdxl" in version.lower()
        or "diffusion-xl" in version.lower()
        or 2048 in dimensions
    ):
        family = "SDXL"
    elif dimensions == [768]:
        family = "SD 1.x（结构判断）"
    elif dimensions == [1024]:
        family = "SD 2.x（结构判断）"
    elif _is_krea2_adapter(header):
        family = "Krea2"
    else:
        family = "未知"
    return {
        "family": family,
        "title": metadata.get("modelspec.title") or metadata.get("ss_output_name", ""),
        "training_model": metadata.get("ss_sd_model_name", "未记录"),
        "network": metadata.get("ss_network_module", "未知"),
        "cross_attention_dimensions": dimensions,
        "size": total_size,
    }


def _evaluate_for_checkpoint(facts, checkpoint):
    family = facts["family"]
    compatible = True if family == "SDXL" else (False if family != "未知" else None)
    from .lora_folders import is_anima, directory
    if is_anima(checkpoint):
        compatible = family == "Anima" if family != "未知" else None
    selected_family = {"krea2": "Krea2", "moss_image": "MOSS-Image"}.get(directory(checkpoint))
    if selected_family:
        compatible = family == selected_family if family != "未知" else None
    title = facts["title"]
    trained = facts["training_model"]
    if not compatible:
        note = (
            "与当前绘图底模结构不兼容"
            if compatible is False
            else "元数据不足，需要来源说明或进一步验证"
        )
    elif family == "Anima":
        note = "Anima 架构；28/40 层 LoRA 由专用补丁映射到 52 层，角色效果需实测"
    elif family in {"Krea2", "MOSS-Image"}:
        note = family + " 结构兼容；当前底模上的实际效果仍需验证"
    elif "illustrious" in title.lower() or "illustrious" in trained.lower():
        note = "SDXL 结构兼容；资料指向 Illustrious，不能视为专门适配 Animagine，效果需实测"
    elif "animagine" in trained.lower():
        note = "SDXL 结构兼容；训练底模为 " + trained + "，与当前 4.0 版本不同"
    else:
        note = "SDXL 结构兼容；当前底模上的实际效果仍需验证"
    return {
        "family": family,
        "structurally_compatible": compatible,
        "title": title,
        "training_model": trained,
        "network": facts["network"],
        "cross_attention_dimensions": list(facts["cross_attention_dimensions"]),
        "note": note,
        "size": facts["size"],
        "tested_load": False,
    }


def inspect_lora(path, checkpoint=None):
    return _evaluate_for_checkpoint(_read_header_facts(path), checkpoint)


def _load_disk_cache():
    cache_path = DATA / "lora-inspection-cache.json"
    if not cache_path.is_file():
        return cache_path, {}
    mtime = cache_path.stat().st_mtime_ns
    if _DISK_CACHE["path"] == cache_path and _DISK_CACHE["mtime_ns"] == mtime:
        return cache_path, _DISK_CACHE["entries"]
    try:
        data = e.read(cache_path)
        entries = data.get("entries", {}) if isinstance(data, dict) and data.get("version") == 3 else {}
    except Exception:
        entries = {}
    _DISK_CACHE.update(path=cache_path, mtime_ns=mtime, entries=entries)
    return cache_path, entries


def inspect_local(checkpoint=None):
    import copy
    from .lora_names import identities, search_identity
    roster = identities()
    results = {}
    tested_path = DATA / "lora-load-tests.json"
    tests = e.read(tested_path) if tested_path.exists() else {}
    configured = e.read(ROOT / "config/comfyui.json")["checkpoint"]
    selected = checkpoint or configured
    from .lora_folders import directory
    lora_root = model_directory(ROOT) / "loras"
    scan_root = lora_root
    if checkpoint and (scan_root / directory(checkpoint)).is_dir():
        scan_root = scan_root / directory(checkpoint)
    cache_path, cache = _load_disk_cache()
    cache_dirty = False
    for p in scan_root.rglob("*.safetensors"):
        name = p.relative_to(lora_root).as_posix()
        cache_key = f"{selected}\n{name}"
        try:
            stat = p.stat()
            downloading = any(Path(str(p) + suffix).exists() for suffix in ('.aria2', '.aria2__temp'))
            if downloading:
                result = inspect_lora(p, checkpoint=selected)
            else:
                cached = cache.get(cache_key)
                if (
                    cached
                    and cached.get("size") == stat.st_size
                    and cached.get("mtime_ns") == stat.st_mtime_ns
                    and cached.get("ctime_ns") == stat.st_ctime_ns
                    and "result" in cached
                ):
                    result = copy.deepcopy(cached["result"])
                else:
                    result = inspect_lora(p, checkpoint=selected)
                    cache[cache_key] = {
                        "size": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns,
                        "ctime_ns": stat.st_ctime_ns,
                        "result": copy.deepcopy(result),
                    }
                    cache_dirty = True
        except Exception as exc:
            result = {
                "structurally_compatible": None,
                "note": str(exc),
                "family": "无法读取",
            }
            stat = None
        result.update(search_identity(name, roster))
        test = tests.get(name, {})
        if (
            stat is not None
            and test.get("status") == "passed"
            and test.get("file_size") == stat.st_size
            and test.get("mtime_ns") == stat.st_mtime_ns
            and test.get("checkpoint") == selected
        ):
            result["tested_load"] = True
            result["note"] += "；已在当前底模真实加载出图"
        results[name] = result
    if cache_dirty:
        try:
            e.save(cache_path, {"version": 3, "entries": cache})
            _DISK_CACHE.update(path=cache_path, mtime_ns=cache_path.stat().st_mtime_ns, entries=cache)
        except OSError:
            pass
    return results
