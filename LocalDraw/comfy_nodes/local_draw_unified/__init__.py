import logging

from aiohttp import web
import comfy.samplers
import nodes
from server import PromptServer

from .catalog import AUTO, DEFAULTS, NONE, catalog, lora_family_for_model, loras, models


def required_node(name):
    node = nodes.NODE_CLASS_MAPPINGS.get(name)
    if node is None:
        raise RuntimeError(f"缺少节点 {name}，请安装对应扩展后重启 ComfyUI。")
    return node()


@PromptServer.instance.routes.get("/local_draw_unified/catalog")
async def get_catalog(request):
    return web.json_response(catalog())


class UnifiedModel:
    CATEGORY = "本地绘图 / 统一文生图"
    FUNCTION = "load"
    RETURN_TYPES = ("LOCAL_DRAW_PIPELINE",)
    RETURN_NAMES = ("绘图配置",)
    DESCRIPTION = "切换当前已适配的本地模型，自动匹配编码器。LoRA 列表按架构筛选；触发词请写进提示词。"

    @classmethod
    def INPUT_TYPES(cls):
        choices = [NONE, *loras()]
        fields = {"model_name": (list(models()), {"tooltip": "选择底模，编码器和 VAE 会自动匹配。"})}
        for i in range(1, 4):
            fields[f"lora_{i}"] = (choices, {"default": NONE, "tooltip": "可留空；仅显示已识别为同架构的 LoRA。"})
            fields[f"strength_{i}"] = ("FLOAT", {"default": 0.7, "min": -2.0, "max": 2.0, "step": 0.05,
                                                       "tooltip": "0 表示关闭，通常可从 0.6–0.8 开始。"})
        return {"required": fields}

    def load(self, model_name, lora_1, strength_1, lora_2, strength_2, lora_3, strength_3):
        profile = models().get(model_name)
        if profile is None:
            raise ValueError("模型未找到，请刷新列表后重新选择。")
        family = profile["family"]
        available_loras = loras()
        active = [(name, strength) for name, strength in (
            (lora_1, strength_1), (lora_2, strength_2), (lora_3, strength_3)
        ) if name != NONE and strength != 0]
        for name, strength in active:
            actual = available_loras.get(name, "unknown")
            if actual != lora_family_for_model(family):
                raise ValueError(f"LoRA 与当前模型不匹配或架构未识别：{name}。请改选该模型的 LoRA，或选择“不使用 LoRA”。")
        qwen35 = None
        if family == "sdxl":
            model, clip, vae = nodes.CheckpointLoaderSimple().load_checkpoint(profile["file"])
        else:
            if family == "anima38":
                model = required_node("Anima38BV2Loader").load_model(profile["file"])[0]
                qwen35 = required_node("AnimaQwen35Loader").load_clip("qwen35_4b.safetensors")[0]
            else:
                model = nodes.UNETLoader().load_unet(profile["file"], "default")[0]
            clip_name = "qwen3vl_4b_fp8_scaled.safetensors" if family == "krea2" else "qwen_3_06b_base.safetensors"
            clip_type = "krea2" if family == "krea2" else "stable_diffusion"
            clip = nodes.CLIPLoader().load_clip(clip_name, clip_type, "default")[0]
            vae = nodes.VAELoader().load_vae("qwen_image_vae.safetensors")[0]
        for name, strength in active:
            if family in ("anima38", "delta"):
                model, clip, report = required_node("DeltaMixQ35LoraLoader").apply(
                    model, name, strength, skip_gen2=True, skip_watch=True, clip=clip, strength_clip=0.0)
                logging.info(report)
            else:
                model, clip = nodes.LoraLoader().load_lora(model, clip, name, strength,
                                                         strength if family == "sdxl" else 0.0)
        return ({"model": model, "clip": clip, "vae": vae, "qwen35": qwen35,
                 "family": family, "label": model_name, "loras": active},)


class UnifiedGenerate:
    CATEGORY = "本地绘图 / 统一文生图"
    FUNCTION = "generate"
    RETURN_TYPES = ("IMAGE", "STRING")
    RETURN_NAMES = ("图片", "本次设置",)
    DESCRIPTION = "提示词保持原文。步数和 CFG 填 0 时采用该模型推荐值；也可手动修改。"

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "pipeline": ("LOCAL_DRAW_PIPELINE",),
            "positive": ("STRING", {"multiline": True, "default": "An anime illustration of an adult female traveler with silver hair, wearing a blue cloak and a white dress. She stands on a stone bridge in a mountain village, warm morning sunlight, detailed background."}),
            "negative": ("STRING", {"multiline": True, "default": "low quality, blurry, jpeg artifacts, watermark"}),
            "width": ("INT", {"default": 832, "min": 64, "max": 8192, "step": 16}),
            "height": ("INT", {"default": 1216, "min": 64, "max": 8192, "step": 16}),
            "batch_size": ("INT", {"default": 1, "min": 1, "max": 64}),
            "seed": ("INT", {"default": 20261016, "min": 0, "max": 0xffffffffffffffff, "control_after_generate": True}),
            "steps": ("INT", {"default": 0, "min": 0, "max": 200, "tooltip": "0=按模型推荐；例如 Krea2 Turbo 8 步，Anima 3.8B 40 步。"}),
            "cfg": ("FLOAT", {"default": 0, "min": 0, "max": 30, "step": 0.5, "tooltip": "0=按模型推荐；Krea2 Turbo 推荐 1。"}),
            "sampler": ([AUTO, *comfy.samplers.KSampler.SAMPLERS],),
            "scheduler": ([AUTO, *comfy.samplers.KSampler.SCHEDULERS],),
        }}

    def generate(self, pipeline, positive, negative, width, height, batch_size, seed, steps, cfg, sampler, scheduler):
        family = pipeline["family"]
        defaults = DEFAULTS[family]
        steps, cfg = steps or defaults[0], cfg or defaults[1]
        sampler = defaults[2] if sampler == AUTO else sampler
        scheduler = defaults[3] if scheduler == AUTO else scheduler
        model, clip = pipeline["model"], pipeline["clip"]
        if family == "anima38":
            encoder = required_node("Anima38BV2Prompt")
            pos = encoder.encode(model, clip, pipeline["qwen35"], positive)[0]
            neg = encoder.encode(model, clip, pipeline["qwen35"], negative)[0]
        else:
            pos = nodes.CLIPTextEncode().encode(clip, positive)[0]
            neg = (nodes.ConditioningZeroOut().zero_out(pos)[0] if family == "krea2"
                   else nodes.CLIPTextEncode().encode(clip, negative)[0])
        latent = nodes.EmptyLatentImage().generate(width, height, batch_size)[0]
        sampled = nodes.common_ksampler(model, seed, steps, cfg, sampler, scheduler, pos, neg, latent)[0]
        images = nodes.VAEDecode().decode(pipeline["vae"], sampled)[0]
        report = f"模型：{pipeline['label']}\n尺寸：{width} × {height}，{batch_size} 张\n步数：{steps}，CFG：{cfg}\n采样：{sampler} / {scheduler}\n种子：{seed}"
        report += "\nLoRA：" + ("；".join(f"{name}（{weight}）" for name, weight in pipeline["loras"]) or "未使用")
        if family == "krea2":
            report += "\nKrea2 Turbo 使用零负向条件，反向提示词不参与生成。"
        logging.info(report)
        return images, report


class ExpandedControls:
    """Editor controls only. All inference lives in the connected, visible nodes."""
    CATEGORY = "本地绘图 / 完整节点"
    FUNCTION = "configure"
    RETURN_TYPES = ()
    DESCRIPTION = "只同步画布上的设置，不加载模型、不执行采样。下方连线中的节点执行实际生成。"

    @classmethod
    def INPUT_TYPES(cls):
        fields = dict(UnifiedModel.INPUT_TYPES()["required"])
        fields.update({
            "steps": ("INT", {"default": 0, "min": 0, "max": 200, "tooltip": "0 使用模型推荐步数；实际数值显示在 KSampler 中。"}),
            "cfg": ("FLOAT", {"default": 0, "min": 0, "max": 30, "step": 0.5}),
            "width": ("INT", {"default": 832, "min": 64, "max": 8192, "step": 16}),
            "height": ("INT", {"default": 1216, "min": 64, "max": 8192, "step": 16}),
            "batch_size": ("INT", {"default": 1, "min": 1, "max": 64}),
        })
        return {"required": fields}

    def configure(self, **kwargs):
        return ()


NODE_CLASS_MAPPINGS = {"LocalDrawUnifiedModel": UnifiedModel, "LocalDrawUnifiedGenerate": UnifiedGenerate,
                       "LocalDrawExpandedControls": ExpandedControls}
NODE_DISPLAY_NAME_MAPPINGS = {"LocalDrawUnifiedModel": "① 选择模型与 LoRA", "LocalDrawUnifiedGenerate": "② 提示词与出图参数",
                              "LocalDrawExpandedControls": "常用设置 · 同步到完整节点"}
WEB_DIRECTORY = "./web"
