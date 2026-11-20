"""Fetch the requested Krea 2 bundle and character LoRAs with SHA256 verification."""
import concurrent.futures
import contextlib
import hashlib
import json
import re
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/krea2-downloads'
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT / 'src'))
from comic_workflow.downloader import download, filehash


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        result = super().redirect_request(req, fp, code, msg, headers, newurl)
        if result and urllib.parse.urlsplit(newurl).hostname != urllib.parse.urlsplit(req.full_url).hostname:
            result.remove_header('Authorization')
        return result


def open_url(url, headers=None, timeout=40):
    request_headers = {'User-Agent': 'Mozilla/5.0', **(headers or {})}
    token_path = ROOT / 'Chenkin_LoRA/civitai-token.txt'
    if urllib.parse.urlsplit(url).hostname == 'civitai.com' and token_path.exists():
        request_headers['Authorization'] = 'Bearer ' + token_path.read_text().strip()
    return urllib.request.build_opener(SafeRedirect()).open(
        urllib.request.Request(url, headers=request_headers), timeout=timeout)


def fetch(url):
    for attempt in range(4):
        try:
            with open_url(url) as response:
                return json.load(response)
        except Exception:
            if attempt == 3:
                raise RuntimeError('Metadata request failed') from None
            time.sleep(1 + attempt)


def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


def hf_entry(repo, filename, destination, kind, label):
    info = fetch('https://huggingface.co/api/models/' + repo + '?blobs=true')
    item = next(x for x in info['siblings'] if x['rfilename'] == filename)
    return {'kind': kind, 'label': label, 'path': destination, 'size': item['size'],
            'sha256': item['lfs']['sha256'], 'url': 'https://huggingface.co/' + repo + '/resolve/' + info['sha'] + '/' + filename,
            'page': 'https://huggingface.co/' + repo, 'revision': info['sha']}


def prepare():
    bundle = []
    for filename in ('diffusion_models/krea2_turbo_fp8_scaled.safetensors',
                     'text_encoders/qwen3vl_4b_fp8_scaled.safetensors', 'vae/qwen_image_vae.safetensors'):
        bundle.append(hf_entry('Comfy-Org/Krea-2', filename, 'models/' + filename, 'component', Path(filename).name))
    # One current character version per entry; costume variants are separate from identity LoRAs.
    selections = [
        (3004261, '原神/角色', '申鹤', None), (2989156, '原神/角色', '莫娜', None),
        (2984693, '原神/角色', '雷电将军', None), (2982659, '原神/角色', '八重神子', None),
        (968887, '原神/角色', '温迪', None), (2869388, '原神/角色', '桑多涅', None),
        (2887348, '原神/角色', None, ['荧皮肤', '若娜瓦v2', '莱茵多特', '安娜丝塔夏', '沃雅妮莎v2', '微斯纳', '奥黛塔']),
        (2949425, '原神/Cos服装', None, ['沃雅妮莎', '微斯纳', '奥黛塔', '荧', '珊瑚宫心海', '桑多涅', '茜特菈莉', '尼可', '梦见月瑞希', '莉奈娅', '胡桃', '哥伦比亚', '芙宁娜', '八重神子', '爱可菲']),
        (2993516, '原神/Cos服装', '刻晴', None),
        (2974413, '明日方舟/角色', '余', None),
        (2862329, '明日方舟/Cos服装', None, ['夕', '维什戴尔', '凯尔希', '缄默德克萨斯']),
    ]
    def one(selection):
        model_id, folder, label, versions = selection
        info = fetch(f'https://civitai.com/api/v1/models/{model_id}')
        candidates = [v for v in info['modelVersions'] if v['baseModel'] == 'Krea 2']
        chosen = [v for v in candidates if v['name'] in versions] if versions else candidates[:1]
        if versions and len(chosen) != len(versions):
            raise RuntimeError(f'Missing requested versions: {model_id}')
        rows = []
        for version in chosen:
            file = next(f for f in version['files'] if f['type'] == 'Model' and f['name'].endswith('.safetensors'))
            name = label or version['name']
            safe = re.sub(r'[<>:"/\\|?*]', '_', name)
            rows.append({'kind': 'lora', 'label': name, 'model_id': model_id, 'version_id': version['id'],
                'version': version['name'], 'model_name': info['name'], 'base_model': version['baseModel'],
                'path': f'models/loras/krea2/{folder}/{safe}_{version["id"]}.safetensors',
                'size': round(file['sizeKB'] * 1024), 'sha256': file['hashes']['SHA256'].lower(),
                'url': file['downloadUrl'], 'triggers': version.get('trainedWords', []),
                'description': re.sub('<[^>]+>', ' ', info.get('description', '') or ''),
                'version_description': re.sub('<[^>]+>', ' ', version.get('description', '') or ''),
                'page': f'https://civitai.com/models/{model_id}?modelVersionId={version["id"]}',
                'text_files': [{'name': f['name'], 'url': f['downloadUrl']} for f in version['files'] if f['name'].endswith('.txt')]})
        return rows
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for rows in pool.map(one, selections):
            bundle.extend(rows)
    bundle.append(hf_entry('quarterturn/krea2-yoimiya', 'krea2_yoimiya.safetensors',
        'models/loras/krea2/原神/角色/宵宫_krea2.safetensors', 'lora', '宵宫'))
    save(OUT / 'manifest.json', {'as_of': '2026-10-16', 'files': bundle})
    print('Prepared', len(bundle), 'files;', round(sum(x['size'] for x in bundle)/1e9, 2), 'GB', flush=True)


def inspect(path):
    with path.open('rb') as stream:
        length = struct.unpack('<Q', stream.read(8))[0]
        if length > 32 * 1024 * 1024:
            raise ValueError('Invalid tensor header')
        header = json.loads(stream.read(length))
    tensors = {k:v for k,v in header.items() if k != '__metadata__'}
    end = max(v['data_offsets'][1] for v in tensors.values())
    if 8 + length + end != path.stat().st_size:
        raise ValueError('Tensor size mismatch')
    return {'tensors': len(tensors), 'keys_sample': list(tensors)[:4], 'metadata': header.get('__metadata__', {})}


def fetch_file(row):
    target = ROOT / row['path']
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix('.safetensors.partial')
    if target.exists():
        if target.stat().st_size == row['size'] and filehash(target) == row['sha256']:
            return target
        raise RuntimeError('Existing target differs; preserved')
    urls = [row['url']]
    if 'huggingface.co/' in row['url']:
        urls.append(row['url'].replace('huggingface.co/', 'hf-mirror.com/'))
    for attempt in range(5):
        try:
            offset = partial.stat().st_size if partial.exists() else 0
            if offset != row['size']:
                headers = {'Range': f'bytes={offset}-'} if offset else {}
                with open_url(urls[attempt % len(urls)], headers, 40) as response:
                    resumed = response.status == 206 and response.headers.get('Content-Range', '').startswith(f'bytes {offset}-')
                    with partial.open('ab' if resumed else 'wb') as out:
                        while block := response.read(1024 * 1024):
                            out.write(block)
            if partial.stat().st_size != row['size'] or filehash(partial) != row['sha256']:
                raise ValueError('File integrity mismatch')
            partial.replace(target)
            return target
        except Exception as exc:
            print('Retry', row['label'], attempt + 1, type(exc).__name__, flush=True)
            if attempt == 4:
                raise RuntimeError(type(exc).__name__) from None
            time.sleep(2)


def run_group(group):
    rows = json.loads((OUT / 'manifest.json').read_text(encoding='utf-8'))['files']
    rows = [row for row in rows if (row['kind'] == 'component') == (group == 'models')]
    def job(row):
        try:
            if group == 'models':
                target = ROOT / row['path']
                mirror = 'https://modelscope.cn/models/Comfy-Org/Krea-2/resolve/master/' + row['path'].removeprefix('models/')
                target = download(target.parent, 12, mirror, row['size'], row['sha256'], target.name,
                    fallback_urls=[row['url'], row['url'].replace('huggingface.co/', 'hf-mirror.com/')], timeout=25)
            else:
                target = fetch_file(row)
            inspection = inspect(target)
            if row['kind'] == 'lora':
                if not any('lora' in key.lower() for key in inspection['keys_sample']):
                    raise ValueError('Not a LoRA tensor header')
                trigger_text = '\n'.join(row.get('triggers', []))
                for text_file in row.get('text_files', []):
                    try:
                        with open_url(text_file['url']) as response:
                            trigger_text += '\n' + response.read(1024*1024).decode('utf-8-sig')
                    except Exception:
                        trigger_text += '\nSee source page for the author prompt.'
                target.with_suffix('.txt').write_text(trigger_text.strip() or 'See source: ' + row['page'], encoding='utf-8')
                save(target.with_suffix('.source.json'), row)
            result = {**row, 'status': 'verified', 'inspection': inspection}
            save(OUT / 'verified' / (target.stem + '.json'), result)
            print('VERIFIED', row['label'], row['path'], flush=True)
            return result
        except Exception as exc:
            print('FAILED', row['label'], type(exc).__name__, flush=True)
            return {**row, 'status': 'failed', 'error': type(exc).__name__}
    with concurrent.futures.ThreadPoolExecutor(max_workers=2 if group == 'models' else 8) as pool:
        results = list(pool.map(job, rows))
    save(OUT / (group + '-result.json'), results)
    ok = sum(row['status'] == 'verified' for row in results)
    print('SUMMARY', group, ok, '/', len(results), flush=True)
    return 0 if ok == len(results) else 1


if __name__ == '__main__':
    mode = sys.argv[1]
    if mode == 'prepare':
        prepare()
    else:
        with (OUT / (mode + '.log')).open('a', encoding='utf-8', buffering=1) as log:
            with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                raise SystemExit(run_group(mode))
