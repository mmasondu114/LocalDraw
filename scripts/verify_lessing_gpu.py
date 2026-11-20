"""Review the quarantined Lessing payload without publishing it to the library."""
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import sys
import time
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from comic_workflow import comfyui, engine as e


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--models', nargs='+', choices=['animagine', 'noob', 'wai'], default=['animagine', 'noob'])
    parser.add_argument('--seeds', nargs='+', type=int, default=[71007, 71008])
    parser.add_argument('--weight', type=float, default=.8)
    parser.add_argument('--precise-outfit', action='store_true')
    args = parser.parse_args()
    paths_config = ROOT / 'config/comfy-model-paths.yaml'
    original_paths_config = paths_config.read_text(encoding='utf-8') if paths_config.exists() else None
    proof = e.read(ROOT / 'data/lora-curation/research/lessing-payload-integrity.json')
    source = ROOT / 'data/backups/lora-download-diagnostic/Lessing-000009.safetensors'
    with source.open('rb') as stream:
        observed = hashlib.file_digest(stream, 'sha256').hexdigest()
    e.need(observed == 'f5c8ff9af743f19b6432cdd73e0c71235a8f4b2a309b5ee12367c743da2158bf', '隔离文件已改变')
    e.need(proof.get('status') == 'payload_and_structure_passed_pending_visual_review', '权重证据未通过')
    diagnostic = ROOT / 'data/comfy-lora-diagnostic'
    staged = diagnostic / '明日方舟/止颂_Lessing-000009.safetensors'
    staged.parent.mkdir(parents=True, exist_ok=True)
    if not staged.exists():
        os.link(source, staged)
    with staged.open('rb') as stream:
        e.need(hashlib.file_digest(stream, 'sha256').hexdigest() == observed, '隔离副本与原文件不一致')
    out = ROOT / 'data/test-results/lessing-gpu' / e.stamp()
    out.mkdir(parents=True)
    reference = ROOT / 'data/lora-curation/research/lessing-official-art'
    shutil.copytree(reference, out / 'official-reference')
    e.save(out / 'source-provenance.json', {
        'source': 'https://civitai.com/models/1429886?modelVersionId=1616200',
        'integrity': proof, 'observed_sha256': observed, 'arguments': vars(args),
        'library_status': 'isolated_not_adopted',
        'trained_base': e.read(ROOT / 'data/lora-curation/sources/lessing-trained-base.json'),
        'reference': 'official-reference/provenance.json',
    })
    positive = (r'lessing\(arknights\), 1boy, solo, male, short dark grey hair, shaggy hair, hair over one eye, '
                'blue eyes, large pale grey curled ram horns, high collar, long black patterned coat, '
                'blue coat lining, blue straps, grey belt, belt pouches, silver buckles, black gloves, '
                'forearm armor, dark trousers, black lace-up boots, large silver zweihander, long sword, '
                'holding sword, full body, head to toe, feet visible, standing, centered, distant view, '
                'Japanese anime, delicate thin lineart, natural colors, detailed clothing, '
                'stone courtyard, daylight, masterpiece, best quality')
    negative = ('worst quality, low quality, extra people, duplicate person, bad anatomy, '
                'extra limbs, bad hands, cropped head, cropped feet, out of frame, girl, white hair, '
                'western comics, thick outlines, text, watermark')
    if args.precise_outfit:
        positive = (r'lessing\(arknights\), 1boy, solo, male, short dark grey shaggy hair, hair over one eye, '
                    'blue eyes, large pale grey twisted ram horns, black high collar, '
                    'long black coat, dark grey coat, geometric diamond pattern, black sleeves, '
                    'silver clasps, grey shoulder straps, grey belt, rectangular belt pouches, '
                    'small blue vertical straps, narrow blue inner coat lining, black gloves, black forearm armor, '
                    'black trousers, tall black lace-up boots, huge silver zweihander sword, holding sword, '
                    'full body, head to toe, feet visible, standing, centered, distant view, '
                    'Japanese anime, delicate thin lineart, natural colors, detailed clothing, '
                    'stone courtyard, daylight, masterpiece, best quality')
        negative += ', blue coat, blue shirt, red necktie, logo, logo print'
    manager = comfyui.ComfyManager()
    records = []
    try:
        for index, model in enumerate(args.models):
            if index:
                manager.stop()
            manager.start(runtime_options={'diagnostic_loras_directory': str(diagnostic)})
            e.save(out / (model + '-gpu-system.json'), comfyui.api('/system_stats'))
            inventory = comfyui.discover()['lora_files']
            name = inventory['明日方舟/止颂_Lessing-000009.safetensors']
            cfg = {**comfyui.config(), 'width': 832, 'height': 1216, 'steps': 28,
                   'cfg': 5.5 if model != 'animagine' else 5.0,
                   'sampler_name': 'euler_ancestral', 'scheduler': 'normal'}
            checkpoint = {'animagine': 'animagine-xl-4.0-opt.safetensors',
                          'noob': 'NoobAI-XL-v1.1.safetensors',
                          'wai': 'waiIllustriousSDXL_v170.safetensors'}[model]
            for seed in args.seeds:
                graph = comfyui.graph(checkpoint, positive, negative, seed, cfg,
                                      [{'file': name, 'weight': args.weight, 'clip_weight': args.weight}])
                key = f'{model}-{seed}'
                e.save(out / (key + '-workflow.json'), graph)
                started = time.monotonic()
                job = comfyui.api('/prompt', {'prompt': graph})
                e.need(not job.get('node_errors'), str(job.get('node_errors')))
                history = None
                while time.monotonic() - started < 900:
                    history = comfyui.api('/history/' + job['prompt_id']).get(job['prompt_id'])
                    if history:
                        break
                    time.sleep(.5)
                e.save(out / (key + '-history.json'), history)
                e.need(history and history.get('status', {}).get('status_str') == 'success', '止颂 GPU 出图失败')
                descriptor = history['outputs']['7']['images'][0]
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(cfg['endpoint'] + '/view?' + urllib.parse.urlencode(descriptor), timeout=30) as response:
                    (out / (key + '.png')).write_bytes(response.read())
                records.append({'key': key, 'checkpoint': checkpoint, 'seed': seed,
                                'seconds': round(time.monotonic() - started, 2), 'acceptance': 'pending_visual_review'})
                e.save(out / 'renders.json', records)
                e.save(ROOT / 'data/test-results/lessing-gpu/latest.json', {'path': str(out), 'acceptance': 'pending_visual_review'})
                print(key + ' 已使用 GPU 生成', flush=True)
    finally:
        manager.stop()
        # The diagnostic weights must not persist in the normal model inventory.
        if original_paths_config is not None:
            paths_config.write_text(original_paths_config, encoding='utf-8')
        else:
            paths_config.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
