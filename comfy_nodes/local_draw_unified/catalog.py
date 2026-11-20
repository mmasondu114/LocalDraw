import json
import struct
from functools import lru_cache
from pathlib import Path

import folder_paths

NONE = "不使用 LoRA"
AUTO = "自动（按模型推荐）"

# steps, CFG, sampler, scheduler
DEFAULTS = {
    "anima38": (40, 6.0, "res_multistep", "beta"),
    "anima": (30, 4.0, "er_sde", "simple"),
    "delta": (35, 4.5, "dpmpp_2m", "sgm_uniform"),
    "krea2": (8, 1.0, "euler", "simple"),
    "sdxl": (28, 6.0, "euler_ancestral", "normal"),
}

DIFFUSION = {
    "Anima-3.8B-v1.1.safetensors": ("Anima 3.8B v1.1", "anima38"),
    "anima-aesthetic-v1.1.safetensors": ("Anima Aesthetic v1.1", "anima"),
    "animaDeltaMix38BQwen0_deltaMixV60.safetensors": ("Anima Delta Mix v6", "delta"),
    "krea2_turbo_fp8_scaled.safetensors": ("Krea2 Turbo", "krea2"),
}
CHECKPOINT_NAMES = {
    "animagine-xl-3.1.safetensors": "Animagine XL 3.1",
    "animagine-xl-4.0-opt.safetensors": "Animagine XL 4.0 Opt",
    "Illustrious-XL-v2.0.safetensors": "Illustrious XL v2.0",
    "NoobAI-XL-v1.1.safetensors": "NoobAI XL v1.1",
    "ponyDiffusionV6XL_v6StartWithThisOne.safetensors": "Pony Diffusion V6 XL",
    "waiIllustriousSDXL_v170.safetensors": "WAI Illustrious v17",
}


def models():
    found = {}
    available = set(folder_paths.get_filename_list("diffusion_models"))
    for name, (label, family) in DIFFUSION.items():
        if name in available:
            found[label] = {"file": name, "family": family, "category": "diffusion_models"}
    for name in folder_paths.get_filename_list("checkpoints"):
        if Path(name).name in CHECKPOINT_NAMES:
            label = CHECKPOINT_NAMES[Path(name).name]
            found[label] = {"file": name, "family": "sdxl", "category": "checkpoints"}
    return found


def lora_family_for_model(family):
    return "anima" if family in ("anima", "anima38", "delta") else family


@lru_cache(maxsize=2048)
def inspect_lora(path, size, mtime):
    with open(path, "rb") as stream:
        raw = stream.read(8)
        if len(raw) != 8:
            return "unknown"
        length = struct.unpack("<Q", raw)[0]
        if not 2 <= length <= min(size - 8, 16_000_000):
            return "unknown"
        header = json.loads(stream.read(length))
    metadata = header.pop("__metadata__", {})
    description = " ".join(str(metadata.get(k, "")) for k in (
        "ss_base_model_version", "modelspec.architecture", "ss_network_module", "ss_sd_model_name", "ss_network_args"
    )).lower()
    if "krea2" in description:
        return "krea2"
    if "anima" in description:
        return "anima"
    if "sdxl" in description or "diffusion-xl" in description or "sd_xl" in description:
        return "sdxl"
    dimensions = {v["shape"][1] for k, v in header.items()
                  if "attn2_to_k" in k and "lora_down" in k and len(v.get("shape", [])) == 2}
    if 2048 in dimensions:
        return "sdxl"
    if dimensions == {768}:
        return "sd1"
    return "unknown"


def loras():
    result = {}
    for name in folder_paths.get_filename_list("loras"):
        if not name.endswith(".safetensors"):
            continue
        path = folder_paths.get_full_path_or_raise("loras", name)
        if any(Path(path + suffix).exists() for suffix in (".aria2", ".aria2__temp")):
            continue
        stat = Path(path).stat()
        try:
            family = inspect_lora(path, stat.st_size, stat.st_mtime_ns)
        except (OSError, ValueError, KeyError, TypeError):
            family = "unknown"
        result[name] = family
    return result


def catalog():
    return {"models": models(), "loras": loras(), "defaults": DEFAULTS, "none": NONE}
