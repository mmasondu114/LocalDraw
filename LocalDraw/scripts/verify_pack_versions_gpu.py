"""Compare two published pack versions against official default character art."""
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

APPEARANCES = {
    '黑键': (r'ebenholz_\(arknights\)', '1boy, young adult man, pale skin, wavy black hair, white hair streak, purple eyes, two dark orange twisted goat horns, dark long cape, black formal jacket, gold shoulder epaulettes, white cravat, purple brooch, white gloves, dark pinstripe trousers, black boots'),
    '棘刺': (r'thorns_\(arknights\)', '1boy, adult man, tan skin, messy short black hair, amber eyes, white long hooded coat, black inner shirt, gold sun brooch, necklace, white gloves, white trousers rolled up at calves, black lace-up boots, black and gold sword'),
}


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    proof = e.read(ROOT / 'data/lora-curation/research/pony-training-base-download.json')
    e.need(proof['complete'] is True, '作者训练底模未完整校验')
    selected = e.read(ROOT / 'data/lora-curation/selection.json')['characters']
    row = next(r for r in selected if r['character']['name'] == '黑键')
    versions = []
    for candidate in row['candidates']:
        path = ROOT / 'models/loras' / candidate['relative_path']
        if not path.exists():
            path = next((ROOT / 'models/loras').rglob('*_' + candidate['original_name']))
        with path.open('rb') as stream:
            e.need(hashlib.file_digest(stream, 'sha256').hexdigest() == candidate['sha256'], '候选完整 SHA256 不符')
        versions.append((candidate, path))
    e.need(len(versions) == 2, '对照候选未明确')
    out = ROOT / 'data/test-results/pack-version-comparison' / e.stamp()
    out.mkdir(parents=True)
    shutil.copytree(ROOT / 'data/lora-curation/research/pack-comparison-official-art', out / 'official-references')
    e.save(out / 'source-provenance.json', {
        'complete_model_checksums_verified': True, 'exact_training_base': proof,
        'candidates': [{'page': c['page'], 'version_id': c['version_id'], 'file': p.relative_to(ROOT).as_posix(), 'sha256': c['sha256']} for c, p in versions],
        'official_references': e.read(out / 'official-references/provenance.json'),
        'seed_pairs': [71007, 71008], 'weight': .8, 'reference_adapters_used': False,
        'scope': 'Identical base, prompts and seeds compare actual candidate identity learning; reference art is inspected by the reviewer, not injected into either candidate.',
        'gpu_only': True, 'acceptance': 'pending_visual_review', 'production_defaults_changed': False})
    manager = comfyui.ComfyManager()
    records = []
    try:
        manager.start()
        e.save(out / 'gpu-system.json', comfyui.api('/system_stats'))
        inventory = comfyui.discover()['lora_files']
        cfg = {**comfyui.config(), 'width': 832, 'height': 1216, 'steps': 28, 'cfg': 7., 'sampler_name': 'euler_ancestral', 'scheduler': 'normal'}
        common = 'solo, full body, head to toe, feet visible, standing, centered, distant view, source_anime, Japanese anime, anime illustration, thin lineart, soft cel shading, stone courtyard, daylight'
        negative = 'score_4, score_5, score_6, source_cartoon, western comics, thick outlines, extra people, duplicate person, bad anatomy, extra limbs, bad hands, cropped head, cropped feet, out of frame, text, watermark, logo'
        for name, (trigger, appearance) in APPEARANCES.items():
            prompt = ', '.join(['score_9, score_8_up, score_7_up', trigger, appearance, common])
            for candidate, path in versions:
                native = inventory[path.relative_to(ROOT / 'models/loras').as_posix()]
                for seed in (71007, 71008):
                    graph = comfyui.graph('ponyDiffusionV6XL_v6StartWithThisOne.safetensors', prompt, negative, seed, cfg, [{'file': native, 'weight': .8, 'clip_weight': .8}])
                    key = f"{name}-{candidate['version_id']}-{seed}"
                    e.save(out / (key + '-workflow.json'), graph)
                    job = comfyui.api('/prompt', {'prompt': graph})
                    e.save(out / (key + '-job.json'), job)
                    e.need(not job.get('node_errors'), '候选节点未接受参数')
                    started = time.monotonic()
                    history = None
                    while time.monotonic() - started < 900:
                        history = comfyui.api('/history/' + job['prompt_id']).get(job['prompt_id'])
                        if history:
                            break
                        time.sleep(.5)
                    e.save(out / (key + '-history.json'), history)
                    e.need(history and history.get('status', {}).get('status_str') == 'success', '候选 GPU 执行失败；禁止 CPU 回退')
                    descriptor = history['outputs']['7']['images'][0]
                    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                    with opener.open(cfg['endpoint'] + '/view?' + urllib.parse.urlencode(descriptor), timeout=30) as response:
                        (out / (key + '.png')).write_bytes(response.read())
                    records.append({'name': name, 'version_id': candidate['version_id'], 'seed': seed, 'file': key + '.png', 'acceptance': 'pending_visual_review'})
                    e.save(out / 'renders.json', records)
                    print(key + ' 已使用 GPU 生成', flush=True)
        e.save(ROOT / 'data/test-results/pack-version-comparison/latest.json', {'path': str(out), 'acceptance': 'pending_visual_review'})
    finally:
        manager.stop()
        (out / 'runtime.log').write_text((ROOT / 'data/logs/comfyui.log').read_text(encoding='utf-8', errors='replace')[-18000:], encoding='utf-8')


if __name__ == '__main__':
    main()
