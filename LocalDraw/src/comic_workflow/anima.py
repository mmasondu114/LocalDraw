"""Anima 3.8B v1.1 graph, separate from the SDXL checkpoint pipeline."""
from pathlib import PurePosixPath
import re

CHECKPOINT = "Anima-3.8B-v1.1.safetensors"

# A narrowly matched action, never inferred from a character's name or feet tags.
KICK_LAYOUT = (
    "Full-body action composition, the entire reclining figure fits inside the canvas. "
    "She lies back on the ground, with her shoulders close to the ground and her torso "
    "nearly horizontal across the frame, supported by one forearm. Her head and hips "
    "form a low diagonal rather than an upright seated silhouette. One thigh is raised, "
    "the knee stays visibly bent, and the shin angles forward into a short kick. "
    "Show a continuous thigh, bent knee, shin, ankle and foot, with space between the two legs. "
    "The kicking foot is prominent in the lower foreground, completely visible from heel "
    "to toes, with clear empty space between the foot and every image edge. "
    "The ankle and bent knee remain visible behind the foot. Moderate foreshortening, "
    "three-quarter view, distinct leg silhouette, foot offset from the face. "
    "The other leg rests naturally along the ground, its foot also inside the frame. "
    "Simple unobtrusive background, crisp contours, "
    "rounded 2.5D anime forms. Mildly annoyed expression."
)


def action_mode_for_prompt(positive):
    """Only the requested reclining, bent-knee kick with a foreground foot."""
    text = positive.casefold()
    if re.search(r"\b(?:no|not|without|avoid)\s+(?:[\w-]+\s+){0,3}(?:kick\w*|reclin\w*)\b|(?:不要|避免|不做).{0,6}(?:踢|躺)", text):
        return "standard"
    patterns = (
        r"\breclin\w*\b|\blying\b|躺|斜倚",
        r"\bkick\w*\b|踢",
        r"\bbent[ -]knee\b|\bknee\b[^.\n]{0,35}\bbent\b|弯膝|屈膝",
        r"\b(?:foot|feet)\b|脚|足",
        r"\bforeground\b|\btoward(?:s)? (?:the )?(?:camera|viewer)\b|前景|朝向镜头|踢向镜头",
        r"2\.5\s*d",
        r"\bannoyed\b|微恼|不悦|微怒",
    )
    return "reclining_kick" if all(re.search(pattern, text) for pattern in patterns) else "standard"


def action_conditioning(nodes, positive):
    """Use layout conditioning early, then continue the SAME sampling trajectory.

    The first paragraph supplies identity/costume, while later scene ornamentation
    waits until the detail phase. Both branches use the trained semantic connector
    and the same LoRA model/encoder links. No image editing or SDXL controls.
    """
    identity = positive.strip().split("\n\n", 1)[0]
    layout = KICK_LAYOUT + "\n\nCharacter appearance: " + identity
    nodes["80"] = {"class_type": "Anima38BV2Prompt", "inputs": {
        **nodes["2"]["inputs"], "prompt": layout}}
    nodes["81"] = {"class_type": "ConditioningSetTimestepRange", "inputs": {
        "conditioning": ["80", 0], "start": 0.0, "end": 0.55}}
    nodes["82"] = {"class_type": "ConditioningSetTimestepRange", "inputs": {
        "conditioning": ["2", 0], "start": 0.55, "end": 1.0}}
    nodes["83"] = {"class_type": "ConditioningCombine", "inputs": {
        "conditioning_1": ["81", 0], "conditioning_2": ["82", 0]}}
    nodes["5"]["inputs"]["positive"] = ["83", 0]


def is_anima(checkpoint):
    return PurePosixPath(str(checkpoint).replace("\\", "/")).name == CHECKPOINT


def graph(checkpoint, positive, negative, seed, cfg, loras=None):
    action_mode = "standard" if cfg.get("literal_prompt") else action_mode_for_prompt(positive)
    nodes = {
        "1": {"class_type": "Anima38BV2Loader", "inputs": {"model_name": checkpoint}},
        "10": {"class_type": "CLIPLoader", "inputs": {
            "clip_name": "qwen_3_06b_base.safetensors", "type": "stable_diffusion", "device": "default"}},
        "11": {"class_type": "AnimaQwen35Loader", "inputs": {"qwen35_model": "qwen35_4b.safetensors"}},
        "12": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_vae.safetensors"}},
        "2": {"class_type": "Anima38BV2Prompt", "inputs": {
            "model": ["1", 0], "native_clip": ["10", 0], "qwen35_clip": ["11", 0], "prompt": positive}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["10", 0], "text": negative}},
        "4": {"class_type": "EmptySD3LatentImage", "inputs": {
            "width": cfg["width"], "height": cfg["height"], "batch_size": 1}},
        "5": {"class_type": "KSampler", "inputs": {
            "model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0],
            "latent_image": ["4", 0], "seed": seed, "steps": cfg["steps"], "cfg": cfg["cfg"],
            "sampler_name": cfg["sampler_name"], "scheduler": cfg["scheduler"], "denoise": 1.0}},
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["12", 0]}},
        "7": {"class_type": "SaveImage", "inputs": {
            "images": ["6", 0], "filename_prefix": "comic-studio/anima"}},
    }
    model, clip = ["1", 0], ["10", 0]
    for i, lora in enumerate(loras or [], 20):
        key = str(i)
        nodes[key] = {"class_type": "LoraLoader", "inputs": {
            "model": model, "clip": clip, "lora_name": lora["file"],
            "strength_model": lora["weight"], "strength_clip": lora.get("clip_weight", 0)}}
        model, clip = [key, 0], [key, 1]
    nodes["2"]["inputs"].update(model=model, native_clip=clip)
    nodes["3"]["inputs"]["clip"] = clip
    nodes["5"]["inputs"]["model"] = model
    if action_mode == "reclining_kick":
        action_conditioning(nodes, positive)
    return nodes
