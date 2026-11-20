"""Resume the pinned, hash-verified native editing model installation."""
import concurrent.futures
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from comic_workflow.downloader import download, filehash

OUT = ROOT / 'data/consistency-upgrade-20261025'


def main():
    rows = json.loads((OUT / 'model-manifest.json').read_text(encoding='utf-8'))
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def fetch(row):
        target = ROOT / 'models' / row['target']
        if target.exists():
            if target.stat().st_size != row['size'] or filehash(target) != row['sha256']:
                raise RuntimeError(f'Preserving unexpected existing model: {target}')
            result = target
        else:
            url = f"https://huggingface.co/{row['repo']}/resolve/{row['revision']}/{row['name']}"
            result = download(target.parent, 8, url, row['size'], row['sha256'], target.name,
                              timeout=45, opener=opener)
        (OUT / (target.name + '.verified.json')).write_text(
            json.dumps({**row, 'path': str(result), 'verified': True}, indent=2), encoding='utf-8')
        return str(result)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        paths = list(pool.map(fetch, rows))
    (OUT / 'models-ready.json').write_text(json.dumps(paths, indent=2), encoding='utf-8')
    print('ALL MODELS SHA256 VERIFIED', flush=True)


if __name__ == '__main__':
    main()
