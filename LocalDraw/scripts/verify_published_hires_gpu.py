"""Evaluate WAI's documented whole-image hires recipe with native ComfyUI nodes."""
import argparse
import copy
import hashlib
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from comic_workflow import comfyui, engine as e


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--denoise', type=float, default=.35, choices=[.35, .5])
    parser.add_argument('--short-prompts', action='store_true')
    parser.add_argument('--style-weight', type=float)
    args = parser.parse_args()
    base = ROOT / 'data/test-results/native-studio-batch/20261007-095103-898360'
    source = next((base / 'projects').rglob('workflow-1.json'))
    graph = e.read(source)
    out = ROOT / 'data/test-results/published-hires' / e.stamp()
    out.mkdir(parents=True)
    verified = e.read(ROOT / 'data/lora-curation/research/anime-upscaler-verified.json')
    assert hashlib.sha256((ROOT / verified['file']).read_bytes()).hexdigest() == verified['sha256']
    width, height = graph['4']['inputs']['width'], graph['4']['inputs']['height']
    target = [round(width * 1.5 / 8) * 8, round(height * 1.5 / 8) * 8]
    if args.style_weight is not None:
        graph['200']['inputs'].update(strength_model=args.style_weight, strength_clip=args.style_weight)
    if args.short_prompts:
        context = 'full body, standing, outdoors, park, daylight, Japanese anime, soft cel shading, thin lineart, masterpiece, best quality, amazing quality'
        negative = 'bad quality, worst quality, worst detail, sketch, censor, nsfw, extra people, duplicate, split screen, text, watermark'
        for index in range(4):
            graph[str(310 + 40 * index)]['inputs']['text'] = graph[str(310 + 40 * index)]['inputs']['text'].split(', , full body,')[0] + ', ' + context
            graph[str(311 + 40 * index)]['inputs']['text'] = negative
        graph['2']['inputs']['text'] = context
        graph['3']['inputs']['text'] = negative
    graph['7']['inputs']['filename_prefix'] = 'comic-studio/published-hires-base'
    sampler = copy.deepcopy(graph['5'])
    sampler['inputs'].update(latent_image=['903', 0], seed=71008, steps=20, denoise=args.denoise)
    graph.update({
        '900': {'class_type': 'UpscaleModelLoader', 'inputs': {'model_name': 'RealESRGAN_x4plus_anime_6B.pth'}},
        '901': {'class_type': 'LocalDrawGpuUpscale', 'inputs': {'upscale_model': ['900', 0], 'image': ['6', 0]}},
        '902': {'class_type': 'ImageScale', 'inputs': {'image': ['901', 0], 'upscale_method': 'lanczos', 'width': target[0], 'height': target[1], 'crop': 'disabled'}},
        '903': {'class_type': 'VAEEncode', 'inputs': {'pixels': ['902', 0], 'vae': ['1', 2]}},
        '904': sampler,
        '905': {'class_type': 'VAEDecode', 'inputs': {'samples': ['904', 0], 'vae': ['1', 2]}},
        '906': {'class_type': 'SaveImage', 'inputs': {'images': ['905', 0], 'filename_prefix': 'comic-studio/published-hires-final'}},
    })
    assert not any(n['class_type'] in ('ImageCrop', 'ImageCompositeMasked', 'LocalDrawPersonLayer') for n in graph.values())
    e.save(out / 'source-provenance.json', {
        'author_recipe': 'https://civitai.com/models/827184?modelVersionId=2883731',
        'saved_author_instructions': 'data/lora-curation/research/wai-author-card.json',
        'native_workflow': 'https://comfyanonymous.github.io/ComfyUI_examples/2_pass_txt2img/',
        'native_upscaler_source_sha256': hashlib.sha256(Path('E:/AI/ComfyUI/comfy_extras/nodes_upscale_model.py').read_bytes()).hexdigest(),
        'upscaler': verified, 'source_graph': source.relative_to(ROOT).as_posix(),
        'base_size': [width, height], 'target_size': target,
        'settings': {'scale': 1.5, 'steps': 20, 'denoise': args.denoise, 'short_prompts': args.short_prompts, 'style_weight_override': args.style_weight},
        'scope': 'Full-canvas upscale and img2img; no cropped character layers. Existing identity hooks, pose branches and references retained. Base size is slightly below the author recommendation, so this is a controlled feasibility test.',
        'gpu_only': True, 'production_defaults_changed': False,
    })
    e.save(out / 'workflow.json', graph)
    manager = comfyui.ComfyManager()
    try:
        manager.start()
        e.save(out / 'gpu-system.json', comfyui.api('/system_stats'))
        job = comfyui.api('/prompt', {'prompt': graph})
        e.save(out / 'job.json', job)
        e.need(not job.get('node_errors'), str(job.get('node_errors')))
        started = time.monotonic()
        history = None
        while time.monotonic() - started < 900:
            history = comfyui.api('/history/' + job['prompt_id']).get(job['prompt_id'])
            if history:
                break
            time.sleep(.5)
        e.save(out / 'history.json', history)
        e.need(history and history.get('status', {}).get('status_str') == 'success', '整幅高清试验未成功；禁止 CPU 回退')
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for node, name in [('7', 'base.png'), ('906', 'result.png')]:
            descriptor = history['outputs'][node]['images'][0]
            with opener.open(comfyui.config()['endpoint'] + '/view?' + urllib.parse.urlencode(descriptor), timeout=30) as response:
                (out / name).write_bytes(response.read())
        e.save(ROOT / 'data/test-results/published-hires/latest.json', {'path': str(out), 'acceptance': 'pending_visual_review'})
        print('作者整幅高清流程已使用 GPU 执行：' + str(out), flush=True)
    finally:
        manager.stop()
        e.save(out / 'runtime-log.json', {'log': (ROOT / 'data/logs/comfyui.log').read_text(encoding='utf-8', errors='replace')[-14000:]})


if __name__ == '__main__':
    main()
