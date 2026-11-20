"""Build the pinned author's joint SDXL regional workflow."""
import json
import re
import uuid
import subprocess

from . import engine as e
from .paths import ROOT

from . import regional_lora, regional_prompt


def install():
    plugin = ROOT/'runtime/custom_nodes/bv_nodepack'
    revision = subprocess.check_output(['git', '-C', str(plugin), 'rev-parse', 'HEAD'], text=True).strip()
    e.need(revision == 'dc7af1bec7461b70ae1e100f3c02e5cff2169869', '多人绘图组件版本与验收版本不符')
    reference = ROOT/'runtime/custom_nodes/ComfyUI_IPAdapter_plus/CrossAttentionPatch.py'
    e.need('base_attention = getattr(self, "base_attention", None)' in reference.read_text(encoding='utf-8'),
           '参考图与区域文字控制的连接修复未安装')


def apply(graph, passes, cfg, negative, add_triggers=True, characters=None, global_loras=None):
    owned = {role['name']: role for role in passes}
    roles = characters or [dict(name=r['name'], prompt=r['positive'], region=r['region']) for r in passes]
    regional_prompt.validate(roles)
    scene = graph['2']['inputs']['text']
    for role in passes:
        scene = scene.replace(role['positive'], '')
    document_id = str(uuid.uuid4())
    document = {
        'schema': 'bv.regional', 'version': 2, 'document_id': document_id,
        'title': '工作室多人绘图', 'canvas': {'width': cfg['width'], 'height': cfg['height']},
        'negative_mode': 'auto', 'overlap': {'mode': 'joint'},
        'prompts': {'global': {'positive_source': scene, 'negative_source': graph['3']['inputs']['text']},
                    'background': {'positive_source': '', 'negative_source': ''}},
        'regions': [],
    }
    resources = {'version': 3, 'entries': [], 'steps': []}
    # The author combines global and regional resources in the same hook
    # groups. A static global patch plus conditional patches retains another
    # full checkpoint backup on CUDA and can exceed the GPU's headroom.
    for index, lora in enumerate(global_loras or []):
        resources['entries'].append({
            'id': str(uuid.uuid4()),
            'source': {'kind': 'native', 'lora_name': lora['file'],
                       'model_strength': lora['weight'], 'clip_strength': lora['clip_weight']},
            'targets': [{'scope': 'global'}],
        })
        graph.pop(str(8+index), None)
    if global_loras:
        graph['2']['inputs']['clip'] = ['1', 1]
        graph['3']['inputs']['clip'] = ['1', 1]
        graph['5']['inputs']['model'] = ['1', 0]
    camera = regional_prompt.camera_context(scene)
    for index, role in enumerate(roles):
        region_id = str(uuid.uuid4())
        x1, y1, x2, y2 = role['region']
        local = owned.get(role['name'])
        text = role['prompt'] + (', ' + camera if camera else '')
        if add_triggers and local:
            text = ', '.join([lora.get('trigger_words', '') for lora in local['loras']] + [text])
        document['regions'].append({
            'id': region_id, 'name': role['name'], 'enabled': True, 'usage': 'generation',
            'strength': 1.0, 'priority': index,
            'prompts': {'positive_source': text, 'negative_source': graph['3']['inputs']['text']},
            'mask': {'feather': float(cfg.get('regional_mask_feather', .015))},
            'geometry': [{'id': str(uuid.uuid4()), 'type': 'rect', 'operation': 'add',
                          'x': x1, 'y': y1, 'width': x2-x1, 'height': y2-y1}],
        })
        for lora in local['loras'] if local else []:
            resources['entries'].append({
                'id': str(uuid.uuid4()),
                'source': {'kind': 'native', 'lora_name': lora['file'],
                           'model_strength': lora['weight'], 'clip_strength': lora['clip_weight']},
                'targets': [{'scope': 'region', 'document_id': document_id, 'region_id': region_id}],
            })
    graph['270'] = {'class_type': 'BV Regional Prompt', 'inputs': {
        'regional_json': json.dumps(document, ensure_ascii=False),
        'lora_v3_config_json': json.dumps(resources, ensure_ascii=False)}}
    graph['299'] = {'class_type': 'LocalDrawGpuClip', 'inputs': {'clip': graph['2']['inputs']['clip']}}
    graph['271'] = {'class_type': 'BV Regional SDXL Attention', 'inputs': {
        'model': graph['5']['inputs']['model'], 'clip': ['299', 0], 'regional': ['270', 0],
        'attention_strength': 1.0, 'start_percent': 0.0, 'end_percent': 1.0}}
    graph['290'] = {'class_type': 'LocalDrawGpuHooks', 'inputs': {'model': ['271', 0]}}
    graph['5']['inputs'].update(model=['290', 0], positive=['271', 1], negative=['271', 2])
    # A standing template only applies to an explicitly requested full-body
    # standing scene. The caller disables this when a user guide exists.
    combined_text = scene + ', ' + ', '.join(role['prompt'] for role in roles)
    if (cfg.get('regional_auto_pose') and re.search(r'\bfull body\b', scene, re.I)
            and re.search(r'\bstanding\b', scene, re.I)
            and not re.search(r'\b(sitting|seated|kneeling|crouching|running|jumping)\b', combined_text, re.I)):
        regional_lora.apply_pose(graph, regional_lora.standing_pose(roles, cfg), cfg)
        graph['280']['inputs'].pop('areas', None)
    graph['271']['_meta'] = {'title': '日漫多人共同画面', 'characters': [r['name'] for r in roles]}
