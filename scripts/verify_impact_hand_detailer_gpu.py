"""Run the pinned author's native Detailer, retaining every diagnostic crop.

Recipe source: ltdrdata/ComfyUI-extension-tutorials dwpose-segs-handfix.png.
This trial uses its two detail passes with an anime checkpoint and verified
anime hand detections; it does not claim to reproduce its SD1.5 DWPose stage.
"""
import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from comic_workflow import comfyui, engine as e, hand_detail


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--max-size', type=int, choices=(768, 1024), default=768)
    parser.add_argument('--seed', type=int, default=71008)
    parser.add_argument('--hand-lora', choices=('v0.1', 'v1.1'))
    parser.add_argument('--hand-weight', type=float, default=.7)
    parser.add_argument('--character-scoped', action='store_true')
    args = parser.parse_args()
    source = ROOT / 'data/test-results/published-hires/20261007-102547-732245/result.png'
    detections = e.read(ROOT / 'data/test-results/hand-detection-resolution/20261007-104419-563445/results.json')[1]
    e.need(detections['image'] == source.relative_to(ROOT).as_posix(), '检测与原图不一致')
    e.need(detections['device'] == 'cuda:0', '检测必须使用 CUDA')
    author = e.read(ROOT / 'data/lora-curation/sources/impact-author-handfix-provenance.json')
    source_node = ROOT / 'runtime/custom_nodes/comfyui-impact-pack'
    revision = subprocess.check_output(['git', '-C', str(source_node), 'rev-parse', 'HEAD'], text=True).strip()
    e.need(revision == 'b22aa90cf4a419a8ba9881f90827fbb35dad2612', '作者节点版本未核对')
    out = ROOT / 'data/test-results/impact-hand-detailer' / e.stamp()
    out.mkdir(parents=True)
    shutil.copyfile(source, out / 'source.png')
    with Image.open(source) as pic:
        mask = Image.new('RGB', pic.size, 0)
        draw = ImageDraw.Draw(mask)
        regions = hand_detail.regions(detections['boxes'], pic.size, 8)
        for region in regions:
            for box in region['boxes']:
                draw.rectangle(box, fill='white')
        mask.save(out / 'mask.png')
    input_dir = ROOT / 'data/comfy-input/qa-impact-hands' / out.name
    input_dir.mkdir(parents=True)
    shutil.copyfile(source, input_dir / 'source.png')
    shutil.copyfile(out / 'mask.png', input_dir / 'mask.png')
    prefix = 'qa-impact-hands/' + out.name + '/'
    g = {
        '1': {'class_type': 'CheckpointLoaderSimple', 'inputs': {'ckpt_name': 'waiIllustriousSDXL_v170.safetensors'}},
        '2': {'class_type': 'CLIPTextEncode', 'inputs': {'clip': ['1', 1], 'text': 'masterpiece, best quality, anime, cel shading, black gloves, hand, natural hand pose, five fingers'}},
        '3': {'class_type': 'CLIPTextEncode', 'inputs': {'clip': ['1', 1], 'text': 'bad hands, fused fingers, extra fingers, missing fingers, photorealistic, blurry'}},
        '10': {'class_type': 'LoadImage', 'inputs': {'image': prefix + 'source.png'}},
        '11': {'class_type': 'LoadImage', 'inputs': {'image': prefix + 'mask.png'}},
        '12': {'class_type': 'ImageToMask', 'inputs': {'image': ['11', 0], 'channel': 'red'}},
        '13': {'class_type': 'MaskToSEGS', 'inputs': {'mask': ['12', 0], 'combined': False, 'crop_factor': 5.0, 'bbox_fill': False, 'drop_size': 10, 'contour_fill': False}},
    }
    selected_hand = None
    if args.hand_lora:
        candidates = e.read(ROOT / 'data/lora-curation/research/hand-lora-author-verified.json')
        selected_hand = next(c for c in candidates if c['version'].endswith(args.hand_lora))
        weight_path = ROOT / selected_hand['file']
        e.need(hashlib.sha256(weight_path.read_bytes()).hexdigest() == selected_hand['sha256'], '手部 LoRA 完整校验失败')
        e.need(0 < args.hand_weight <= 1, '手部作者权重范围无效')
        g['200'] = {'class_type': 'LoraLoader', 'inputs': {'model': ['1', 0], 'clip': ['1', 1],
            'lora_name': selected_hand['diagnostic_relative'], 'strength_model': args.hand_weight, 'strength_clip': args.hand_weight}}
        g['2']['inputs']['clip'] = g['3']['inputs']['clip'] = ['200', 1]
        if selected_hand['triggers']:
            g['2']['inputs']['text'] += ', ' + ', '.join(selected_hand['triggers'])
    image = ['10', 0]
    branches = [{'name': 'all', 'segs': ['13', 0], 'positive': ['2', 0],
                 'negative': ['3', 0], 'clip': ['200', 1] if selected_hand else ['1', 1]}]
    if args.character_scoped:
        source_graph = e.read(source.parent / 'workflow.json')
        source_roles = e.read(ROOT / 'data/test-results/native-studio-batch/20261007-095103-898360/source-provenance.json')['request']['characters']
        branches = []
        g['290'] = {'class_type': 'LocalDrawGpuHooks', 'inputs': {'model': ['1', 0]}}
        g['299'] = {'class_type': 'LocalDrawGpuClip', 'inputs': {'clip': ['1', 1]}}
        for index, role in enumerate(source_roles):
            base = 300 + index * 40
            def node(offset, kind, **inputs):
                key = str(base + offset)
                g[key] = {'class_type': kind, 'inputs': inputs}
                return [key, 0]
            local_regions = [r for r in regions if role['region'][0] <= (r['boxes'][0][0] + r['boxes'][0][2]) / (2 * mask.width) < role['region'][2]]
            e.need(len(local_regions) == 2, '角色手部归属不明确；不得推测身份')
            role_mask = Image.new('RGB', mask.size, 0)
            role_draw = ImageDraw.Draw(role_mask)
            for region in local_regions:
                for box in region['boxes']:
                    role_draw.rectangle(box, fill='white')
            mask_name = f'role-{index}-mask.png'
            role_mask.save(out / mask_name)
            shutil.copyfile(out / mask_name, input_dir / mask_name)
            loaded = node(1, 'LoadImage', image=prefix + mask_name)
            mask_link = node(2, 'ImageToMask', image=loaded, channel='red')
            segs = node(3, 'MaskToSEGS', mask=mask_link, combined=False, crop_factor=5.0,
                        bbox_fill=False, drop_size=10, contour_fill=False)
            hook_inputs = dict(source_graph[str(base)]['inputs'])
            hook_inputs.pop('prev_hooks', None)
            hook = node(4, 'CreateHookLora', **hook_inputs)
            if selected_hand:
                hook = node(5, 'CreateHookLora', lora_name=selected_hand['diagnostic_relative'],
                            strength_model=args.hand_weight, strength_clip=args.hand_weight, prev_hooks=hook)
            clip = node(8, 'SetClipHooks', clip=['299', 0], hooks=hook, apply_to_conds=True, schedule_clip=False)
            positive = role['prompt'] + ', masterpiece, best quality, anime, cel shading, hand, natural hand pose, five fingers'
            if selected_hand and selected_hand['triggers']:
                positive += ', ' + ', '.join(selected_hand['triggers'])
            pos = node(10, 'CLIPTextEncode', clip=clip, text=positive)
            neg = node(11, 'CLIPTextEncode', clip=clip, text=g['3']['inputs']['text'])
            branches.append({'name': role['name'], 'segs': segs, 'positive': pos, 'negative': neg,
                             'clip': clip, 'regions': local_regions, 'lora': hook_inputs})
    for stage, (guide, denoise) in enumerate(((256, .6), (360, .4)), 1):
      for branch_index, branch in enumerate(branches):
        key = str(600 + stage * 100 + branch_index * 10)
        g[key] = {'class_type': 'DetailerForEachDebug', 'inputs': {
            'image': image, 'segs': branch['segs'], 'model': ['290', 0] if args.character_scoped else (['200', 0] if selected_hand else ['1', 0]),
            'clip': branch['clip'], 'vae': ['1', 2],
            'guide_size': guide, 'guide_size_for': True, 'max_size': args.max_size,
            'seed': args.seed + stage - 1, 'steps': 20, 'cfg': 5.5,
            'sampler_name': 'euler_ancestral', 'scheduler': 'normal', 'positive': branch['positive'], 'negative': branch['negative'],
            'denoise': denoise, 'feather': 5, 'noise_mask': True, 'force_inpaint': True, 'wildcard': '',
            'cycle': 1, 'inpaint_model': False, 'noise_mask_feather': 0,
            'tiled_encode': False, 'tiled_decode': False,
        }}
        image = [key, 0]
        for offset, label, index in ((0, 'whole', 0), (1, 'original-crops', 1), (2, 'repaired-crops', 2)):
            save_key = str(int(key) + 1 + offset) if args.character_scoped else str(30 + stage * 10 + offset)
            g[save_key] = {'class_type': 'SaveImage', 'inputs': {'images': [key, index], 'filename_prefix': f'qa-impact-hands/{out.name}/stage-{stage}-{branch["name"]}-{label}'}}
    e.save(out / 'workflow.json', g)
    e.save(out / 'source-provenance.json', {'tutorial': author, 'node_source': 'https://github.com/comfyorg/comfyui-impact-pack',
        'node_revision': revision, 'regions': regions, 'image_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'adaptations': ['WAI17 anime checkpoint and its sampler/CFG range replace author SD1.5 photo checkpoints.',
            'GPU-verified anime hand boxes replace general photo detector; native MaskToSEGS supplies regions.',
            'Two author Detailer passes retained; DWPose ControlNet stage omitted for this isolated trial.',
            'No character body cropping or compositing. Only native author local hand inpainting.'],
        'max_size': args.max_size, 'hand_lora': selected_hand, 'hand_weight': args.hand_weight if selected_hand else None,
        'character_scoped': args.character_scoped, 'branches': branches,
        'scoped_lora_source': 'https://blog.comfy.org/p/masking-and-scheduling-lora-and-model-weights' if args.character_scoped else None,
        'production_defaults_changed': False})
    manager = comfyui.ComfyManager()
    log_path = ROOT / 'data/logs/comfyui.log'
    log_start = log_path.stat().st_size if log_path.exists() else 0
    try:
        runtime = {'impact_hand_trial': True}
        if selected_hand:
            runtime['diagnostic_loras_directory'] = str(ROOT / 'data/comfy-lora-diagnostic/hand-author')
        manager.start(runtime)
        stats = comfyui.api('/system_stats')
        e.need('--gpu-only' in stats['system']['argv'], '禁止回退到 CPU')
        e.save(out / 'gpu-system.json', stats)
        info = comfyui.api('/object_info/DetailerForEachDebug')
        e.need('DetailerForEachDebug' in info, '作者修复节点未加载')
        if selected_hand:
            g['200']['inputs']['lora_name'] = comfyui.discover()['lora_files'][selected_hand['diagnostic_relative']]
        available = comfyui.discover()['lora_files']
        for node in g.values():
            if node['class_type'] == 'CreateHookLora':
                name = node['inputs']['lora_name']
                node['inputs']['lora_name'] = available[name.replace('\\', '/')]
        e.save(out / 'workflow.json', g)
        job = comfyui.api('/prompt', {'prompt': g})
        e.save(out / 'job.json', job)
        e.need(not job.get('node_errors') and job.get('prompt_id'), '作者修复图节点校验失败')
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            history = comfyui.api('/history/' + job['prompt_id']).get(job['prompt_id'])
            if history:
                break
            time.sleep(.5)
        else:
            raise TimeoutError('作者修复图尚未完成；不得重复提交')
        e.save(out / 'history.json', history)
        e.need(history.get('status', {}).get('status_str') == 'success', '作者修复图执行失败')
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for key, result in history['outputs'].items():
            for i, descriptor in enumerate(result.get('images', []), 1):
                with opener.open(comfyui.config()['endpoint'] + '/view?' + urllib.parse.urlencode(descriptor), timeout=30) as response:
                    (out / f'{key}-{i}.png').write_bytes(response.read())
        e.save(out / 'report.json', {'acceptance': 'pending_visual_review', 'expected_people': 4, 'hand_regions': len(regions), 'passes': 2})
        e.save(ROOT / 'data/test-results/impact-hand-detailer/latest.json', {'path': str(out), 'acceptance': 'pending_visual_review'})
        print('作者原生 Detailer 完成两轮 GPU 修复：' + str(out), flush=True)
    finally:
        manager.stop()
        if log_path.exists():
            with log_path.open('rb') as stream:
                stream.seek(log_start)
                (out / 'runtime.log').write_bytes(stream.read())


if __name__ == '__main__':
    main()
