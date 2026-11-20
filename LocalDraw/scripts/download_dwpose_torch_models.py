"""Download only the author's documented TorchScript weights, with full SHA256.

HF/CDN truncated transfers remain .partial files and are never loadable models.
Standard HTTP Range resumes are accepted only with matching Content-Range.
"""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import re
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
FILES = (
    ('hr16/yolox-onnx', 'yolox_l.torchscript.pt'),
    ('hr16/DWPose-TorchScript-BatchSize5', 'dw-ll_ucoco_384_bs5.torchscript.pt'),
)


def download(pair):
    repo, name = pair
    reader = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with reader.open('https://huggingface.co/api/models/' + repo + '?blobs=true', timeout=40) as response:
        meta = json.load(response)
    catalog = next(s for s in meta['siblings'] if s['rfilename'] == name)['lfs']
    revision, size, digest = meta['sha'], catalog['size'], catalog['sha256']
    target = ROOT / 'data/pose-detector-diagnostic' / name
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == digest:
        return record(repo, name, revision, size, digest, target)
    partial = target.with_suffix(target.suffix + '.partial')
    for attempt in range(5):
        start = partial.stat().st_size if partial.exists() else 0
        if start == size:
            break
        if start > size:
            raise RuntimeError('未确认的下载文件比作者完整文件更大')
        url = f'https://huggingface.co/{repo}/resolve/{revision}/{name}?download=true&localdraw={time.time_ns()}'
        headers = {'User-Agent': 'LocalDrawResearch/1.0', 'Cache-Control': 'no-cache'}
        if start:
            headers['Range'] = f'bytes={start}-'
        with reader.open(urllib.request.Request(url, headers=headers), timeout=90) as response:
            if response.status == 206:
                match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('Content-Range', ''))
                if not match or int(match[1]) != start or int(match[3]) != size:
                    raise RuntimeError('下载续传范围与作者文件不一致')
            elif response.status == 200:
                start = 0
            else:
                raise RuntimeError('模型下载响应未确认')
            reported = start
            with partial.open('ab' if start else 'wb') as stream:
                while chunk := response.read(1024 * 1024):
                    stream.write(chunk)
                    if stream.tell() - reported >= 32 * 1024 * 1024:
                        reported = stream.tell()
                        print(f'{name}: {reported}/{size} 字节', flush=True)
            print(f'{name}: 第 {attempt + 1} 次传输后 {partial.stat().st_size}/{size} 字节', flush=True)
    if not partial.is_file() or partial.stat().st_size != size or hashlib.sha256(partial.read_bytes()).hexdigest() != digest:
        raise RuntimeError('模型完整 SHA256 校验失败：' + name)
    partial.replace(target)
    print('作者完整 SHA256 校验通过：' + name, flush=True)
    return record(repo, name, revision, size, digest, target)


def record(repo, name, revision, size, digest, path):
    return {'source': 'https://huggingface.co/' + repo, 'revision': revision,
            'original_name': name, 'file': path.relative_to(ROOT).as_posix(), 'sha256': digest, 'bytes': size}


if __name__ == '__main__':
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(download, FILES))
    (ROOT / 'data/lora-curation/research/dwpose-torch-models-verified.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
