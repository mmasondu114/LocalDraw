"""Check corrected Touch and Ch'en weights against complete official game art."""
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from comic_workflow import comfyui, engine as e, regional_lora


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    roster = e.read(ROOT / 'data/lora-curation/selection.json')['characters']
    audit = e.read(ROOT / 'data/lora-curation/library-audit.json')['characters']
    out = ROOT / 'data/test-results/corrected-identities-gpu' / e.stamp()
    out.mkdir(parents=True)
    descriptions = {
        'Touch': ('1girl, grey hair, yellow eyes, cow ears, black cow horns through black hat, '
                  'orange tinted goggles over eyes, headset, white scarf, open grey long coat, '
                  'black tactical vest, waist pouches, dark skirt, black pantyhose, black ankle boots, gloves'),
        '陈': ('1girl, long blue hair, low twintails, red eyes, dragon horns, dragon tail, '
              'open dark jacket with white sleeve stripes, white collared shirt, yellow necktie, '
              'fingerless black gloves, black shorts, black shin guards, black boots'),
    }
    shared = ('solo, full body, head to toe, feet visible, standing, centered, distant view, '
              'Japanese anime, anime illustration, delicate thin lineart, natural colors, '
              'detailed clothing, stone courtyard, daylight, masterpiece, best quality')
    negative = ('worst quality, low quality, extra people, duplicate person, bad anatomy, '
                'extra limbs, bad hands, cropped head, cropped feet, out of frame, '
                'western comics, thick outlines, text, watermark')
    manager = comfyui.ComfyManager()
    records = []
    try:
        manager.start()
        e.save(out / 'gpu-system.json', comfyui.api('/system_stats'))
        cfg = {**comfyui.config(), 'width': 832, 'height': 1216, 'steps': 28,
               'cfg': 5.5, 'sampler_name': 'euler_ancestral', 'scheduler': 'normal'}
        inventory = comfyui.discover()['lora_files']
        for name in descriptions:
            row = next(r for r in roster if r['character']['name'] == name)
            selected = row['selected']
            e.need(selected and any(r['name'] == name and r['status'] == 'verified' for r in audit),
                   name + '尚未通过完整文件校验')
            prompt = ', '.join([selected['triggers'][0], descriptions[name], shared])
            e.save(out / (name + '-provenance.json'), {
                'character': row['character'], 'weight': selected,
                'official_reference': 'data/lora-curation/research/corrected-identities-official-art/provenance.json',
                'gpu_only': True, 'acceptance': 'pending_visual_review',
            })
            for seed in (71007, 71008):
                graph = comfyui.graph('waiIllustriousSDXL_v170.safetensors', prompt, negative,
                                      seed, cfg, [{'file': inventory[selected['relative_path']],
                                                   'weight': .7, 'clip_weight': .7}])
                pose = regional_lora.standing_pose([{'region': [0, 0, 1, 1]}],
                                                  {**cfg, 'regional_pose_margin': .18})
                regional_lora.apply_pose(graph, pose, cfg)
                graph['280']['inputs'].pop('areas', None)
                key = f'{name}-{seed}'
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
                e.need(history and history.get('status', {}).get('status_str') == 'success',
                       name + ' GPU 出图失败')
                descriptor = history['outputs']['7']['images'][0]
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(cfg['endpoint'] + '/view?' + urllib.parse.urlencode(descriptor), timeout=30) as response:
                    (out / (key + '.png')).write_bytes(response.read())
                records.append({'name': name, 'key': key, 'seconds': round(time.monotonic() - started, 2),
                                'acceptance': 'pending_visual_review'})
                e.save(out / 'renders.json', records)
                e.save(ROOT / 'data/test-results/corrected-identities-gpu/latest.json',
                       {'path': str(out), 'acceptance': 'pending_visual_review'})
                print(key + ' 已使用 GPU 生成', flush=True)
    finally:
        manager.stop()


if __name__ == '__main__':
    main()
