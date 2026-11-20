"""Download the pinned Delta Mix v6 checkpoint and verify shared components."""
import contextlib
import json
from download_krea2_bundle import ROOT, inspect, save, download, filehash, fetch

OUT = ROOT / 'data/delta-mix-v6'


def main():
    version = json.loads((OUT / 'version.json').read_text(encoding='utf-8'))
    entry = next(f for f in version['files'] if f['type'] == 'Model')
    size = round(entry['sizeKB'] * 1024)
    sha = entry['hashes']['SHA256'].lower()
    repo = 'mogaru99/animaDeltaMix38BQwen0_deltaMixV60'
    mirror = fetch('https://huggingface.co/api/models/' + repo + '?blobs=true')
    mirror_file = next(f for f in mirror['siblings'] if f['rfilename'] == entry['name'])
    assert mirror_file['lfs']['sha256'] == sha and mirror_file['size'] == size
    url = 'https://huggingface.co/' + repo + '/resolve/' + mirror['sha'] + '/' + entry['name']
    target = download(ROOT / 'models/diffusion_models', 16, url, size, sha, entry['name'],
                      fallback_urls=[url.replace('huggingface.co/', 'hf-mirror.com/')], timeout=30)
    result = {'version_id': version['id'], 'path': str(target), 'size': size,
              'sha256': sha, 'mirror_url': url, 'inspection': inspect(target), 'shared_components': []}
    components = json.loads((ROOT / 'config/anima-model.json').read_text(encoding='utf-8'))['files']
    for f in components:
        if f['name'] not in ('qwen_3_06b_base.safetensors', 'qwen_image_vae.safetensors'):
            continue
        path = ROOT / f['directory'] / f['name']
        assert path.stat().st_size == f['size'] and filehash(path) == f['sha256'], f['name']
        result['shared_components'].append({**f, 'verified': True})
    result['status'] = 'verified'
    save(OUT / 'download-result.json', result)
    print('Delta Mix v6 and both shared components verified.', flush=True)


if __name__ == '__main__':
    with (OUT / 'download.log').open('a', encoding='utf-8', buffering=1) as log:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            main()
