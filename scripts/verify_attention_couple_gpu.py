"""Evaluate the published, unmodified A8R8 Attention Couple on SDXL/CUDA.

Its author node handles attention. Native ComfyUI conditioning masks and LoRA
hooks handle model weights; this script only assembles the documented nodes.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from comic_workflow import comfyui, engine as e, regional_lora, reference_images, regional_prompt, lora_binding

PIN = '8d617cea60f2cd9e55f3152be356141d19ad48a2'


def shared_context(scene, cfg):
    context = re.sub(r'\b(?:[1-9]\s*(?:people|girls?|boys?|others?)|solo)\b', '', scene, flags=re.I)
    if cfg.get('single_person_context'):
        # Regional Prompter documents that the common prompt is copied into
        # every region. A group relationship must stay in the base prompt.
        context = re.sub(r'\bside\s+by\s+side\b', '', context, flags=re.I)
    return context


def build(checkpoint, scene, negative, seed, cfg, roles, style, global_weight):
    graph = comfyui.graph(checkpoint, scene, negative, seed, cfg)
    graph['299'] = {'class_type': 'LocalDrawGpuClip', 'inputs': {'clip': ['1', 1]}}
    graph['295'] = {'class_type': 'SolidMask', 'inputs': {'value': 0., 'width': cfg['width'], 'height': cfg['height']}}
    global_hooks = None
    for index, lora in enumerate(style):
        inputs = {'lora_name': lora['file'], 'strength_model': lora['weight'], 'strength_clip': lora['clip_weight']}
        if global_hooks:
            inputs['prev_hooks'] = global_hooks
        key = str(200 + index)
        graph[key] = {'class_type': 'CreateHookLora', 'inputs': inputs}
        global_hooks = [key, 0]
    clip = ['299', 0]
    if global_hooks:
        graph['296'] = {'class_type': 'SetClipHooks', 'inputs': {
            'clip': clip, 'hooks': global_hooks, 'apply_to_conds': True, 'schedule_clip': False}}
        clip = ['296', 0]
    graph['2']['inputs']['clip'] = clip
    graph['3']['inputs']['clip'] = clip
    combined_pos = combined_neg = None
    regions = {}
    context = shared_context(scene, cfg)
    for index, role in enumerate(roles):
        base = 300 + 40 * index
        def node(offset, kind, **inputs):
            key = str(base + offset)
            graph[key] = {'class_type': kind, 'inputs': inputs}
            return [key, 0]
        hooks = global_hooks
        for offset, lora in enumerate(role['loras']):
            inputs = {'lora_name': lora['file'], 'strength_model': lora['weight'], 'strength_clip': lora['clip_weight']}
            if hooks:
                inputs['prev_hooks'] = hooks
            hooks = node(offset, 'CreateHookLora', **inputs)
        owned_clip = clip
        if hooks:
            owned_clip = node(8, 'SetClipHooks', clip=['299', 0], hooks=hooks, apply_to_conds=True, schedule_clip=False)
        positive = node(10, 'CLIPTextEncode', clip=owned_clip, text=role['prompt'] + ', ' + context)
        negative_cond = node(11, 'CLIPTextEncode', clip=owned_clip, text=negative)
        x1, y1, x2, y2 = role['region']
        left, top = round(x1 * cfg['width']), round(y1 * cfg['height'])
        right, bottom = round(x2 * cfg['width']), round(y2 * cfg['height'])
        solid = node(12, 'SolidMask', value=1., width=right-left, height=bottom-top)
        mask = node(13, 'MaskComposite', destination=['295', 0], source=solid, x=left, y=top, operation='add')
        pos = node(14, 'ConditioningSetMask', conditioning=positive, mask=mask, strength=1., set_cond_area='default')
        neg = node(15, 'ConditioningSetMask', conditioning=negative_cond, mask=mask, strength=1., set_cond_area='default')
        regions['region_' + str(index + 1)] = node(16, 'AttentionCoupleRegion', cond=positive, mask=mask, weight=1.)
        combined_pos = node(17, 'ConditioningCombine', conditioning_1=combined_pos, conditioning_2=pos) if combined_pos else pos
        combined_neg = node(18, 'ConditioningCombine', conditioning_1=combined_neg, conditioning_2=neg) if combined_neg else neg
    graph['270'] = {'class_type': 'AttentionCoupleRegions', 'inputs': regions}
    graph['271'] = {'class_type': 'AttentionCouple', 'inputs': {
        'model': ['1', 0], 'base_prompt': ['2', 0], 'global_prompt_weight': global_weight,
        'regions': ['270', 0], 'width': cfg['width'], 'height': cfg['height']}}
    graph['290'] = {'class_type': 'LocalDrawGpuHooks', 'inputs': {'model': ['271', 0]}}
    graph['5']['inputs'].update(model=['290', 0], positive=combined_pos, negative=combined_neg)
    return graph


def author_global_build(checkpoint, scene, negative, seed, cfg, roles, style, global_weight):
    """Use the author's example's empty sampler positive and global model weights."""
    graph = comfyui.graph(checkpoint, '', negative, seed, cfg, style + [lora for role in roles for lora in role['loras']])
    graph['298'] = {'class_type': 'CLIPTextEncode', 'inputs': {'clip': graph['2']['inputs']['clip'], 'text': scene}}
    graph['295'] = {'class_type': 'SolidMask', 'inputs': {'value': 0., 'width': cfg['width'], 'height': cfg['height']}}
    regions = {}
    context = shared_context(scene, cfg)
    for index, role in enumerate(roles):
        base = 300 + index * 40
        x1, y1, x2, y2 = role['region']
        left, top = round(x1 * cfg['width']), round(y1 * cfg['height'])
        right, bottom = round(x2 * cfg['width']), round(y2 * cfg['height'])
        graph[str(base+10)] = {'class_type': 'CLIPTextEncode', 'inputs': {'clip': graph['2']['inputs']['clip'], 'text': role['prompt'] + ', ' + context}}
        graph[str(base+12)] = {'class_type': 'SolidMask', 'inputs': {'value': 1., 'width': right-left, 'height': bottom-top}}
        graph[str(base+13)] = {'class_type': 'MaskComposite', 'inputs': {'destination': ['295', 0], 'source': [str(base+12), 0], 'x': left, 'y': top, 'operation': 'add'}}
        graph[str(base+16)] = {'class_type': 'AttentionCoupleRegion', 'inputs': {'cond': [str(base+10), 0], 'mask': [str(base+13), 0], 'weight': 1.}}
        regions['region_'+str(index+1)] = [str(base+16), 0]
    graph['270'] = {'class_type': 'AttentionCoupleRegions', 'inputs': regions}
    graph['271'] = {'class_type': 'AttentionCouple', 'inputs': {
        'model': graph['5']['inputs']['model'], 'base_prompt': ['298', 0], 'global_prompt_weight': global_weight,
        'regions': ['270', 0], 'width': cfg['width'], 'height': cfg['height']}}
    graph['290'] = {'class_type': 'LocalDrawGpuHooks', 'inputs': {'model': ['271', 0]}}
    graph['5']['inputs']['model'] = ['290', 0]
    return graph


def author_private_clip_build(checkpoint, scene, negative, seed, cfg, roles, style, global_weight):
    """Use published encoder-only hooks, retaining the author's attention math."""
    graph = author_global_build(checkpoint, scene, negative, seed, cfg, roles, style, global_weight)
    owned = {lora['file'] for role in roles for lora in role['loras']}
    for item in graph.values():
        if item['class_type'] == 'LoraLoader' and item['inputs']['lora_name'] in owned:
            item['inputs']['strength_clip'] = 0.
    graph['2']['inputs']['text'] = scene
    graph['299'] = {'class_type': 'LocalDrawGpuClip', 'inputs': {'clip': graph['2']['inputs']['clip']}}
    for index, role in enumerate(roles):
        base = 300 + index * 40
        hooks = None
        for offset, lora in enumerate(role['loras']):
            inputs = {'lora_name': lora['file'], 'strength_model': 0., 'strength_clip': lora['clip_weight']}
            if hooks:
                inputs['prev_hooks'] = hooks
            key = str(base + offset)
            graph[key] = {'class_type': 'CreateHookLora', 'inputs': inputs}
            hooks = [key, 0]
        graph[str(base+8)] = {'class_type': 'SetClipHooks', 'inputs': {
            'clip': ['299', 0], 'hooks': hooks, 'apply_to_conds': False, 'schedule_clip': False}}
        graph[str(base+10)]['inputs']['clip'] = [str(base+8), 0]
    return graph


def per_person_pose(graph, roles, cfg):
    """Bind each native conditioning branch to its own full-canvas pose hint."""
    control = e.read(ROOT / 'config/multi-person-control.json')
    pose = regional_lora.standing_pose(roles, {**cfg, 'regional_pose_margin': .18})
    graph['281'] = {'class_type': 'ControlNetLoader', 'inputs': {'control_net_name': control['name']}}
    for index in range(len(roles)):
        base = 300 + index * 40
        pose_key, control_key = str(base + 20), str(base + 21)
        own_pose = {**pose, 'people': [pose['people'][index]]}
        graph[pose_key] = {'class_type': 'OpenPoseStudio', 'inputs': {
            'pose_json': json.dumps(own_pose), 'render_body': True, 'render_hand': True, 'render_face': True}}
        for item in graph.values():
            for field, value in item['inputs'].items():
                if value == [str(base+14), 0]:
                    item['inputs'][field] = [control_key, 0]
                elif value == [str(base+15), 0]:
                    item['inputs'][field] = [control_key, 1]
        graph[control_key] = {'class_type': 'ControlNetApplyAdvanced', 'inputs': {
            'positive': [str(base+14), 0], 'negative': [str(base+15), 0], 'control_net': ['281', 0],
            'image': [pose_key, 0], 'strength': 1., 'start_percent': 0., 'end_percent': 1.}}


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--counts', nargs='+', type=int, choices=[2, 3, 4], default=[2])
    parser.add_argument('--seeds', nargs='+', type=int, default=[71007, 71008])
    parser.add_argument('--models', nargs='+', choices=['wai', 'noob'], default=['wai'])
    parser.add_argument('--weight', type=float, default=.7)
    parser.add_argument('--style-weight', type=float, default=.35)
    parser.add_argument('--global-weight', type=float, default=.3)
    parser.add_argument('--pose', action='store_true')
    parser.add_argument('--single-person-context', action='store_true')
    parser.add_argument('--per-person-pose', action='store_true')
    parser.add_argument('--candidate-characters', nargs='+', choices=['甘雨', '莫娜'])
    parser.add_argument('--native-attention', action='store_true')
    parser.add_argument('--references', action='store_true')
    parser.add_argument('--reference-weight', type=float, default=.3)
    parser.add_argument('--studio-pipeline', action='store_true')
    parser.add_argument('--binding', choices=['masked_hooks', 'author_global', 'author_global_private_clip'], default='masked_hooks')
    args = parser.parse_args()
    e.need(not args.per_person_pose or args.binding == 'masked_hooks', '逐角色姿势控制仅用于原生区域钩子对照')
    e.need(not args.native_attention or args.binding == 'masked_hooks', '原生注意力对照仅用于原生区域钩子')
    e.need(not args.references or args.native_attention, '作者 Attention Couple 尚未验证参考图回调兼容，禁止直接接入')
    e.need(not args.studio_pipeline or args.native_attention, '工作室对照只接入原生整幅遮罩后端')
    plugin = ROOT / 'runtime/custom_nodes/A8R8_ComfyUI_nodes'
    revision = subprocess.check_output(['git', '-C', str(plugin), 'rev-parse', 'HEAD'], text=True).strip()
    diff = subprocess.check_output(['git', '-C', str(plugin), 'diff'], text=True)
    e.need(revision == PIN and not diff, '作者 Attention Couple 版本发生变化')
    out = ROOT / 'data/test-results/attention-couple' / e.stamp()
    out.mkdir(parents=True)
    e.save(out / 'source-provenance.json', {
        'repository': 'https://github.com/ramyma/A8R8_ComfyUI_nodes', 'revision': revision,
        'local_vendor_changes': diff, 'arguments': vars(args),
        'attention_backend': 'native_comfyui' if args.native_attention else 'pinned_unmodified_attention_couple',
        'primary_workflow': 'https://blog.comfy.org/p/masking-and-scheduling-lora-and-model-weights' if args.native_attention else 'https://github.com/ramyma/A8R8_ComfyUI_nodes',
        'author_attention_code_executed': not args.native_attention,
        'assembly': {
            'masked_hooks': 'Native CUDA masked LoRA hooks',
            'author_global': 'Author example: empty sampler positive, globally loaded LoRAs, regional text/masks',
            'author_global_private_clip': 'Author attention with global UNet weights, per-character encoder-only native LoRA hooks and shared scene pooled conditioning',
        }[args.binding],
        'license_basis': 'Author GPL-3.0 derived attention code retains its credits.',
        'official_references': e.read(ROOT / 'data/lora-curation/research/qa-official-references.json'),
    })
    frozen = out / 'official-references'
    frozen.mkdir()
    for ref in e.read(ROOT / 'data/lora-curation/research/qa-official-references.json'):
        for key in ('file', 'clip_file'):
            source = ROOT / 'data/comfy-input' / ref[key]
            shutil.copyfile(source, frozen / source.name)
    if args.references:
        ip_plugin = ROOT / 'runtime/custom_nodes/ComfyUI_IPAdapter_plus'
        e.save(out / 'reference-source-provenance.json', {
            'repository': 'https://github.com/cubiq/ComfyUI_IPAdapter_plus',
            'revision': subprocess.check_output(['git', '-C', str(ip_plugin), 'rev-parse', 'HEAD'], text=True).strip(),
            'local_patch': subprocess.check_output(['git', '-C', str(ip_plugin), 'diff', '--', 'CrossAttentionPatch.py'], text=True),
            'attention_mode': 'Native attention only; author callback batch semantics unchanged.',
        })
    selected = {r['character']['name']: r['selected'] for r in e.read(ROOT / 'data/lora-curation/selection.json')['characters'] if r['selected']}
    if args.candidate_characters:
        for candidate in e.read(ROOT / 'data/lora-curation/research/official-art-comparison-candidates.json'):
            if candidate['character'] in args.candidate_characters:
                e.need(candidate['evidence'].get('full_file_verified') and candidate['evidence'].get('structurally_compatible'), '候选文件尚未完整核验')
                selected[candidate['character']] = candidate
    e.save(out / 'model-selection.json', {name: selected[name] for name in ('甘雨', '刻晴', '琴', '莫娜')})
    ganyu_trigger = selected['甘雨']['triggers'][0] if args.candidate_characters and '甘雨' in args.candidate_characters else selected['甘雨']['triggers'][0].split(',')[0]
    mona_trigger = selected['莫娜']['triggers'][0].replace('green eyes', 'turquoise eyes') if args.candidate_characters and '莫娜' in args.candidate_characters else selected['莫娜']['triggers'][0].split(',')[0]
    definitions = [
        ('甘雨', ganyu_trigger + ', 1girl, blue hair, long hair, red and black horns, purple eyes, neck bell, bare shoulders, detached white sleeves, black leotard, black pantyhose, white and gold pelvic curtain, chinese knot'),
        ('刻晴', selected['刻晴']['triggers'][0] + ', 1girl'),
        ('琴', selected['琴']['triggers'][1] + ', 1girl'),
        ('莫娜', mona_trigger + ', 1girl, dark purple hair, twintails, turquoise eyes, large blue witch hat, gold star ornaments, star earrings, blue leotard, gold trim, covered navel, black pantyhose, purple detached sleeves, black gloves, blue capelet, black fur collar, red cape lining, black choker, neck bow'),
    ]
    negative = 'worst quality, low quality, bad anatomy, bad hands, extra limbs, extra people, duplicate person, collage, split screen, thick outlines, western comics, text, watermark'
    manager = comfyui.ComfyManager()
    records = []
    try:
        for index, model in enumerate(args.models):
            if index:
                manager.stop()
            manager.start(runtime_options={'attention_couple_trial': not args.native_attention})
            e.save(out / (model + '-gpu-system.json'), comfyui.api('/system_stats'))
            if not args.native_attention:
                info = comfyui.api('/object_info/AttentionCouple')
                e.need('AttentionCouple' in info, '作者 Attention Couple 未载入')
            inventory = comfyui.discover()['lora_files']
            cfg = {**comfyui.config(), 'width': 1024, 'height': 768, 'steps': 28,
                   'cfg': 5.5, 'sampler_name': 'euler_ancestral', 'scheduler': 'normal',
                   'single_person_context': args.single_person_context}
            checkpoint = {'wai': 'waiIllustriousSDXL_v170.safetensors', 'noob': 'NoobAI-XL-v1.1.safetensors'}[model]
            style = []
            if args.style_weight:
                style = [{'file': inventory['原神/动画截图风格_Eufoniuzs_Anime_Screencap_illustrious_3-000010.safetensors'],
                          'weight': args.style_weight, 'clip_weight': args.style_weight}]
            for count in args.counts:
                scene = f'{count}girls, full body, standing side by side, outdoors, park, daylight, Japanese anime, soft cel shading, thin lineart, masterpiece, best quality, anime screencap, delicate thin lineart, gentle lighting, natural colors, detailed anime background'
                roles = [{'name': name, 'prompt': prompt, 'region': [i/count, 0, (i+1)/count, 1],
                          'loras': [{'file': inventory[selected[name]['relative_path']], 'weight': args.weight, 'clip_weight': args.weight}]}
                         for i, (name, prompt) in enumerate(definitions[:count])]
                for seed in args.seeds:
                    builder = {'masked_hooks': build, 'author_global': author_global_build,
                               'author_global_private_clip': author_private_clip_build}[args.binding]
                    if args.studio_pipeline:
                        graph = comfyui.graph(checkpoint, scene, negative, seed, cfg, style)
                        passes = [{'name': role['name'], 'positive': role['prompt'], 'region': role['region'],
                                   'loras': role['loras']} for role in roles]
                        studio_cfg = {**cfg, 'regional_lora_backend': 'native_masked', 'regional_lora_hooks': True,
                                      'regional_auto_pose': args.per_person_pose, 'regional_pose_margin': .18}
                        cast = regional_prompt.apply(graph, {'regional_characters': True, 'text': scene, 'characters': roles}, studio_cfg, passes)
                        lora_binding.apply_passes(graph, passes, style, studio_cfg, seed, negative, False, cast)
                    else:
                        graph = builder(checkpoint, scene, negative, seed, cfg, roles, style, args.global_weight)
                    if args.native_attention and not args.studio_pipeline:
                        graph['290']['inputs']['model'] = ['1', 0]
                        for node_id in list(graph):
                            if graph[node_id]['class_type'] in ('AttentionCouple', 'AttentionCoupleRegions', 'AttentionCoupleRegion'):
                                graph.pop(node_id)
                    if args.per_person_pose and not args.studio_pipeline:
                        per_person_pose(graph, roles, cfg)
                    elif args.pose:
                        regional_lora.apply_pose(graph, regional_lora.standing_pose(roles, {**cfg, 'regional_pose_margin': .18}), cfg)
                        graph['280']['inputs'].pop('areas', None)
                        graph['282']['inputs'].update(strength=1., end_percent=1.)
                    if args.references:
                        refs = [{'image': 'qa-official-references/'+file+'-clip.png', 'name': role['name'],
                                 'region': role['region'], 'weight': args.reference_weight, 'end_at': .8}
                                for file, role in zip(('Ganyu', 'Keqing', 'Jean', 'Mona'), roles)]
                        reference_images.apply_references(graph, refs, cfg)
                    assert sum(n['class_type'] == 'KSampler' for n in graph.values()) == 1
                    assert not any(n['class_type'] in ('ImageCrop', 'ImageCompositeMasked', 'LocalDrawPersonLayer') for n in graph.values())
                    key = f'{model}-{count}-people-{seed}'
                    e.save(out / (key + '-workflow.json'), graph)
                    started = time.monotonic()
                    job = comfyui.api('/prompt', {'prompt': graph})
                    e.need(not job.get('node_errors'), str(job.get('node_errors')))
                    history = None
                    while time.monotonic()-started < 900:
                        history = comfyui.api('/history/'+job['prompt_id']).get(job['prompt_id'])
                        if history:
                            break
                        time.sleep(.5)
                    e.save(out / (key + '-history.json'), history)
                    e.need(history and history.get('status', {}).get('status_str') == 'success', 'Attention Couple GPU 执行失败')
                    descriptor = history['outputs']['7']['images'][0]
                    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                    with opener.open(cfg['endpoint']+'/view?'+urllib.parse.urlencode(descriptor), timeout=30) as response:
                        (out / (key+'.png')).write_bytes(response.read())
                    records.append({'key': key, 'expected_people': count, 'characters': [r['name'] for r in roles],
                                    'seconds': round(time.monotonic()-started, 2), 'acceptance': 'pending_visual_review'})
                    e.save(out / 'renders.json', records)
                    e.save(ROOT / 'data/test-results/attention-couple/latest.json', {'path': str(out), 'acceptance': 'pending_visual_review'})
                    print(key + ' 已使用 GPU 生成', flush=True)
    finally:
        manager.stop()


if __name__ == '__main__':
    main()
