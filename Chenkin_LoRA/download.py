"""Download Chenkin's Genshin / Arknights LoRAs with aria2 and persistent resume."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
import datetime as dt
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent
CATEGORIES = ('genshin', 'arknights')
SDXL_FAMILY = {'SDXL 1.0', 'Pony', 'Illustrious', 'NoobAI'}
ARIA_EXE = ROOT / 'tools' / 'aria2-1.36.0' / 'aria2c.exe'


def atomic_json(path, value):
    temp = path.with_suffix(path.suffix + '.tmp')
    with temp.open('w', encoding='utf-8') as out:
        json.dump(value, out, ensure_ascii=False, indent=2)
        out.flush()
        os.fsync(out.fileno())
    os.replace(temp, path)


def categories(model):
    if model.get('type', '').upper() != 'LORA':
        return []
    if model.get('creator', {}).get('username', '').casefold() != 'chenkin':
        return []
    text = ' '.join([model.get('name', '')] + model.get('tags', [])).casefold()
    return [cat for cat in CATEGORIES if cat in text]


def safe_name(name):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip(' .')
    stem, ext = os.path.splitext(name)
    return (stem[:65] or 'model') + ext[:16]


def make_manifest(catalog, family='sdxl-family'):
    entries, seen = [], set()
    for model in catalog['items']:
        for cat in categories(model):
            for version in model.get('modelVersions', []):
                base = version.get('baseModel')
                if family == 'sdxl-family' and base not in SDXL_FAMILY:
                    continue
                if family == 'sdxl' and base != 'SDXL 1.0':
                    continue
                for file in version.get('files', []):
                    if file.get('type') != 'Model':
                        continue
                    key = (cat, model['id'], version['id'], file['id'])
                    if key in seen:
                        continue
                    seen.add(key)
                    sha = file.get('hashes', {}).get('SHA256', '').lower()
                    if not re.fullmatch(r'[0-9a-f]{64}', sha):
                        raise ValueError(f"文件 {file['id']} 缺少有效 SHA256，无法保证完整性。")
                    url = file['downloadUrl']
                    parsed = urllib.parse.urlsplit(url)
                    if parsed.scheme != 'https' or parsed.netloc != 'civitai.com' or not parsed.path.startswith('/api/download/models/'):
                        raise ValueError('清单包含非 Civitai 下载地址。')
                    name = f"m{model['id']}_v{version['id']}_f{file['id']}_{safe_name(file['name'])}"
                    entries.append(dict(category=cat, model_id=model['id'], model=model['name'],
                        version_id=version['id'], version=version['name'], base_model=version.get('baseModel'),
                        file_id=file['id'], original_name=file['name'], relative_path=f'{cat}/{name}',
                        bytes=round(file.get('sizeKB', 0) * 1024), sha256=sha, url=url,
                        page=f"https://civitai.com/models/{model['id']}?modelVersionId={version['id']}"))
    return entries


def save_manifest(entries):
    atomic_json(ROOT / 'manifest.json', entries)
    with (ROOT / '清单.csv').open('w', encoding='utf-8-sig', newline='') as out:
        writer = csv.DictWriter(out, fieldnames=list(entries[0]) if entries else ['category'])
        writer.writeheader()
        writer.writerows(entries)


def refresh_catalog(opener, token):
    url = 'https://civitai.com/api/v1/models?' + urllib.parse.urlencode(
        dict(username='Chenkin', types='LORA', limit=20, nsfw='true'))
    seen, all_models = set(), {}
    page = 0
    while url:
        if url in seen:
            raise RuntimeError('目录分页重复，已保留旧清单。')
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != 'https' or parsed.netloc != 'civitai.com' or parsed.path != '/api/v1/models':
            raise RuntimeError('目录分页地址异常，已保留旧清单。')
        seen.add(url)
        headers = {'User-Agent': 'Chenkin-LoRA-Downloader/1.0', 'Accept': 'application/json'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        for attempt in range(4):
            try:
                with opener.open(urllib.request.Request(url, headers=headers), timeout=60) as response:
                    data = json.load(response)
                break
            except (OSError, ValueError):
                if attempt == 3:
                    raise RuntimeError('联网更新目录失败；旧目录未改动。可稍后用 --refresh 重试。') from None
                time.sleep(3 * (attempt + 1))
        if not isinstance(data.get('items'), list):
            raise RuntimeError('服务器没有返回模型目录，旧目录未改动。')
        for model in data['items']:
            if model.get('creator', {}).get('username', '').casefold() == 'chenkin':
                all_models[model['id']] = model
        page += 1
        print(f'目录更新：第 {page} 页，共 {len(all_models)} 个作者模型。', flush=True)
        url = data.get('metadata', {}).get('nextPage')
        cursor = data.get('metadata', {}).get('nextCursor')
        if not url and cursor:
            url = 'https://civitai.com/api/v1/models?' + urllib.parse.urlencode(
                dict(username='Chenkin', types='LORA', limit=20, nsfw='true', cursor=cursor))
    if not all_models:
        raise RuntimeError('作者目录为空，保留旧清单。')
    selected = []
    for model in all_models.values():
        if not categories(model):
            continue
        model.pop('description', None)
        for version in model.get('modelVersions', []):
            version.pop('images', None)
            version.pop('description', None)
        selected.append(model)
    result = dict(author='Chenkin', fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                  catalog_total_models=len(all_models), items=selected)
    make_manifest(result)  # Validate everything before replacing the previous complete catalog.
    atomic_json(ROOT / 'catalog.json', result)
    return result


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as src:
        for chunk in iter(lambda: src.read(4 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def pending_files(entries):
    pending = []
    for entry in entries:
        path = ROOT / entry['relative_path']
        control = Path(str(path) + '.aria2')
        temporary_control = Path(str(control) + '__temp')
        if path.exists() and digest(path) == entry['sha256']:
            control.unlink(missing_ok=True)
            temporary_control.unlink(missing_ok=True)
            print('已校验，跳过：' + entry['relative_path'], flush=True)
            continue
        if path.exists() and not control.exists() and temporary_control.exists():
            # Windows may interrupt aria2 between flushing and renaming its control file.
            os.replace(temporary_control, control)
        if not path.exists() or control.exists():
            pending.append(entry)
            continue
        # Preserve incomplete/corrupt data if a sudden outage lost the control file.
        backup = ROOT / '_damaged' / (path.name + '.' + str(time.time_ns()))
        backup.parent.mkdir(exist_ok=True)
        path.replace(backup)
        pending.append(entry)
        print('无续传记录或校验不符，已保留旧文件并重新下载：' + path.name, flush=True)
    return pending


def with_token(url, token):
    if not token:
        return url
    p = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(p.query, keep_blank_values=True)
    query = [(k, v) for k, v in query if k != 'token'] + [('token', token)]
    return urllib.parse.urlunsplit(p._replace(query=urllib.parse.urlencode(query)))


def redact(line, token):
    if token:
        line = line.replace(token, '[KEY]').replace(urllib.parse.quote_plus(token), '[KEY]')
    return re.sub(r'https?://\S+', '[download-url]', line)


def aria_input(entries, token):
    lines = []
    for entry in entries:
        path = ROOT / entry['relative_path']
        lines.extend([with_token(entry['url'], token), '  dir=' + str(path.parent),
                      '  out=' + path.name, '  checksum=sha-256=' + entry['sha256']])
    return '\n'.join(lines) + '\n'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def resolve_download(entry, token, proxy):
    """Authenticate only at Civitai; hand aria2 a temporary CDN URL without our API key."""
    if urllib.parse.urlsplit(entry['url']).netloc != 'civitai.com':
        return entry  # Used by the offline HTTP integration test.
    opener = urllib.request.build_opener(NoRedirect, urllib.request.ProxyHandler(
        {'http': proxy, 'https': proxy} if proxy else {}))
    headers = {'User-Agent': 'Chenkin-LoRA-Downloader/1.1', 'Range': 'bytes=0-0'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    for attempt in range(3):
        try:
            try:
                with opener.open(urllib.request.Request(entry['url'], headers=headers), timeout=25):
                    raise RuntimeError('未返回模型文件的下载跳转地址')
            except urllib.error.HTTPError as error:
                if error.code in (301, 302, 303, 307, 308):
                    url = urllib.parse.urljoin(entry['url'], error.headers.get('Location', ''))
                    parsed = urllib.parse.urlsplit(url)
                    if parsed.scheme != 'https' or not parsed.hostname or parsed.netloc == 'civitai.com' or any(c in url for c in '\r\n'):
                        raise RuntimeError('下载跳转地址不符合预期') from None
                    return {**entry, 'url': url}
                if error.code in (401, 403, 404):
                    raise RuntimeError(f'HTTP {error.code}，请检查 Key、下载权限或版本是否仍可用') from None
                raise
        except RuntimeError:
            raise
        except (OSError, ValueError):
            if attempt == 2:
                raise RuntimeError('获取下载地址失败，可重新运行续传') from None
            time.sleep(2 * (attempt + 1))


def resolve_downloads(entries, token, proxy):
    print('正在获取临时下载地址；API Key 仅发送给 Civitai 登录接口。', flush=True)
    ready = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(resolve_download, entry, token, proxy): i for i, entry in enumerate(entries)}
        for done, future in enumerate(as_completed(futures), 1):
            index = futures[future]
            try:
                ready[index] = future.result()
            except RuntimeError as error:
                print('暂未取得地址：' + entries[index]['relative_path'] + '；' + str(error), flush=True)
            if done % 10 == 0 or done == len(entries):
                print(f'下载地址准备：{done}/{len(entries)}，成功 {len(ready)}。', flush=True)
    return [ready[i] for i in sorted(ready)]


def download(entries, token, args, proxy):
    # Pin the verified official build: the 1.37.0 Windows build can fail to rename
    # control files (upstream aria2/aria2#2138), breaking periodic resume saves.
    if not ARIA_EXE.exists():
        raise RuntimeError('未找到已验证的 aria2 1.36.0，请保留随附的 tools 文件夹。')
    exe = str(ARIA_EXE)
    entries = resolve_downloads(entries, token, proxy)
    if not entries:
        return 1
    command = [exe, '--no-conf=true', '--input-file=-', '--continue=true',
        '--check-integrity=true', '--always-resume=true', '--allow-overwrite=false',
        '--auto-file-renaming=false', '--file-allocation=none', '--auto-save-interval=5',
        f'--max-concurrent-downloads={args.jobs}', f'--split={args.connections}',
        f'--max-connection-per-server={args.connections}', '--min-split-size=2M',
        '--max-tries=8', '--retry-wait=10', '--connect-timeout=25', '--timeout=60',
        '--max-file-not-found=2', '--summary-interval=15', '--console-log-level=warn',
        '--download-result=hide', '--enable-color=false', '--show-console-readout=false',
        '--check-certificate=true']
    if proxy:
        # Passed over stdin, not recorded in process arguments or saved session files.
        config = aria_input(entries, '')
        config = config.replace('\n  dir=', '\n  all-proxy=' + proxy + '\n  dir=')
    else:
        config = aria_input(entries, '')
    print(f'开始下载：{len(entries)} 个文件，{args.jobs} 文件并行，每文件最多 {args.connections} 连接。', flush=True)
    logpath = ROOT / 'download.log'
    with logpath.open('a', encoding='utf-8') as log:
        log.write('\nSTART ' + dt.datetime.now().isoformat() + '\n')
        child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT)
        try:
            child.stdin.write(config.encode('utf-8'))
            child.stdin.close()
            for raw in child.stdout:
                line = redact(raw.decode('utf-8', errors='replace'), token)
                print(line, end='', flush=True)
                log.write(line)
                log.flush()
            return child.wait()
        except KeyboardInterrupt:
            # The console Ctrl+C reaches aria2 too, allowing its control file to be saved.
            print('\n正在保留下载进度……', flush=True)
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.terminate()
                child.wait()
            raise
        finally:
            child.stdout.close()


def get_token(no_prompt):
    token = os.environ.get('CIVITAI_API_KEY', '').strip()
    tokenfile = ROOT / 'civitai-token.txt'
    if not token and tokenfile.exists():
        token = tokenfile.read_text(encoding='utf-8-sig').strip()
    if not token and not no_prompt:
        print('Civitai 下载通常要求 API Key。可在 https://civitai.com/user/account 创建。')
        print('下方隐藏输入，不回显、不保存；直接回车尝试匿名下载。')
        token = getpass.getpass('Civitai API Key: ').strip()
    if any(ord(c) < 32 for c in token):
        raise ValueError('API Key 含换行等无效字符。')
    return token


def download_with_retries(entries, pending, token, args, proxy):
    result = 1
    for attempt in range(1, 4):
        if attempt > 1:
            print(f'仍有 {len(pending)} 个文件未完成，10 秒后开始第 {attempt}/3 轮续传。', flush=True)
            time.sleep(10)
            pending = pending_files(pending)
        if pending:
            result = download(pending, token, args, proxy)
        failures = []
        for entry in entries:
            path = ROOT / entry['relative_path']
            if not path.exists() or digest(path) != entry['sha256']:
                failures.append(entry)
            else:
                Path(str(path) + '.aria2').unlink(missing_ok=True)
                Path(str(path) + '.aria2__temp').unlink(missing_ok=True)
        atomic_json(ROOT / 'unfinished.json', failures)
        print(f'校验通过：{len(entries) - len(failures)}/{len(entries)}。', flush=True)
        if not failures:
            return 0, []
        pending = failures
    return result or 1, failures


def run(args):
    for cat in CATEGORIES:
        (ROOT / cat).mkdir(exist_ok=True)
    token = '' if args.list and not args.refresh else get_token(args.no_prompt)
    proxies = urllib.request.getproxies()
    proxy = args.proxy or proxies.get('https') or proxies.get('http')
    if proxy:
        parsed = urllib.parse.urlsplit(proxy)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or any(c in proxy for c in '\r\n'):
            raise ValueError('请提供 HTTP / Mixed 代理地址，例如 --proxy http://127.0.0.1:7890')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({'http': proxy, 'https': proxy} if proxy else {}))
    catalog = refresh_catalog(opener, token) if args.refresh else json.loads((ROOT / 'catalog.json').read_text(encoding='utf-8'))
    entries = make_manifest(catalog, args.family)
    if not entries:
        raise RuntimeError('未找到符合条件的 LoRA。')
    save_manifest(entries)
    print('目录时间：' + catalog['fetched_at'])
    for cat in CATEGORIES:
        rows = [e for e in entries if e['category'] == cat]
        print(f"{cat}: {len(set(e['model_id'] for e in rows))} 个模型 / {len(rows)} 个文件 / {sum(e['bytes'] for e in rows) / 1024**3:.2f} GiB")
    print('基础模型筛选：' + {'sdxl-family': 'SDXL 1.0 / Pony / Illustrious / NoobAI', 'sdxl': '仅 SDXL 1.0', 'all': '所有基础模型'}[args.family])
    print('包含筛选范围内的历史版本；清单.csv 中可查看每个文件所需的基础模型。', flush=True)
    if args.list:
        return 0
    pending = pending_files(entries)
    if not pending:
        print('全部文件已完成，并通过 SHA256 校验。')
        return 0
    needed = sum(max(0, e['bytes'] - ((ROOT / e['relative_path']).stat().st_size if (ROOT / e['relative_path']).exists() else 0)) for e in pending)
    if shutil.disk_usage(ROOT).free < needed + 256 * 1024**2:
        raise RuntimeError('剩余磁盘空间不足，请腾出空间后重试。')
    if not token:
        try:
            request = urllib.request.Request(pending[0]['url'], headers={'Range': 'bytes=0-0', 'User-Agent': 'Chenkin-LoRA-Downloader/1.0'})
            with opener.open(request, timeout=25) as response:
                response.read(1)
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                print('Civitai 要求登录。请重新运行 BAT 并输入 API Key；或保存到同目录 civitai-token.txt（仅一行）。')
                return 3
            print(f'预检查 HTTP {error.code}；由 aria2 继续尝试各文件。')
        except OSError:
            print('预检查连接失败；由 aria2 重试各文件。')
    result, failures = download_with_retries(entries, pending, token, args, proxy)
    if failures:
        print(f'尚有 {len(failures)} 个文件未完成，详见 unfinished.json。重新运行同一个 BAT 可继续。')
        print('401/403 通常表示 Key 或下载权限问题；404/付费或已删除版本不会被伪报为成功。')
        return result or 1
    print('全部文件已下载完成，并通过 SHA256 校验。')
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--list', action='store_true', help='只导出清单，不下载')
    parser.add_argument('--refresh', action='store_true', help='重新遍历作者在线目录；成功后替换本地目录')
    parser.add_argument('--no-prompt', action='store_true', help='只使用环境变量/本地 Key 文件')
    parser.add_argument('--proxy', help='HTTP/Mixed 代理，例如 http://127.0.0.1:7890')
    parser.add_argument('--jobs', type=int, default=3)
    parser.add_argument('--connections', type=int, default=8)
    parser.add_argument('--family', choices=['sdxl-family', 'sdxl', 'all'], default='sdxl-family',
                        help='默认仅 SDXL 及其 Pony/Illustrious/NoobAI 衍生版本')
    args = parser.parse_args()
    if not 1 <= args.jobs <= 8 or not 1 <= args.connections <= 16:
        parser.error('jobs 范围 1–8，connections 范围 1–16')
    lock = (ROOT / '.download.lock').open('a+b')
    try:
        import msvcrt
        if lock.seek(0, 2) == 0:
            lock.write(b'0')
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            print('已有下载器在运行，请勿同时打开两次。')
            return 2
        return run(args)
    except KeyboardInterrupt:
        print('\n已停止。保留模型和 .aria2 文件，再运行 BAT 续传。')
        return 130
    except Exception as error:
        # Never print request URLs or credentials from network exceptions.
        if isinstance(error, (RuntimeError, ValueError)):
            print('错误：' + str(error))
        else:
            print('错误：' + type(error).__name__ + '。请检查网络、权限及目录文件。')
        return 1
    finally:
        lock.close()


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    raise SystemExit(main())
