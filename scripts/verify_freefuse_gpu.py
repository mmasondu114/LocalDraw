"""Run the published FreeFuse SDXL nodes without changing their implementation.

The author's manual mask utility preserves explicit studio regions. The optional
automatic mode uses the author's complete SDXL example's Phase 1 settings.
Each image is sampled and decoded once on one shared canvas, with no body paste.
"""
import argparse
import hashlib
from pathlib import Path
import subprocess
import sys
import time
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from comic_workflow import comfyui, engine as e

REVISION = 'fe3aed9581b39d8acf89ae840cc8df78fad45063'
SOURCE = 'https://github.com/yaoliliu/FreeFuse/tree/comfyui'


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--count', type=int, choices=[2, 3, 4], default=2)
    parser.add_argument('--seed', type=int, default=71008)
    parser.add_argument('--sitting', action='store_true')
    parser.add_argument('--masks', choices=['manual', 'automatic'], default='manual')
    parser.add_argument('--studio-sampling', action='store_true')
    parser.add_argument('--positive-bias', type=float, default=7.)
    parser.add_argument('--concise-appearance', action='store_true')
    args = parser.parse_args()
    for directory in ('data/published-solutions/FreeFuse', 'runtime/custom_nodes/FreeFuse'):
        revision = subprocess.check_output(['git', '-C', str(ROOT / directory), 'rev-parse', 'HEAD'], text=True).strip()
        e.need(revision == REVISION, 'FreeFuse 作者版本与固定来源不符')
        # Python import creates bytecode caches; tracked author sources must stay exact.
        changes = subprocess.check_output(['git', '-C', str(ROOT / directory), 'status', '--porcelain', '--untracked-files=no'], text=True).strip()
        e.need(not changes, 'FreeFuse 原版实现存在本地改动')
    provenance = e.read(ROOT / 'data/test-results/native-studio-batch/20261007-095103-898360/source-provenance.json')
    characters = provenance['request']['characters'][:args.count]
    if args.concise_appearance:
        # Select already-tested appearance tags; avoid long concepts spanning
        # several CLIP chunks or repeated punctuation in the full source tags.
        short = {
            '甘雨': 'genshin ganyu, blue hair, red and black horns, purple eyes, neck bell, black leotard, detached white sleeves, black pantyhose',
            '刻晴': 'kqngdf, purple hair, cone hair bun, twintails, purple eyes, purple dress, detached sleeves, black pantyhose, purple gloves',
            '琴': 'lgcjng, blonde hair, ponytail, blue eyes, black shirt, white vest, black sleeves, black gloves, white pants, white boots',
            '莫娜': 'genshin mona, dark purple hair, twintails, turquoise eyes, large blue witch hat, blue leotard, black pantyhose, purple detached sleeves, black gloves'}
        characters = [{**c, 'prompt': short[c['name']]} for c in characters]
    selection = e.read(ROOT / 'data/lora-curation/selection.json')['characters']
    chosen = []
    concepts = {'甘雨': 'genshin ganyu', '刻晴': 'kqngdf', '琴': 'lgcjng', '莫娜': 'genshin mona'}
    if args.concise_appearance:
        concepts = {c['name']: c['prompt'] for c in characters}
    for character in characters:
        row = next(r for r in selection if r['character']['name'] == character['name'])
        candidate = row['selected']
        path = ROOT / 'models/loras' / candidate['relative_path']
        with path.open('rb') as stream:
            e.need(hashlib.file_digest(stream, 'sha256').hexdigest() == candidate['sha256'], '角色权重完整 SHA256 不符')
        chosen.append({'name': character['name'], 'path': candidate['relative_path'], 'sha256': candidate['sha256'],
                       'page': candidate['page'], 'concept_text': concepts[character['name']]})
    cfg = {**comfyui.config(), 'width': 1216, 'height': 832, 'steps': 30, 'cfg': 7.,
           'sampler_name': 'euler', 'scheduler': 'normal'}
    if args.studio_sampling:
        cfg.update({key: comfyui.config()[key] for key in ('steps', 'cfg', 'sampler_name', 'scheduler')})
    background = 'outdoors, park, daylight, one continuous park background'
    scene = f'{args.count}girls, full body, head to toe, feet visible, ' + (
        'sitting side by side on one park bench' if args.sitting else 'standing side by side')
    scene += ', distant view, wide shot, Japanese anime, thin lineart, soft cel shading, masterpiece, best quality, amazing quality'
    positive = ', '.join([scene, *[c['prompt'] for c in characters], background])
    negative = 'worst quality, low quality, bad anatomy, bad hands, extra fingers, missing fingers, extra limbs, extra people, duplicate person, cropped head, cropped feet, out of frame, collage, split screen, western comics, thick outlines, text, watermark'
    out = ROOT / 'data/test-results/freefuse' / e.stamp()
    out.mkdir(parents=True)
    report = {'arguments': vars(args), 'author_repository': SOURCE, 'author_revision': REVISION,
              'author_files_unmodified': True, 'author_workflow': 'workflows/sdxl_freefuse_complete.json',
              'gpu_only': True, 'chosen_weights': chosen, 'complete_lora_sha256_verified': True,
              'official_visual_references': provenance['official_references'][:args.count],
              'reference_adapters_used': False, 'production_defaults_changed': False,
              'concept_matching': 'Concepts remain verbatim in the main prompt. Full appearance tags or an explicitly requested concise selection of those same tags are used. An earlier full-tag concept failed author token matching before sampling.',
              'effective_sampling': {k:cfg[k] for k in ('checkpoint', 'steps', 'cfg', 'sampler_name', 'scheduler')},
              'scope': 'Published author LoRA loader, token positions, manual mask bank or automatic Phase 1, and mask applicator; native shared latent, sampling and decoding. The checkpoint, roles, explicit regions and Japanese anime scene are application inputs. Author SDXL example settings are the starting point; each requested prompt, sampling or bias adaptation is recorded in arguments and effective_sampling.',
              'acceptance': 'pending_visual_review'}
    e.save(out / 'report.json', report)
    manager = comfyui.ComfyManager()
    try:
        manager.start({'freefuse_trial': True})
        e.save(out / 'gpu-system.json', comfyui.api('/system_stats'))
        info = comfyui.api('/object_info')
        for name in ('FreeFuseLoRALoader', 'FreeFuseTokenPositions', 'FreeFuseMaskApplicator', 'FreeFusePhase1Sampler', 'FreeFuseMaskBankFromImages'):
            e.need(name in info, '作者节点未加载：' + name)
        inventory = comfyui.discover()['lora_files']
        graph = comfyui.graph(cfg['checkpoint'], positive, negative, args.seed, cfg)
        graph['90'] = {'class_type': 'LocalDrawGpuHooks', 'inputs': {'model': ['1', 0]}}
        graph['91'] = {'class_type': 'LocalDrawGpuClip', 'inputs': {'clip': ['1', 1]}}
        model, clip, data = ['90', 0], ['91', 0], None
        concept_inputs = {'enable_background': True, 'background_text': background}
        bank_inputs = {'width': cfg['width'] // 8, 'height': cfg['height'] // 8, 'use_alpha': False, 'invert_alpha': False, 'threshold': .5}
        for index, character in enumerate(chosen):
            key = str(100 + index)
            adapter = f'character{index+1}'
            inputs = {'model': model, 'clip': clip, 'lora_name': inventory[character['path']],
                      'adapter_name': adapter, 'strength_model': .7, 'strength_clip': .7}
            if data:
                inputs['freefuse_data'] = data
            graph[key] = {'class_type': 'FreeFuseLoRALoader', 'inputs': inputs}
            model, clip, data = [key, 0], [key, 1], [key, 2]
            concept_inputs.update({f'adapter_name_{index+1}': adapter, f'concept_text_{index+1}': character['concept_text']})
            x0 = round(index * cfg['width'] / args.count)
            x1 = round((index+1) * cfg['width'] / args.count)
            empty, rectangle, composite, image = [str(200 + 10*index + j) for j in range(4)]
            graph[empty] = {'class_type': 'SolidMask', 'inputs': {'value': 0., 'width': cfg['width'], 'height': cfg['height']}}
            graph[rectangle] = {'class_type': 'SolidMask', 'inputs': {'value': 1., 'width': x1-x0, 'height': cfg['height']}}
            graph[composite] = {'class_type': 'MaskComposite', 'inputs': {'destination': [empty, 0], 'source': [rectangle, 0], 'x': x0, 'y': 0, 'operation': 'add'}}
            graph[image] = {'class_type': 'MaskToImage', 'inputs': {'mask': [composite, 0]}}
            bank_inputs[f'mask_image_{index:02d}'] = [image, 0]
            graph[f'mask-preview-{index}'] = {'class_type': 'SaveImage', 'inputs': {'images': [image, 0], 'filename_prefix': f'comic-studio/freefuse-mask-{index}'}}
        concept_inputs['freefuse_data'] = data
        graph['300'] = {'class_type': 'FreeFuseConceptMap', 'inputs': concept_inputs}
        graph['301'] = {'class_type': 'FreeFuseTokenPositions', 'inputs': {'clip': clip, 'prompt': positive, 'freefuse_data': ['300', 0], 'filter_meaningless': True, 'filter_single_char': True}}
        graph['2']['inputs']['clip'] = clip
        graph['3']['inputs']['clip'] = clip
        if args.masks == 'manual':
            bank_inputs['freefuse_data'] = ['301', 0]
            graph['302'] = {'class_type': 'FreeFuseMaskBankFromImages', 'inputs': bank_inputs}
            masks = ['302', 0]
        else:
            graph['302'] = {'class_type': 'FreeFusePhase1Sampler', 'inputs': {
                'model': model, 'conditioning': ['2', 0], 'neg_conditioning': ['3', 0], 'latent': ['4', 0],
                'freefuse_data': ['301', 0], 'seed': args.seed, 'steps': cfg['steps'], 'collect_step': 10, 'cfg': cfg['cfg'],
                'sampler_name': cfg['sampler_name'], 'scheduler': cfg['scheduler'], 'collect_block': 18, 'collect_block_end': 18,
                'collect_region': 'output_early ★ (recommended)', 'collect_tf_index': 3, 'temperature': 500.,
                'top_k_ratio': .1, 'disable_lora_phase1': True, 'bg_scale': .95, 'use_morphological_cleaning': False,
                'balance_iterations': 15, 'balance_lr': .01, 'gravity_weight': .01, 'spatial_weight': .00004,
                'momentum': .2, 'centroid_margin': 0., 'border_penalty': 0., 'anisotropy': 1.3}}
            model, masks = ['302', 0], ['302', 1]
            graph['auto-mask-preview'] = {'class_type': 'SaveImage', 'inputs': {'images': ['302', 2], 'filename_prefix': 'comic-studio/freefuse-automatic-masks'}}
        graph['303'] = {'class_type': 'FreeFuseMaskApplicator', 'inputs': {
            'model': model, 'masks': masks, 'freefuse_data': ['301', 0], 'latent': ['4', 0],
            'enable_token_masking': True, 'enable_attention_bias': True, 'bias_scale': 5.,
            'positive_bias_scale': args.positive_bias, 'bidirectional': True, 'use_positive_bias': True, 'bias_blocks': 'all'}}
        graph['5']['inputs']['model'] = ['303', 0]
        graph['7']['inputs']['filename_prefix'] = 'comic-studio/freefuse'
        e.save(out / 'workflow.json', graph)
        job = comfyui.api('/prompt', {'prompt': graph})
        e.save(out / 'job.json', job)
        e.need(not job.get('node_errors'), '作者节点不接受工作流')
        started = time.monotonic()
        history = None
        while time.monotonic()-started < 900:
            history = comfyui.api('/history/' + job['prompt_id']).get(job['prompt_id'])
            if history:
                break
            time.sleep(.5)
        e.save(out / 'history.json', history)
        e.need(history and history.get('status', {}).get('status_str') == 'success', 'FreeFuse GPU 执行失败；禁止 CPU 回退')
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        outputs = [('7', 'result.png')] + [(f'mask-preview-{i}', f'mask-{i}.png') for i in range(args.count)]
        if args.masks == 'automatic':
            outputs.append(('auto-mask-preview', 'automatic-masks.png'))
        for node, filename in outputs:
            descriptor = history['outputs'][node]['images'][0]
            with opener.open(cfg['endpoint'] + '/view?' + urllib.parse.urlencode(descriptor), timeout=30) as response:
                (out / filename).write_bytes(response.read())
        report['execution_seconds'] = time.monotonic()-started
        e.save(out / 'report.json', report)
        e.save(ROOT / 'data/test-results/freefuse/latest.json', {'path': str(out), 'acceptance': 'pending_visual_review'})
        print('FreeFuse 已使用 GPU 完成：' + str(out), flush=True)
    except Exception as ex:
        report.update(execution_failed=True, error=str(ex), acceptance='not_accepted')
        e.save(out / 'report.json', report)
        raise
    finally:
        manager.stop()
        (out / 'runtime.log').write_text((ROOT / 'data/logs/comfyui.log').read_text(encoding='utf-8', errors='replace')[-35000:], encoding='utf-8')


if __name__ == '__main__':
    main()
