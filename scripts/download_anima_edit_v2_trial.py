"""Download the pinned public Edit v2 weight in validated HTTP ranges."""
import concurrent.futures
import hashlib
import json
import math
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/test-results/delta-v6-anima-edit-v2-20261018'
info = json.loads((OUT / 'source-version.json').read_text(encoding='utf-8-sig'))
entry = next(f for f in info['files'] if f['name'] == 'AnimeEditV2.safetensors')
size = round(entry['sizeKB'] * 1024)
digest = entry['hashes']['SHA256'].lower()
target = ROOT / 'models/loras/anime/edit' / entry['name']
parts = OUT / 'download-parts'
parts.mkdir(exist_ok=True)
chunk = 4 * 1024 * 1024

def part(index):
    start, end = index * chunk, min((index + 1) * chunk, size) - 1
    path = parts / str(index)
    if path.exists() and path.stat().st_size == end - start + 1:
        return path
    for attempt in range(4):
        try:
            req = urllib.request.Request(entry['downloadUrl'], headers={
                'User-Agent': 'Mozilla/5.0', 'Range': f'bytes={start}-{end}', 'Cache-Control': 'no-cache'})
            with urllib.request.urlopen(req, timeout=25) as response:
                expected = f'bytes {start}-{end}/{size}'
                if response.status != 206 or response.headers.get('Content-Range') != expected:
                    raise ValueError('Incorrect range: ' + str(response.headers.get('Content-Range')))
                data = response.read(end - start + 2)
            if len(data) != end - start + 1:
                raise ValueError('Incomplete range')
            path.write_bytes(data)
            return path
        except Exception as exc:
            print('Part', index, 'attempt', attempt + 1, type(exc).__name__, flush=True)
            if attempt == 3:
                raise
            time.sleep(1)

if not target.exists():
    count = math.ceil(size / chunk)
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(part, i) for i in range(count)]
        for done, future in enumerate(concurrent.futures.as_completed(futures), 1):
            future.result()
            print(f'Parts {done}/{count}', flush=True)
    assembled = target.with_suffix('.assembling')
    with assembled.open('wb') as stream:
        for index in range(count):
            stream.write((parts / str(index)).read_bytes())
    assert assembled.stat().st_size == size
    assert hashlib.sha256(assembled.read_bytes()).hexdigest() == digest
    assembled.replace(target)
assert target.stat().st_size == size
assert hashlib.sha256(target.read_bytes()).hexdigest() == digest
(OUT / 'download-verified.json').write_text(json.dumps({'path': str(target), 'bytes': size,
    'sha256': digest, 'source': entry['downloadUrl']}, indent=2), encoding='utf-8')
print('SHA256 verified:', target, flush=True)
