"""Read safetensors headers only; never execute pickle/model code."""

import json
import struct
import copy
import re
from functools import lru_cache
from pathlib import Path
from . import engine as e
from .paths import ROOT, DATA


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


def inspect_lora(path, checkpoint=None):
    path = Path(path)
    e.need(not Path(str(path) + '.aria2').exists() and not Path(str(path) + '.aria2__temp').exists(),
           'LoRA 尚在下载，校验完成后才能加载')
    with path.open("rb") as f:
        raw = f.read(8)
        e.need(len(raw) == 8, "LoRA 文件不完整")
        size = struct.unpack("<Q", raw)[0]
        e.need(
            2 <= size <= 16_000_000 and size + 8 < path.stat().st_size,
            "无效 safetensors 文件头",
        )
        header = json.loads(f.read(size))
    metadata = header.pop("__metadata__", {})
    payload_size = path.stat().st_size - size - 8
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
    if "krea2" in (version + " " + metadata.get("ss_network_module", "")).lower():
        family, compatible = "Krea2", False
    elif "moss" in (version + " " + metadata.get("ss_network_module", "")).lower() or _is_moss_adapter(header):
        family, compatible = "MOSS-Image", False
    elif "anima" in (version + " " + metadata.get("ss_network_module", "") + " " + metadata.get("ss_sd_model_name", "")).lower():
        family, compatible = "Anima", False
    elif (
        "sdxl" in version.lower()
        or "diffusion-xl" in version.lower()
        or 2048 in dimensions
    ):
        family, compatible = "SDXL", True
    elif dimensions == [768]:
        family, compatible = "SD 1.x（结构判断）", False
    elif dimensions == [1024]:
        family, compatible = "SD 2.x（结构判断）", False
    else:
        family, compatible = "未知", None
    from .lora_folders import is_anima
    if is_anima(checkpoint):
        compatible = family == "Anima" if family != "未知" else None
    from .lora_folders import directory
    selected_family = {"krea2": "Krea2", "moss_image": "MOSS-Image"}.get(directory(checkpoint))
    if selected_family:
        compatible = family == selected_family if family != "未知" else None
    title = metadata.get("modelspec.title") or metadata.get("ss_output_name", "")
    trained = metadata.get("ss_sd_model_name", "未记录")
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
        "network": metadata.get("ss_network_module", "未知"),
        "cross_attention_dimensions": dimensions,
        "note": note,
        "size": path.stat().st_size,
        "tested_load": False,
    }


@lru_cache(maxsize=4096)
def _cached_inspection(path, checkpoint, size, mtime_ns, ctime_ns):
    # File identity invalidates entries when downloads or replacements finish.
    return inspect_lora(path, checkpoint=checkpoint)


def inspect_local(checkpoint=None):
    from .lora_names import identities, search_identity
    roster = identities()
    results = {}
    tested_path = DATA / "lora-load-tests.json"
    tests = e.read(tested_path) if tested_path.exists() else {}
    configured = e.read(ROOT / "config/comfyui.json")["checkpoint"]
    selected = checkpoint or configured
    from .lora_folders import directory
    scan_root = ROOT / "models/loras"
    if checkpoint and (scan_root / directory(checkpoint)).is_dir():
        scan_root = scan_root / directory(checkpoint)
    for p in scan_root.rglob("*.safetensors"):
        try:
            stat = p.stat()
            downloading = any(Path(str(p) + suffix).exists() for suffix in ('.aria2', '.aria2__temp'))
            if downloading:
                result = inspect_lora(p, checkpoint=selected)
            else:
                result = copy.deepcopy(_cached_inspection(p, selected, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
        except Exception as exc:
            result = {
                "structurally_compatible": None,
                "note": str(exc),
                "family": "无法读取",
            }
        name = p.relative_to(ROOT / "models/loras").as_posix()
        result.update(search_identity(name, roster))
        test = tests.get(name, {})
        if (
            test.get("status") == "passed"
            and test.get("file_size") == p.stat().st_size
            and test.get("mtime_ns") == p.stat().st_mtime_ns
            and test.get("checkpoint")
            == selected
        ):
            result["tested_load"] = True
            result["note"] += "；已在当前底模真实加载出图"
        results[name] = result
    return results
