"""Evaluate the author's SDXL refine workflow on a reviewed background mask."""
import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
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
    parser.add_argument('--mask-directory', type=Path, required=True)
    parser.add_argument('--seed', type=int, default=71008)
    parser.add_argument('--author-alpha', action='store_true')
    args = parser.parse_args()
    masks = e.read(args.mask_directory / 'report.json')
    e.need(masks['acceptance'] == 'four_people_protection_passed_only', '人物保护遮罩尚未通过实图复核')
    e.need(masks['device'] == 'cuda:0' and masks['guarded_forward_calls']['ISNetDIS'] == 1, '动漫分割未通过实际 GPU 推理验证')
    source = ROOT / masks['source']
    e.need(hashlib.sha256(source.read_bytes()).hexdigest() == masks['source_sha256'], '背景遮罩与原图不一致')
    revision = subprocess.check_output(['git', '-C', str(ROOT / 'runtime/custom_nodes/comfyui-inpaint-nodes'), 'rev-parse', 'HEAD'], text=True).strip()
    e.need(revision == '5b7681746fe19e563113d27366ba85236bc1ba5b', '作者局部重绘版本未经核对')
    weights = e.read(ROOT / 'data/lora-curation/research/fooocus-inpaint-models-verified.json')
    for record in weights:
        path = ROOT / 'models/inpaint' / record['original_name']
        with path.open('rb') as stream:
            e.need(hashlib.file_digest(stream, 'sha256').hexdigest() == record['sha256'], '局部重绘模型完整校验失败')
    out = ROOT / 'data/test-results/published-background-inpaint' / e.stamp()
    inputs = ROOT / 'data/comfy-input/qa-published-background' / out.name
    out.mkdir(parents=True)
    inputs.mkdir(parents=True)
    for name, path in [('source.png', source), ('mask.png', args.mask_directory / 'background-mask.png')]:
        shutil.copyfile(path, inputs / name)
        shutil.copyfile(path, out / name)
    if args.author_alpha:
        # Use the author's foreground alpha directly. The earlier conservative
        # 16px border retained pale background and visibly outlined the people.
        alpha = Image.open(args.mask_directory / 'author-alpha.png').convert('L')
        mask_image = alpha.point(lambda value: 0 if value >= 25 else 255)
        mask_image.save(inputs / 'mask.png')
        mask_image.save(out / 'mask.png')
    prefix = 'qa-published-background/' + out.name + '/'
    positive = 'masterpiece, best quality, anime screencap, Japanese anime background illustration, hand drawn background, flat colors, simple clean shapes, soft cel shading, thin lineart, outdoors, autumn park, red maple trees, continuous paved walkway, green lawn, distant trees, clear blue sky, daylight, consistent perspective'
    negative = 'people, person, girl, boy, face, character, human silhouette, extra people, white background, blank background, white void, fog, collage, split screen, seams, photorealistic, western comics, text, watermark, worst quality, low quality'
    graph = {
        '1': {'class_type': 'CheckpointLoaderSimple', 'inputs': {'ckpt_name': 'waiIllustriousSDXL_v170.safetensors'}},
        '2': {'class_type': 'CLIPTextEncode', 'inputs': {'clip': ['9', 1], 'text': positive}},
        '3': {'class_type': 'CLIPTextEncode', 'inputs': {'clip': ['9', 1], 'text': negative}},
        '9': {'class_type': 'LoraLoader', 'inputs': {'model': ['1', 0], 'clip': ['1', 1], 'lora_name': '原神/动画截图风格_Eufoniuzs_Anime_Screencap_illustrious_3-000010.safetensors', 'strength_model': .35, 'strength_clip': .35}},
        '10': {'class_type': 'LoadImage', 'inputs': {'image': prefix + 'source.png'}},
        '11': {'class_type': 'LoadImage', 'inputs': {'image': prefix + 'mask.png'}},
        '12': {'class_type': 'ImageToMask', 'inputs': {'image': ['11', 0], 'channel': 'red'}},
        '13': {'class_type': 'INPAINT_LoadFooocusInpaint', 'inputs': {'head': 'fooocus_inpaint_head.pth', 'patch': 'inpaint_v26.fooocus.patch'}},
        '14': {'class_type': 'LocalDrawGpuFooocusGuard', 'inputs': {'patch': ['13', 0]}},
        '15': {'class_type': 'INPAINT_VAEEncodeInpaintConditioning', 'inputs': {'positive': ['2', 0], 'negative': ['3', 0], 'vae': ['1', 2], 'pixels': ['10', 0], 'mask': ['12', 0]}},
        '16': {'class_type': 'INPAINT_ApplyFooocusInpaint', 'inputs': {'model': ['9', 0], 'patch': ['14', 0], 'latent': ['15', 2]}},
        '17': {'class_type': 'KSampler', 'inputs': {'model': ['16', 0], 'positive': ['15', 0], 'negative': ['15', 1], 'latent_image': ['15', 3], 'seed': args.seed, 'steps': 20, 'cfg': 5.0, 'sampler_name': 'dpmpp_2m_sde_gpu', 'scheduler': 'karras', 'denoise': .7}},
        '18': {'class_type': 'VAEDecode', 'inputs': {'samples': ['17', 0], 'vae': ['1', 2]}},
        '19': {'class_type': 'ImageCompositeMasked', 'inputs': {'destination': ['10', 0], 'source': ['18', 0], 'x': 0, 'y': 0, 'resize_source': False, 'mask': ['12', 0]}},
        '7': {'class_type': 'SaveImage', 'inputs': {'images': ['19', 0], 'filename_prefix': 'qa-published-background/' + out.name + '/result'}},
        '8': {'class_type': 'SaveImage', 'inputs': {'images': ['18', 0], 'filename_prefix': 'qa-published-background/' + out.name + '/raw'}},
    }
    report = {'source': source.relative_to(ROOT).as_posix(), 'source_sha256': masks['source_sha256'],
              'mask_report': (args.mask_directory / 'report.json').resolve().relative_to(ROOT).as_posix(),
              'author': 'https://github.com/Acly/comfyui-inpaint-nodes', 'revision': revision,
              'author_workflow': 'workflows/inpaint-refine.json', 'gpu_only': True,
              'recipe': {'steps': 20, 'cfg': 5.0, 'sampler': 'dpmpp_2m_sde_gpu', 'scheduler': 'karras', 'denoise': .7},
              'adaptations': ['WAI and existing anime style LoRA replace the author photo checkpoint.', 'Reviewed author ISNet CUDA foreground mask supplies the background region.', 'The entire original frame is inpainted at its original size; character pixels keep their existing coordinates.'],
              'production_adoption': False, 'acceptance': 'running'}
    report['mask_method'] = 'Author foreground alpha threshold0.1, no added protective border' if args.author_alpha else 'Author foreground alpha threshold0.1, conservative16px protective border'
    e.save(out / 'report.json', report)
    manager = comfyui.ComfyManager()
    try:
        manager.start({'fooocus_hand_trial': True})
        stats = comfyui.api('/system_stats')
        e.need('--gpu-only' in stats['system']['argv'], '背景重绘禁止 CPU 回退')
        e.save(out / 'gpu-system.json', stats)
        graph['9']['inputs']['lora_name'] = comfyui.discover()['lora_files'][graph['9']['inputs']['lora_name']]
        e.save(out / 'workflow.json', graph)
        job = comfyui.api('/prompt', {'prompt': graph})
        e.save(out / 'job.json', job)
        e.need(job.get('prompt_id') and not job.get('node_errors'), '作者背景工作流未通过检查')
        history = None
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            history = comfyui.api('/history/' + job['prompt_id']).get(job['prompt_id'])
            if history:
                break
            time.sleep(.5)
        e.save(out / 'history.json', history)
        e.need(history and history.get('status', {}).get('status_str') == 'success', '背景 GPU 局部重绘失败')
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for key, name in [('7', 'result.png'), ('8', 'raw.png')]:
            descriptor = history['outputs'][key]['images'][0]
            with opener.open(comfyui.config()['endpoint'] + '/view?' + urllib.parse.urlencode(descriptor), timeout=30) as response:
                (out / name).write_bytes(response.read())
        original = Image.open(source).convert('RGB')
        result = Image.open(out / 'result.png').convert('RGB')
        mask = Image.open(out / 'mask.png').convert('RGB')
        assert original.size == result.size == mask.size
        difference = ImageChops.difference(original, result)
        assert ImageChops.multiply(difference, ImageChops.invert(mask)).getbbox() is None
        assert difference.getbbox() is not None
        report.update(protected_pixels_identical=True, acceptance='pending_visual_review')
        e.save(out / 'report.json', report)
        print(out.relative_to(ROOT).as_posix(), flush=True)
    finally:
        manager.stop()
        (out / 'runtime.log').write_text((ROOT / 'data/logs/comfyui.log').read_text(encoding='utf-8', errors='replace')[-25000:], encoding='utf-8')


if __name__ == '__main__':
    main()
