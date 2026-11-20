"""Download full-precision drawing bundles, pinned and SHA256 verified."""
import concurrent.futures
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from comic_workflow.downloader import download

OUT = ROOT / 'data/krea2-moss-bf16'
SELECTIONS = {
    'Comfy-Org/MOSS-Image': [
        'diffusion_models/moss_image_sft_bf16.safetensors',
        'text_encoders/qwen3.5_9b_bf16.safetensors',
        'vae/flux2-vae.safetensors',
    ],
    'Comfy-Org/Krea-2': [
        'diffusion_models/krea2_turbo_bf16.safetensors',
        'text_encoders/qwen3vl_4b_bf16.safetensors',
        'vae/qwen_image_vae.safetensors',
    ],
}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = OUT / 'manifest.json'
    if manifest.exists():
        rows = json.loads(manifest.read_text(encoding='utf-8'))
    else:
        rows = []
        for repo, names in SELECTIONS.items():
            with urllib.request.urlopen('https://huggingface.co/api/models/' + repo + '?blobs=true', timeout=40) as response:
                info = json.load(response)
            for name in names:
                item = next(s for s in info['siblings'] if s['rfilename'] == name)
                rows.append(dict(repo=repo, name=name, revision=info['sha'], size=item['size'], sha256=item['lfs']['sha256']))
        manifest.write_text(json.dumps(rows, indent=2), encoding='utf-8')

    def job(row):
        target = ROOT / 'models' / row['name']
        url = f"https://huggingface.co/{row['repo']}/resolve/{row['revision']}/{row['name']}"
        mirror = f"https://modelscope.cn/models/{row['repo']}/resolve/master/{row['name']}"
        # Never overwrite a pre-existing model that has an unexpected size.
        if target.exists() and target.stat().st_size != row['size']:
            raise RuntimeError(f'Existing file differs: {target}')
        result = download(target.parent, 12, mirror, row['size'], row['sha256'], target.name,
                          fallback_urls=[url, url.replace('huggingface.co/', 'hf-mirror.com/')], timeout=30)
        (OUT / (target.name + '.verified.json')).write_text(json.dumps({**row, 'path': str(result), 'verified': True}, indent=2), encoding='utf-8')
        return str(result)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(job, rows))
    (OUT / 'complete.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
    print('ALL FILES SHA256 VERIFIED', flush=True)


if __name__ == '__main__':
    main()
