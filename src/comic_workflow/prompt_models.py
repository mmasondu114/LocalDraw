"""Local diffusion model profiles used by the studio's prompt renderer."""
from .paths import ROOT

MODELS = {
    "qwen_image_edit_2511_fp8mixed.safetensors": ("Qwen Image Edit 2511 · 多图参考试验", "qwen_edit", 40, 4.0, "euler", "simple"),
    "Anima-3.8B-v1.1.safetensors": ("Anima 3.8B v1.1", "anima38", 40, 6.0, "res_multistep", "beta"),
    "anima-aesthetic-v1.1.safetensors": ("Anima Aesthetic v1.1", "anima", 30, 4.0, "er_sde", "simple"),
    "animaDeltaMix38BQwen0_deltaMixV60.safetensors": ("Anima Delta Mix v6", "delta", 35, 4.5, "dpmpp_2m", "sgm_uniform"),
    "krea2_turbo_fp8_scaled.safetensors": ("Krea2 Turbo · FP8", "krea2", 8, 1.0, "euler", "simple"),
    "krea2_turbo_bf16.safetensors": ("Krea2 Turbo · BF16 高精度", "krea2", 8, 1.0, "euler", "simple"),
    "moodyKrea2Mix_v3_Global_BF16_abilirated.safetensors": ("Moody Krea 2 Mix v3 Global BF16 (abilirated)", "krea2", 8, 1.0, "euler", "simple"),
    "moss_image_sft_bf16.safetensors": ("MOSS-Image SFT · BF16 高精度", "moss_image", 25, 2.5, "er_sde", "simple"),
}


def installed():
    from .qwen_edit import CHECKPOINT, ASSETS
    return [name for name in MODELS if (ROOT / "models/diffusion_models" / name).is_file()
            and (name != CHECKPOINT or all((ROOT / 'models' / asset).is_file() for asset in ASSETS))]


def defaults(checkpoint):
    p = MODELS.get(checkpoint)
    if p:
        return dict(steps=p[2], cfg=p[3], sampler_name=p[4], scheduler=p[5])
    return dict(steps=28, cfg=5.0, sampler_name="euler_ancestral", scheduler="normal")


def graph(checkpoint, positive, negative, seed, cfg, loras=None):
    from .delta_style import apply as apply_delta_style
    positive, negative = apply_delta_style(checkpoint, positive, negative, cfg)
    family = MODELS[checkpoint][1]
    if family == "qwen_edit":
        from .qwen_edit import graph as edit_graph
        return edit_graph(checkpoint, positive, negative, seed, cfg, loras)
    if family == "anima38":
        from .anima import graph as anima_graph
        return anima_graph(checkpoint, positive, negative, seed, cfg, loras)
    krea = family == "krea2"
    moss = family == "moss_image"
    clip_name = ("qwen3vl_4b_bf16.safetensors" if checkpoint in ("krea2_turbo_bf16.safetensors",
                         "moodyKrea2Mix_v3_Global_BF16_abilirated.safetensors")
                 else "qwen3vl_4b_fp8_scaled.safetensors" if krea
                 else "qwen3.5_9b_bf16.safetensors" if moss else "qwen_3_06b_base.safetensors")
    if krea:
        encoder = (ROOT / "models") / "text_encoders" / "qwen3vl_4b_abliterated_bf16.safetensors"
        if encoder.is_file():
            clip_name = encoder.name
    nodes = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": checkpoint, "weight_dtype": "default"}},
        "10": {"class_type": "CLIPLoader", "inputs": {
            "clip_name": clip_name,
            "type": family if krea or moss else "stable_diffusion", "device": "default"}},
        "12": {"class_type": "VAELoader", "inputs": {"vae_name": "flux2-vae.safetensors" if moss else "qwen_image_vae.safetensors"}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["10", 0], "text": positive}},
        "3": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["2", 0]}} if krea else
             {"class_type": "CLIPTextEncode", "inputs": {"clip": ["10", 0], "text": negative}},
        "4": {"class_type": "EmptyFlux2LatentImage" if moss else "EmptySD3LatentImage", "inputs": {"width": cfg["width"], "height": cfg["height"], "batch_size": 1}},
        "5": {"class_type": "KSampler", "inputs": {
            "model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0], "latent_image": ["4", 0],
            "seed": seed, "steps": cfg["steps"], "cfg": cfg["cfg"], "sampler_name": cfg["sampler_name"],
            "scheduler": cfg["scheduler"], "denoise": 1.0}},
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["12", 0]}},
        "7": {"class_type": "SaveImage", "inputs": {"images": ["6", 0], "filename_prefix": "comic-studio/prompt"}},
    }
    model, clip = ["1", 0], ["10", 0]
    for i, lora in enumerate(loras or [], 20):
        key = str(i)
        inputs = {"model": model, "clip": clip, "lora_name": lora["file"],
                  "strength_model": lora["weight"], "strength_clip": 0.0}
        if family == "delta":
            inputs.update(skip_gen2=True, skip_watch=True)
        nodes[key] = {"class_type": "DeltaMixQ35LoraLoader" if family == "delta" else "LoraLoader", "inputs": inputs}
        model, clip = [key, 0], [key, 1]
    nodes["2"]["inputs"]["clip"] = clip
    if not krea:
        nodes["3"]["inputs"]["clip"] = clip
    nodes["5"]["inputs"]["model"] = model
    return nodes
