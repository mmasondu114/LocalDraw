"""Download studio segmentation/detection models from pinned, hashed sources."""
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    for name in ['person-mask-sam', 'anime-person-mask', 'face-layout']:
        cfg = json.loads((ROOT / 'config' / (name+'.json')).read_text(encoding='utf-8'))
        path = ROOT / cfg['model']
        if path.is_file() and digest(path) == cfg['sha256']:
            print(name, 'verified', flush=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(path.suffix+'.part')
        request = urllib.request.Request(cfg['source'], headers={'User-Agent':'LocalDraw/1.0'})
        with urllib.request.urlopen(request, timeout=90) as response, partial.open('wb') as target:
            while block := response.read(1024*1024):
                target.write(block)
        if digest(partial) != cfg['sha256']:
            raise RuntimeError(name+' model hash mismatch')
        partial.replace(path)
        print(name, 'downloaded and verified', flush=True)


if __name__ == '__main__':
    main()
