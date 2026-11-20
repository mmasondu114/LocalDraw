"""WAI's documented whole-canvas hires fix using native ComfyUI nodes."""
import copy
import hashlib

from .installed_assets import read_path
from . import engine as e
from .paths import ROOT


def pose_conditioning(graph, roots):
    """Copy the joint pose conditioning for the second sampling pass only.

    Low denoise starts in the tail of ComfyUI's full sigma schedule. Reusing a
    pose ending at 60% leaves a 35%-denoise hires pass completely uncontrolled.
    Preserve the first pass; its independent second-pass controls end at 100%.
    Native ControlNet resizes the same whole-canvas hint for the larger latent.
    """
    replacements, additions, active = {}, {}, set()

    def node(key):
        if key in replacements:
            return replacements[key]
        e.need(key not in active, '高清姿势条件图中存在循环')
        active.add(key)
        original = graph[key]
        changed = original['class_type'] == 'ControlNetApplyAdvanced'

        def value(item):
            nonlocal changed
            if isinstance(item, list) and len(item) == 2 and isinstance(item[0], str) \
                    and type(item[1]) is int and item[0] in graph:
                parent = node(item[0])
                changed |= parent != item[0]
                return [parent, item[1]]
            if isinstance(item, dict):
                return {k: value(v) for k, v in item.items()}
            if isinstance(item, list):
                return [value(v) for v in item]
            return item

        inputs = value(original['inputs'])
        if original['class_type'] == 'ControlNetApplyAdvanced':
            inputs['end_percent'] = 1.
        result = key
        if changed:
            result = 'hires-pose-' + key
            e.need(result not in graph, '高清姿势条件节点重复')
            additions[result] = {**copy.deepcopy(original), 'inputs': inputs}
        active.remove(key)
        replacements[key] = result
        return result

    updated = [[node(key), output] for key, output in roots]
    graph.update(additions)
    return updated, [key for key, value in additions.items()
                     if value['class_type'] == 'ControlNetApplyAdvanced']


def apply(graph, cfg, seed):
    recipe = e.read(ROOT / 'config/hires-fix.json')
    path = read_path(ROOT, recipe['file'])
    e.need(path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == recipe['sha256'],
           '日漫高清模型未安装完整或校验失败')
    target = [round(cfg[key] * recipe['scale'] / 8) * 8 for key in ('width', 'height')]
    sampler = copy.deepcopy(graph['5'])
    sampler['inputs'].update(latent_image=['903', 0], seed=seed,
                             steps=recipe['steps'], denoise=recipe['denoise'])
    controls = []
    if cfg.get('hires_pose_control', False) and graph.get('290', {}).get('_meta', {}).get('coupled_pose'):
        conditions, controls = pose_conditioning(graph, [sampler['inputs']['positive'], sampler['inputs']['negative']])
        sampler['inputs']['positive'], sampler['inputs']['negative'] = conditions
    graph.update({
        '900': {'class_type': 'UpscaleModelLoader', 'inputs': {'model_name': path.name}},
        '901': {'class_type': 'LocalDrawGpuUpscale', 'inputs': {'upscale_model': ['900', 0], 'image': ['6', 0]}},
        '902': {'class_type': 'ImageScale', 'inputs': {'image': ['901', 0], 'upscale_method': 'lanczos',
                  'width': target[0], 'height': target[1], 'crop': 'disabled'}},
        '903': {'class_type': 'VAEEncode', 'inputs': {'pixels': ['902', 0], 'vae': ['1', 2]}},
        '904': sampler,
        '905': {'class_type': 'VAEDecode', 'inputs': {'samples': ['904', 0], 'vae': ['1', 2]}},
    })
    graph['7']['inputs']['images'] = ['905', 0]
    return {'width': target[0], 'height': target[1], 'scale': recipe['scale'],
            'steps': recipe['steps'], 'denoise': recipe['denoise'], 'source': recipe['source'],
            'hires_pose_controls': controls, 'first_pass_conditioning_preserved': True}
