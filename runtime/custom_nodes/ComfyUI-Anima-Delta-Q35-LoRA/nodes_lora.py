from __future__ import annotations

import folder_paths
import comfy.sd
import comfy.utils

from .maps_lora import detect_lora_family, remap_lora_keys


class DeltaMixQ35LoraLoader:
    CATEGORY = "Anima Delta Mix"
    FUNCTION = "apply"
    RETURN_TYPES = ("MODEL", "CLIP", "STRING")
    RETURN_NAMES = ("model", "clip", "log")
    DESCRIPTION = (
        "Load 28/40/52 Anima LoRA onto a 52-block 1.1 / Delta Mix DiT. "
        "Rewrites block indices. Drops Connector, llm_adapter, Gen-2 and watch by default. "
        "CLIP slot is optional 0.6B only."
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model": ("MODEL",),
                "lora_name": (folder_paths.get_filename_list("loras"),),
                "strength_model": (
                    "FLOAT",
                    {"default": 0.65, "min": -2.0, "max": 2.0, "step": 0.01},
                ),
                "skip_gen2": ("BOOLEAN", {"default": True}),
                "skip_watch": ("BOOLEAN", {"default": True}),
            },
            "optional": {
                "clip": ("CLIP",),
                "strength_clip": (
                    "FLOAT",
                    {"default": 0.0, "min": -2.0, "max": 2.0, "step": 0.01},
                ),
            },
        }

    def apply(
        self,
        model,
        lora_name,
        strength_model,
        skip_gen2=True,
        skip_watch=True,
        clip=None,
        strength_clip=0.0,
    ):
        path = folder_paths.get_full_path("loras", lora_name)
        if not path:
            raise FileNotFoundError(lora_name)
        raw = comfy.utils.load_torch_file(path, safe_load=True)
        family = detect_lora_family(raw.keys())
        remapped, stats = remap_lora_keys(
            raw,
            family,
            skip_gen2=bool(skip_gen2),
            skip_watch=bool(skip_watch),
        )
        log = (
            f"DeltaMix Q35 LoRA\n"
            f"file={lora_name}\n"
            f"family={family} skip_gen2={bool(skip_gen2)} skip_watch={bool(skip_watch)}\n"
            f"mapped={stats['mapped']} kept={stats['kept']} "
            f"dropped_gen2={stats['dropped_gen2']} dropped_watch={stats['dropped_watch']} "
            f"dropped_unknown={stats['dropped_unknown']} "
            f"dropped_interface={stats['dropped_interface']} non_dit={stats['non_dit']}\n"
        )
        model_out, clip_out = comfy.sd.load_lora_for_models(
            model,
            clip,
            remapped,
            float(strength_model),
            float(strength_clip) if clip is not None else 0.0,
        )
        if clip is None:
            clip_out = clip
        print(log.strip())
        return (model_out, clip_out, log)
