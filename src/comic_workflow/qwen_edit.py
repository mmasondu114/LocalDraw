"""Native Qwen editing with explicit, ordered, immutable image references.

Based on Comfy-Org's image_qwen_image_edit_2511 template (non-Lightning).
Reference conditioning is not a visual acceptance decision.
"""
import copy
import hashlib
from functools import lru_cache
from . import engine as e
from .paths import DATA, ROOT

CHECKPOINT = 'qwen_image_edit_2511_fp8mixed.safetensors'
ENCODER = 'qwen_2.5_vl_7b_fp8_scaled.safetensors'
VAE = 'qwen_image_vae.safetensors'
ROLES = {'identity', 'scene', 'prop', 'style', 'composition'}
RECIPE = {'version': 'qwen-edit-2511-native-v1', 'steps': 40, 'cfg': 4.0,
          'sampler_name': 'euler', 'scheduler': 'simple', 'shift': 3.1, 'cfg_norm': 1.0}
ASSETS = {
    'diffusion_models/' + CHECKPOINT: 'c9fdc158e46d3b61ef75f21ae866ca2fe808bf4a53643120d1c1e87c19280a4e',
    'text_encoders/' + ENCODER: 'cb5636d852a0ea6a9075ab1bef496c0db7aef13c02350571e388aea959c5c0b4',
    'vae/' + VAE: 'a70580f0213e67967ee9c95f05bb400e8fb08307e017a924bf3441223e023d1f',
}


@lru_cache(maxsize=12)
def _asset_hash(path, size, mtime_ns, ctime_ns):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def freeze_assets():
    rows = []
    for name, expected in ASSETS.items():
        path = ROOT / 'models' / name
        e.need(path.is_file(), 'Qwen 编辑模型文件尚未安装完整：' + name)
        stat = path.stat()
        actual = _asset_hash(str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        e.need(actual == expected, 'Qwen 编辑模型文件校验不符：' + name)
        rows.append({'file': name, 'sha256': actual, 'size': stat.st_size})
    return rows


def freeze_references(repo, pid, panel, selections):
    from .anima_consistency import freeze_reference
    e.need(isinstance(selections, list) and 1 <= len(selections) <= 3,
           'Qwen 编辑须明确选择 1–3 张参考图，顺序对应提示词中的 image 1–3')
    cast = {c['character_id'] for c in panel.get('characters', [])}
    frozen, seen = [], set()
    for item in selections:
        item = {'image_id': item} if isinstance(item, str) else item
        e.need(isinstance(item, dict), '参考图条目无效')
        key, role = item.get('image_id'), item.get('role', 'composition')
        e.need(isinstance(key, str) and key and key not in seen, '参考图编号为空或重复')
        e.need(role in ROLES, '参考图用途无效')
        character = item.get('character_id', '')
        e.need(isinstance(character, str) and (not character or role == 'identity' and character in cast),
               '人物参考必须属于本格出镜角色')
        seen.add(key)
        frozen.append({**freeze_reference(repo, pid, key), 'role': role, 'character_id': character})
    return frozen


def validate_frozen(references):
    e.need(isinstance(references, list) and 1 <= len(references) <= 3, '缺少 Qwen 固定参考图')
    root = (DATA / 'comfy-input' / 'consistency').resolve()
    seen = set()
    for ref in references:
        e.need(isinstance(ref, dict) and isinstance(ref.get('reference'), str), '固定参考记录无效')
        path = (DATA / 'comfy-input' / ref['reference']).resolve()
        e.need(path.is_relative_to(root) and path.is_file(), '固定参考图不存在或越界')
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        e.need(digest == ref.get('reference_sha256'), '固定参考图已变化，不能复用旧任务')
        e.need(digest not in seen, '参考图内容重复')
        seen.add(digest)
    return copy.deepcopy(references)


def graph(checkpoint, positive, negative, seed, cfg, loras=None):
    e.need(checkpoint == CHECKPOINT, '不支持的 Qwen 编辑模型')
    e.need(not loras, 'Qwen 编辑试验不加载其他模型的角色或画风 LoRA')
    refs = validate_frozen(cfg.get('edit_references'))
    ratio = refs[0]['reference_width'] / refs[0]['reference_height']
    e.need(abs(cfg['width'] / cfg['height'] / ratio - 1) < 0.04,
           'Qwen 编辑当前须选择与第一张参考图相同的画格比例，避免拉伸人物')
    nodes = {
        '1': {'class_type': 'UNETLoader', 'inputs': {'unet_name': checkpoint, 'weight_dtype': 'default'}},
        '10': {'class_type': 'CLIPLoader', 'inputs': {'clip_name': ENCODER, 'type': 'qwen_image', 'device': 'default'}},
        '12': {'class_type': 'VAELoader', 'inputs': {'vae_name': VAE}},
        'edit-shift': {'class_type': 'ModelSamplingAuraFlow', 'inputs': {'model': ['1', 0], 'shift': 3.1}},
        'edit-norm': {'class_type': 'CFGNorm', 'inputs': {'model': ['edit-shift', 0], 'strength': 1.0}},
        '2': {'class_type': 'TextEncodeQwenImageEditPlus', 'inputs': {'clip': ['10', 0], 'vae': ['12', 0], 'prompt': positive}},
        '3': {'class_type': 'TextEncodeQwenImageEditPlus', 'inputs': {'clip': ['10', 0], 'vae': ['12', 0], 'prompt': negative}},
        'edit-positive': {'class_type': 'FluxKontextMultiReferenceLatentMethod', 'inputs': {'conditioning': ['2', 0], 'reference_latents_method': 'index_timestep_zero'}},
        'edit-negative': {'class_type': 'FluxKontextMultiReferenceLatentMethod', 'inputs': {'conditioning': ['3', 0], 'reference_latents_method': 'index_timestep_zero'}},
        '4': {'class_type': 'VAEEncode', 'inputs': {'pixels': ['edit-base-scale', 0], 'vae': ['12', 0]}},
        '5': {'class_type': 'KSampler', 'inputs': {
            'model': ['edit-norm', 0], 'positive': ['edit-positive', 0], 'negative': ['edit-negative', 0],
            'latent_image': ['4', 0], 'seed': seed, 'steps': cfg['steps'], 'cfg': cfg['cfg'],
            'sampler_name': cfg['sampler_name'], 'scheduler': cfg['scheduler'], 'denoise': 1.0}},
        '6': {'class_type': 'VAEDecode', 'inputs': {'samples': ['5', 0], 'vae': ['12', 0]}},
        '7': {'class_type': 'SaveImage', 'inputs': {'images': ['6', 0], 'filename_prefix': 'comic-studio/qwen-edit'}},
    }
    for n, ref in enumerate(refs, 1):
        key = f'edit-image-{n}'
        nodes[key] = {'class_type': 'LoadImage', 'inputs': {'image': ref['reference']}}
        pixels = [key, 0]
        if n == 1:
            # As in the official template, first image determines output framing.
            # Requested size is explicit; additional references retain their aspect.
            nodes['edit-base-scale'] = {'class_type': 'ImageScale', 'inputs': {
                'image': pixels, 'upscale_method': 'lanczos', 'width': cfg['width'],
                'height': cfg['height'], 'crop': 'disabled'}}
            pixels = ['edit-base-scale', 0]
        for branch in ('2', '3'):
            nodes[branch]['inputs'][f'image{n}'] = pixels
    return nodes
