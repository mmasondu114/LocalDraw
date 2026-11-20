"""Test cameo-only gap identities in an existing pack on its exact CUDA base."""
import argparse
import hashlib
from pathlib import Path
import shutil
import sys
import time
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from comic_workflow import comfyui, engine as e

DEFINITIONS = {
    '灵知': (r'gnosis_\(arknights\)', '1boy, male, adult man, black hair, red streak in hair, hair over one eye, amber eyes, rectangular tinted glasses, long white coat, black feather collar, black feathered cape, dark turtleneck, orange and blue bead necklace, black belt, waist pouches, black gloves, black trousers, black boots, holding black and red staff'),
    '圣约送葬人': (r'executor_the_ex_foedere_\(arknights\)', '1boy, male, adult man, short white hair, grey blue eyes, black halo, black angel wings, long dark grey coat, white outer coat, red coat lining, red scarf, gold cross badge, black gloves, black trousers, black boots, holding shotgun'),
    '伺夜': (r'vigil_\(arknights\)', '1boy, male, young adult man, short black hair, yellow green eyes, grey wolf ears, grey wolf tail, dark pinstripe suit, white shirt, loose black tie, long dark coat, purple blue coat lining, white fur collar, silver bead necklace, black gloves, black lace-up boots, holding pistol'),
    'Sharp': (r'sharp_\(arknights\)', '1boy, male, middle aged man, muscular, short brown hair, beard, black goggles, headset, beige scarf, black tactical uniform, black armored tactical vest, teal trim, gray combat trousers, black gloves, black combat boots, holding black shield and sword'),
    'Stormeye': (r'stormeye_\(arknights\)', '1boy, male, adult man, muscular, grey slicked back hair, dark animal ears, goggles, black respirator mask, black tactical uniform, black armored tactical vest, teal trim, rolled sleeves, fingerless gloves, black combat trousers, black combat boots, holding compound bow, quiver of arrows'),
    'Pith': (r'pith_\(arknights\)', '1girl, female, adult woman, grey bob hair, teal streak in hair, glasses, face mask, dark hood, long dark grey hooded coat, teal coat lining, black tactical pouches, gray gloves, short black skirt, black pantyhose, black heeled ankle boots, holding magic staff'),
}


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--names', nargs='+', choices=list(DEFINITIONS), default=list(DEFINITIONS))
    parser.add_argument('--seeds', nargs='+', type=int, default=[71007])
    parser.add_argument('--weight', type=float, default=.8)
    parser.add_argument('--no-lora', action='store_true')
    args = parser.parse_args()
    proof = e.read(ROOT / 'data/lora-curation/research/pony-training-base-download.json')
    e.need(proof['complete'] is True, '精确训练底模未校验完整')
    path = next((ROOT / 'models/loras').rglob('*arknight_all_v2.safetensors'))
    with path.open('rb') as stream:
        sha = hashlib.file_digest(stream, 'sha256').hexdigest()
    e.need(sha == '5ce5b527969bb9b2758624f18e00d438b164c06e7942191300b2615805985c7b', '通用模型与原始下载记录不符')
    out = ROOT / 'data/test-results/gap-pack' / e.stamp()
    out.mkdir(parents=True)
    shutil.copytree(ROOT / 'data/lora-curation/research/gap-pack-official-art', out / 'official-references')
    e.save(out / 'source-provenance.json', {
        'source': 'https://civitai.com/models/462775?modelVersionId=536648',
        'pack_file': path.relative_to(ROOT).as_posix(), 'pack_sha256': sha, 'arguments': vars(args),
        'trained_base': proof, 'training_tags': e.read(ROOT / 'data/lora-curation/research/existing-multichar-gap-candidate.json'),
        'limitations': 'The gap tokens occur in multiple-character training images rather than dedicated identity folders. Tags alone do not establish that the pack learned them.',
        'acceptance': 'pending_gpu_visual_review',
    })
    manager = comfyui.ComfyManager()
    records = []
    try:
        manager.start()
        e.save(out / 'gpu-system.json', comfyui.api('/system_stats'))
        inventory = comfyui.discover()['lora_files']
        native = inventory[path.relative_to(ROOT / 'models/loras').as_posix()]
        cfg = {**comfyui.config(), 'width': 832, 'height': 1216, 'steps': 28, 'cfg': 7.,
               'sampler_name': 'euler_ancestral', 'scheduler': 'normal'}
        shared = ('solo, full body, head to toe, feet visible, standing, centered, distant view, '
                  'source_anime, Japanese anime, anime illustration, delicate thin lineart, soft cel shading, '
                  'natural colors, detailed clothing, stone courtyard, daylight')
        negative = ('score_4, score_5, score_6, source_cartoon, western comics, thick outlines, '
                    'extra people, duplicate person, bad anatomy, extra limbs, bad hands, '
                    'cropped head, cropped feet, out of frame, text, watermark, logo')
        for name in args.names:
            trigger, appearance = DEFINITIONS[name]
            prompt = ', '.join(['score_9, score_8_up, score_7_up', trigger, appearance, shared])
            for seed in args.seeds:
                graph = comfyui.graph('ponyDiffusionV6XL_v6StartWithThisOne.safetensors', prompt, negative, seed, cfg,
                                      [] if args.no_lora else [{'file': native, 'weight': args.weight, 'clip_weight': args.weight}])
                key = name + '-' + str(seed)
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
                e.need(history and history.get('status', {}).get('status_str') == 'success', name+' GPU 出图失败')
                descriptor = history['outputs']['7']['images'][0]
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(cfg['endpoint']+'/view?'+urllib.parse.urlencode(descriptor), timeout=30) as response:
                    (out / (key + '.png')).write_bytes(response.read())
                records.append({'name': name, 'key': key, 'seed': seed, 'seconds': round(time.monotonic()-started, 2),
                                'acceptance': 'pending_visual_review'})
                e.save(out / 'renders.json', records)
                e.save(ROOT / 'data/test-results/gap-pack/latest.json', {'path': str(out), 'acceptance': 'pending_visual_review'})
                print(key+' 已使用 GPU 生成', flush=True)
    finally:
        manager.stop()


if __name__ == '__main__':
    main()
