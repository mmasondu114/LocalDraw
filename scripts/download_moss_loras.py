"""Download pinned MOSS SFT adapters and verify publisher SHA-256 hashes."""
import concurrent.futures
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from comic_workflow.downloader import download
from download_krea2_bundle import inspect

OUT = ROOT / 'data/moss-loras'
SELECTIONS = [
    ('akhaliq/MOSS-Image-SFT-4-steps', 'moss_image_sft_4step_v2.0.safetensors', '加速/4步加速_AK-v2.safetensors', '', 1.0, 4, 1.0),
    ('lodestones/MOSS-Image-SFT-8-steps-dmd', 'lora_latest.safetensors', '加速/8步加速_DMD-latest.safetensors', '', 0.7, 8, 1.0),
    ('axiomofmind/Moss-image-anime-lora', 'MOSS-Image-Anime-LoRA-ComfyUI.safetensors', '画风/青年动漫_Anime.safetensors', 'anime character', 1.0, 30, 3.0),
    ('axiomofmind/Moss-image-animated-lora', 'MOSS-Image-Animated-LoRA-ComfyUI.safetensors', '画风/3D动画_Animated.safetensors', '', 0.7, 30, 3.0),
    ('axiomofmind/Moss-image-beauty-lora', 'MOSS-Image-Beauty-LoRA-ComfyUI.safetensors', '画风/人像摄影_Beauty.safetensors', 'moss_beauty_style', 1.0, 30, 3.0),
    ('axiomofmind/Moss-image-gouache-lora', 'MOSS-Image-Gouache-LoRA-ComfyUI.safetensors', '画风/复古水粉_Gouache.safetensors', 'moss_vgouache_style', 1.0, 30, 3.0),
    ('axiomofmind/Moss-image-psychedelic-lora', 'MOSS-Image-Psychedelic-LoRA-ComfyUI.safetensors', '画风/迷幻波普_Psychedelic.safetensors', 'moss_psychpop_style', 1.0, 30, 3.0),
    ('Kandoo/MOSS-Image-Impressionism-LoRA', 'moss-image-impressionism-lora.safetensors', '画风/印象派油画_Impressionism.safetensors', '', 1.0, 50, 3.0),
]


def main():
    rows = []
    for repo, filename, dest, trigger, strength, steps, cfg in SELECTIONS:
        info = json.loads((OUT / (repo.replace('/', '--') + '.json')).read_text(encoding='utf-8'))
        entry = next(s for s in info['siblings'] if s['rfilename'] == filename)
        rows.append(dict(repo=repo, filename=filename, revision=info['sha'],
                         size=entry['size'], sha256=entry['lfs']['sha256'],
                         path='models/loras/moss_image/' + dest, trigger=trigger,
                         strength=strength, steps=steps, cfg=cfg, sampler='euler', scheduler='simple',
                         page='https://huggingface.co/' + repo))
    (OUT / 'manifest.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Downloading {len(rows)} adapters: {sum(r["size"] for r in rows)/1e9:.2f} GB', flush=True)

    def job(row):
        target = ROOT / row['path']
        url = f'https://huggingface.co/{row["repo"]}/resolve/{row["revision"]}/{row["filename"]}'
        if target.exists() and target.stat().st_size != row['size']:
            raise RuntimeError(f'Existing file differs: {target}')
        download(target.parent, 24, url, row['size'], row['sha256'], target.name,
                 fallback_urls=[url.replace('huggingface.co/', 'hf-mirror.com/')], timeout=30)
        details = inspect(target)
        result = {**row, 'verified': True, 'inspection': details}
        target.with_suffix('.source.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        target.with_suffix('.txt').write_text(row['trigger'], encoding='utf-8')
        (OUT / (target.stem + '.verified.json')).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print('VERIFIED ' + target.name, flush=True)
        return result

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(job, rows))
    (OUT / 'complete.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print('ALL VERIFIED', flush=True)


if __name__ == '__main__':
    main()
