"""Keep native conditioning LoRA hooks within VRAM during whole-image sampling."""


class LocalDrawGpuHooks:
    RETURN_TYPES = ('MODEL',)
    FUNCTION = 'configure'
    CATEGORY = 'local_draw'

    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'model': ('MODEL',)}}

    def configure(self, model):
        import comfy.hooks
        import comfy.model_management
        if comfy.model_management.get_torch_device().type != 'cuda':
            raise RuntimeError('多人绘图必须使用 CUDA，禁止回退到 CPU')
        # Without this, four hooks can cache four copies of the full checkpoint.
        # Rebuild each region's patches on the GPU instead of exceeding VRAM.
        result = model.clone(disable_dynamic=True)
        result.hook_mode = comfy.hooks.EnumHookMode.MinVram
        return (result,)


class LocalDrawGpuClip:
    RETURN_TYPES = ('CLIP',)
    FUNCTION = 'configure'
    CATEGORY = 'local_draw'

    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'clip': ('CLIP',)}}

    def configure(self, clip):
        import comfy.hooks
        import comfy.model_management
        if comfy.model_management.get_torch_device().type != 'cuda':
            raise RuntimeError('角色文本编码必须使用 CUDA，禁止回退到 CPU')
        result=clip.clone(disable_dynamic=True)
        result.patcher.hook_mode=comfy.hooks.EnumHookMode.MinVram
        return (result,)


class LocalDrawRegionalReferenceAttention:
    """Keep BV text routing underneath the author's IPAdapter callbacks."""
    RETURN_TYPES = ('MODEL',)
    FUNCTION = 'combine'
    CATEGORY = 'local_draw'

    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'regional_model': ('MODEL',), 'reference_model': ('MODEL',)}}

    def combine(self, regional_model, reference_model):
        import copy
        import comfy.model_management
        if comfy.model_management.get_torch_device().type != 'cuda':
            raise RuntimeError('区域参考图控制必须使用 CUDA，禁止回退到 CPU')
        regional = regional_model.model_options.get('transformer_options', {}).get('patches_replace', {}).get('attn2', {})
        references = reference_model.model_options.get('transformer_options', {}).get('patches_replace', {}).get('attn2', {})
        result = reference_model.clone()
        combined = 0
        for key, adapter in references.items():
            if type(adapter).__name__ != 'Attn2Replace' or not type(adapter).__module__.endswith('CrossAttentionPatch'):
                continue
            # Current ComfyUI names its SDXL middle block 0; IPAdapter's
            # published table uses 1. Keep its transformer/module indices.
            target_key = ('middle', 0, *key[2:]) if key[:2] == ('middle', 1) else key
            router = regional.get(target_key) or regional.get(target_key[:2])
            if router is None or type(router).__name__ != 'SDXLAttentionRouter':
                raise RuntimeError('参考图注意力层缺少对应的 SDXL 区域文字控制')
            scoped = copy.copy(adapter)
            scoped.multigpu_kwargs = {}
            scoped.base_attention = router
            result.set_model_attn2_replace(scoped, *target_key)
            combined += 1
        if not combined:
            raise RuntimeError('没有可连接的区域参考图注意力层')
        return (result,)


class LocalDrawGpuInpaintEncode:
    """Keep native inpaint preprocessing beside GPU-decoded images.

    Native VAEEncodeForInpaint expects CPU masks, whereas --gpu-only can
    return CUDA images after decoding. Align these inputs on CUDA; the
    caller uses native GrowMask before encoding, so no CPU kernel is needed.
    """
    RETURN_TYPES=('LATENT',)
    FUNCTION='encode'
    CATEGORY='local_draw'

    @classmethod
    def INPUT_TYPES(cls):
        return {'required':{'pixels':('IMAGE',),'mask':('MASK',),'vae':('VAE',)}}

    def encode(self,pixels,mask,vae):
        import comfy.model_management
        import nodes
        device=comfy.model_management.get_torch_device()
        if device.type!='cuda':
            raise RuntimeError('局部重绘必须使用 CUDA，禁止回退到 CPU')
        return nodes.VAEEncodeForInpaint().encode(
            vae,pixels.to(device),mask.to(device),grow_mask_by=0)


class LocalDrawGpuUpscale:
    """Assert CUDA inference while delegating to ComfyUI's native upscaler."""
    RETURN_TYPES = ('IMAGE',)
    FUNCTION = 'upscale'
    CATEGORY = 'local_draw'

    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'upscale_model': ('UPSCALE_MODEL',), 'image': ('IMAGE',)}}

    def upscale(self, upscale_model, image):
        import comfy.model_management
        if comfy.model_management.get_torch_device().type != 'cuda':
            raise RuntimeError('日漫高清放大必须使用 CUDA，禁止回退到 CPU')
        from comfy_extras.nodes_upscale_model import ImageUpscaleWithModel
        calls = []

        def check_device(module, inputs):
            parameter = next(module.parameters())
            if parameter.device.type != 'cuda' or inputs[0].device.type != 'cuda':
                raise RuntimeError('高清模型或输入未在 CUDA，禁止回退到 CPU')
            if not calls:
                print('LocalDraw upscaler inference device: ' + str(parameter.device), flush=True)
            calls.append(True)

        handle = upscale_model.model.register_forward_pre_hook(check_device)
        try:
            result = ImageUpscaleWithModel.upscale(upscale_model, image)
            if not calls:
                raise RuntimeError('高清模型未执行 GPU 推理')
            return tuple(result.result)
        finally:
            handle.remove()


class LocalDrawGpuFooocusGuard:
    """Assert the author's inpaint head executes on CUDA without changing it."""
    RETURN_TYPES = ('INPAINT_PATCH',)
    FUNCTION = 'configure'
    CATEGORY = 'local_draw'

    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'patch': ('INPAINT_PATCH',)}}

    def configure(self, patch):
        import comfy.model_management
        if comfy.model_management.get_torch_device().type != 'cuda':
            raise RuntimeError('局部重绘模型必须使用 CUDA，禁止回退到 CPU')
        head, weights = patch

        class CheckedHead:
            def to(self, *args, **kwargs):
                head.to(*args, **kwargs)
                return self

            def __call__(self, pixels):
                if any(p.device.type != 'cuda' for p in head.parameters()) or pixels.device.type != 'cuda':
                    raise RuntimeError('局部重绘模型或输入未在 CUDA，禁止回退到 CPU')
                print('LocalDraw Fooocus inpaint head inference device: ' + str(pixels.device), flush=True)
                return head(pixels)

        return ((CheckedHead(), weights),)


class LocalDrawPublishedPoseRender:
    """Use the pinned ControlNet preprocessor's unchanged pose rasterizer.

    JSON decoding and line drawing are CPU geometry; no detector or neural
    inference is called here. All downstream model inference remains CUDA.
    """
    RETURN_TYPES = ('IMAGE',)
    FUNCTION = 'render'
    CATEGORY = 'local_draw'

    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'pose_json': ('STRING', {'multiline': True}),
                             'render_body': ('BOOLEAN', {'default': True}),
                             'render_hand': ('BOOLEAN', {'default': True}),
                             'render_face': ('BOOLEAN', {'default': True})}}

    def render(self, pose_json, render_body, render_hand, render_face):
        import json
        import sys
        import subprocess
        from pathlib import Path
        import torch
        root = Path(__file__).resolve().parents[3]
        source = root / 'data/published-solutions/comfyui-controlnet-aux'
        revision = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'],
                                           text=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).strip()
        if revision != '83463c2e4b04e729268e57f638b4212e0da4badc':
            raise RuntimeError('姿势渲染器作者版本未核对')
        sys.path.insert(0, str(source/'src'))
        import custom_controlnet_aux.dwpose as published
        if not Path(published.__file__).resolve().is_relative_to(source.resolve()):
            raise RuntimeError('姿势渲染器来源不匹配')
        poses, _, height, width = published.decode_json_as_poses(json.loads(pose_json))
        pixels = published.draw_poses(poses, height, width, render_body, render_hand, render_face)
        return (torch.from_numpy(pixels.astype('float32')/255.).unsqueeze(0),)


NODE_CLASS_MAPPINGS = {'LocalDrawGpuHooks': LocalDrawGpuHooks, 'LocalDrawGpuClip':LocalDrawGpuClip,
                      'LocalDrawPublishedPoseRender':LocalDrawPublishedPoseRender,
                      'LocalDrawGpuFooocusGuard':LocalDrawGpuFooocusGuard,
                      'LocalDrawGpuUpscale':LocalDrawGpuUpscale,
                      'LocalDrawGpuInpaintEncode':LocalDrawGpuInpaintEncode,
                      'LocalDrawRegionalReferenceAttention':LocalDrawRegionalReferenceAttention}
NODE_DISPLAY_NAME_MAPPINGS = {'LocalDrawGpuHooks': '多人画面统一生成（GPU）'}
