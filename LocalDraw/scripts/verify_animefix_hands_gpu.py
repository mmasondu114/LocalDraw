"""Evaluate the published AnimeFix native MaskDetailer recipe on eight hands.

Keeps the author's actual prompts and sampling widgets. The reviewed Meta SAM
masks are produced by its unmodified CUDA predictor. Per-character native LoRA
hooks retain the original identity instead of loading unrelated author styles.
"""
import argparse
import hashlib
from pathlib import Path
import shutil
import sys
import time
import urllib.parse
import urllib.request
from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from comic_workflow import comfyui, engine as e


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--seed', type=int, default=71008)
    parser.add_argument('--mask-directory', type=Path,
                        default=ROOT / 'data/test-results/published-sam-hands/meta-20261007-1506')
    args = parser.parse_args()
    masks = e.read(args.mask_directory / 'report.json')
    e.need(masks['device'] == 'cuda:0' and masks['guarded_forward_calls']['mask_decoder'] == 8,
           '八手遮罩未完成 CUDA 推理')
    source = ROOT / masks['source']
    e.need(hashlib.sha256(source.read_bytes()).hexdigest() == masks['source_sha256'], '原图与遮罩不一致')
    source_graph = e.read(source.parent / 'workflow.json')
    author_record = e.read(ROOT / 'data/lora-curation/research/animefix-author-workflow.json')
    author = e.read(ROOT / 'data/published-solutions/AnimeFix-Workflow/workflow_facefix_v2.json')
    nodes = {n['id']: n for n in author['nodes']}
    positive_extra = nodes[275]['widgets_values'][0]
    negative_extra = nodes[279]['widgets_values'][0]
    values = list(nodes[284]['widgets_values'])
    values.pop(5)  # UI seed randomization widget is not an API input.
    fields = ['guide_size', 'guide_size_for', 'max_size', 'mask_mode', 'seed', 'steps', 'cfg',
              'sampler_name', 'scheduler', 'denoise', 'feather', 'crop_factor', 'drop_size',
              'refiner_ratio', 'batch_size', 'cycle', 'inpaint_model', 'noise_mask_feather',
              'bbox_fill', 'contour_fill']
    recipe = dict(zip(fields, values, strict=True))
    recipe['seed'] = args.seed
    out = ROOT / 'data/test-results/animefix-hands' / e.stamp()
    out.mkdir(parents=True)
    shutil.copyfile(source, out / 'source.png')
    input_dir = ROOT / 'data/comfy-input/qa-animefix-hands' / out.name
    input_dir.mkdir(parents=True)
    shutil.copyfile(source, input_dir / 'source.png')
    prefix = 'qa-animefix-hands/' + out.name + '/'
    graph = {'1': source_graph['1'],
             '290': {'class_type': 'LocalDrawGpuHooks', 'inputs': {'model': ['1', 0]}},
             '291': {'class_type': 'DifferentialDiffusion', 'inputs': {'model': ['290', 0], 'strength': 1.0}},
             '299': {'class_type': 'LocalDrawGpuClip', 'inputs': {'clip': ['1', 1]}},
             '10': {'class_type': 'LoadImage', 'inputs': {'image': prefix + 'source.png'}}}
    for key, node in source_graph.items():
        if node['class_type'] == 'CreateHookLora' and int(key) < 300:
            graph[key] = node
    image = ['10', 0]
    names = ('甘雨', '刻晴', '琴', '莫娜')
    regions = []
    for index, name in enumerate(names):
        base = 300 + index * 40

        def node(offset, kind, **inputs):
            key = str(base + offset)
            graph[key] = {'class_type': kind, 'inputs': inputs}
            return [key, 0]
        graph[str(base)] = source_graph[str(base)]
        clip = node(8, 'SetClipHooks', clip=['299', 0], hooks=[str(base), 0],
                    apply_to_conds=True, schedule_clip=False)
        original_pos = node(10, 'CLIPTextEncode', clip=clip,
                            text=source_graph[str(base + 10)]['inputs']['text'])
        original_neg = node(11, 'CLIPTextEncode', clip=clip, text=source_graph['3']['inputs']['text'])
        extra_pos = node(12, 'CLIPTextEncode', clip=clip, text=positive_extra)
        extra_neg = node(13, 'CLIPTextEncode', clip=clip, text=negative_extra)
        positive = node(14, 'ConditioningCombine', conditioning_1=original_pos, conditioning_2=extra_pos)
        negative = node(15, 'ConditioningCombine', conditioning_1=original_neg, conditioning_2=extra_neg)
        with Image.open(source) as picture:
            mask = Image.new('L', picture.size, 0)
            hand_records = [item for item in masks['hand_regions']
                            if index / 4 <= (item['box'][0] + item['box'][2]) / (2 * picture.width) < (index + 1) / 4]
        e.need(len(hand_records) == 2, '角色手部归属不明确；不得猜测身份')
        for record in hand_records:
            with Image.open(args.mask_directory / record['file']) as hand:
                mask = ImageChops.lighter(mask, hand.convert('L'))
        mask_name = f'role-{index}-mask.png'
        mask.save(input_dir / mask_name)
        mask.save(out / mask_name)
        loaded = node(16, 'LoadImage', image=prefix + mask_name)
        mask_link = node(17, 'ImageToMask', image=loaded, channel='red')
        grown = node(18, 'GrowMask', mask=mask_link, expand=8, tapered_corners=True)
        pipe = node(19, 'ToBasicPipe', model=['291', 0], clip=clip, vae=['1', 2], positive=positive, negative=negative)
        detailer = node(20, 'MaskDetailerPipe', image=image, mask=grown, basic_pipe=pipe, **recipe)
        image = detailer
        node(21, 'SaveImage', images=image, filename_prefix=f'qa-animefix-hands/{out.name}/stage-{index}-{name}')
        node(22, 'SaveImage', images=[detailer[0], 1], filename_prefix=f'qa-animefix-hands/{out.name}/hand-crops-{index}-{name}')
        regions.append({'name': name, 'hands': hand_records,
                        'complete_lora_name': graph[str(base)]['inputs']['lora_name']})
    graph['7'] = {'class_type': 'SaveImage', 'inputs': {'images': image, 'filename_prefix': 'qa-animefix-hands/' + out.name + '/result'}}
    report = {'acceptance': 'pending_visual_review', 'author': author_record, 'recipe': recipe,
              'source': source.relative_to(ROOT).as_posix(), 'source_sha256': masks['source_sha256'],
              'mask_report': (args.mask_directory / 'report.json').relative_to(ROOT).as_posix(),
              'regions': regions, 'gpu_only': True, 'production_adoption': False,
              'adaptations': ['Frozen reviewed CUDA anime detector boxes and native Meta SAM predict replace the author general YOLO front end.',
                  'Original per-character LoRA hooks and full VAE replace unrelated author style LoRAs and approximate VAE.',
                  'Native GrowMask expands 8px; KJ GrowMaskWithBlur 6px blur is omitted. Native MaskDetailer feather8/noise-feather10 are retained.',
                  'Current ComfyUI native DifferentialDiffusion is applied explicitly before Impact sampling, avoiding its obsolete internal apply() call while retaining strength1 semantics.',
                  'No body crop/paste; only author local hand inpainting.'],
              'scope': 'Native MaskDetailer sampling and extra hand prompts match pinned author JSON; this is an adapted evaluation, not a full unmodified reproduction.'}
    e.save(out / 'report.json', report)
    manager = comfyui.ComfyManager()
    try:
        manager.start({'impact_hand_trial': True})
        stats = comfyui.api('/system_stats')
        e.need('--gpu-only' in stats['system']['argv'], '修复禁止回退 CPU')
        e.save(out / 'gpu-system.json', stats)
        available = comfyui.discover()['lora_files']
        for node in graph.values():
            if node['class_type'] == 'CreateHookLora':
                name = node['inputs']['lora_name']
                node['inputs']['lora_name'] = available[name.replace('\\', '/')]
        e.save(out / 'workflow.json', graph)
        job = comfyui.api('/prompt', {'prompt': graph})
        e.save(out / 'job.json', job)
        e.need(job.get('prompt_id') and not job.get('node_errors'), '作者 MaskDetailer 不接受工作流')
        history = None
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            history = comfyui.api('/history/' + job['prompt_id']).get(job['prompt_id'])
            if history:
                break
            time.sleep(.5)
        e.save(out / 'history.json', history)
        e.need(history and history.get('status', {}).get('status_str') == 'success', '作者手部 GPU 修复失败')
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for key, result in history['outputs'].items():
            for index, descriptor in enumerate(result.get('images', [])):
                with opener.open(comfyui.config()['endpoint'] + '/view?' + urllib.parse.urlencode(descriptor), timeout=30) as response:
                    (out / ('result.png' if key == '7' else f'{key}-{index}.png')).write_bytes(response.read())
        e.save(ROOT / 'data/test-results/animefix-hands/latest.json', {'path': str(out), 'acceptance': 'pending_visual_review'})
        print('公开日漫 MaskDetailer 完成八手 GPU 对照：' + str(out), flush=True)
    finally:
        manager.stop()
        (out / 'runtime.log').write_text((ROOT / 'data/logs/comfyui.log').read_text(encoding='utf-8', errors='replace')[-30000:], encoding='utf-8')


if __name__ == '__main__':
    main()
