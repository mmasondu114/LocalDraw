"""Download the pinned Qwen editing assets from ModelScope, eight connections total."""
import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from comic_workflow.downloader import download, filehash

OUT = ROOT / 'data/consistency-upgrade-20261025'


def now():
    return datetime.now(timezone(timedelta(hours=8))).isoformat()


def set_state(**fields):
    path = OUT / 'state.json'
    state = json.loads(path.read_text(encoding='utf-8'))
    state.update(fields, updated=now())
    temp = path.with_suffix('.modelscope.tmp')
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


class Tee:
    def __init__(self, console, log):
        self.console, self.log = console, log
    def write(self, value):
        self.console.write(value)
        self.log.write(value)
        self.log.flush()
        return len(value)
    def flush(self):
        self.console.flush()
        self.log.flush()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-links', action='store_true')
    args = parser.parse_args()
    rows = json.loads((OUT / 'modelscope-manifest.json').read_text(encoding='utf-8'))
    expected = {r['target']: r for r in json.loads((OUT / 'model-manifest.json').read_text(encoding='utf-8'))}
    for row in rows:
        assert row['target'] in expected and row['sha256'] == expected[row['target']]['sha256'] and row['size'] == expected[row['target']]['size'], 'Manifest differs from pinned official asset'
        assert row['url'].startswith('https://modelscope.cn/models/Comfy-Org/'), 'Unexpected source'
        target = (ROOT / 'models' / row['target']).resolve()
        assert target.is_relative_to((ROOT / 'models').resolve()), 'Target outside models directory'
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    if args.check_links:
        for row in rows:
            start, end = 1048576, 1049599
            req = urllib.request.Request(row['url'] + '?part=0&offset=' + str(start), headers={'Range': f'bytes={start}-{end}', 'User-Agent': 'ComicWorkflow/1.0'})
            with opener.open(req, timeout=30) as response:
                body = response.read(1025)
                assert response.status == 206 and response.headers.get('Content-Range') == f"bytes {start}-{end}/{row['size']}" and len(body) == 1024, 'Range resume unsupported'
            target = ROOT / 'models' / row['target']
            sample = target if target.exists() else target.with_name(target.name + '.parts') / '0'
            with sample.open('rb') as source:
                source.seek(start)
                assert source.read(1024) == body, 'Domestic data differs from existing official-source bytes'
            print('RANGE RESUME VERIFIED: ' + row['target'], flush=True)
        return

    # One process lock for this launcher; the downloader also locks every model.
    lock = (OUT / 'modelscope-download.lock').open('a+b')
    lock.seek(0); lock.write(b'0'); lock.flush(); lock.seek(0)
    import msvcrt
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        raise RuntimeError('This downloader is already running. Keep the existing window open.')
    original_stdout, original_stderr = sys.stdout, sys.stderr
    log = (OUT / 'download-modelscope.log').open('a', encoding='utf-8')
    sys.stdout, sys.stderr = Tee(original_stdout, log), Tee(original_stderr, log)
    try:
        print('\nModelScope 国内源 | 总共8线程 | 沿用现有分块断点 | 完整SHA256校验', flush=True)
        print('保存目录：' + str(ROOT / 'models'), flush=True)
        print('关闭后可再次双击BAT续传；建议按Ctrl+C等待连接结束。', flush=True)
        set_state(phase='downloading_native_edit_models_modelscope', download_active=True, download_process_id=os.getpid(), download_script='scripts/download_consistency_modelscope.py', download_log='download-modelscope.log', download_connections=8)
        paths = []
        # Sequential models keep the total number of downloading workers at eight.
        for row in rows:
            target = ROOT / 'models' / row['target']
            if target.exists():
                print('核验已有文件：' + target.name, flush=True)
                if target.stat().st_size != row['size'] or filehash(target) != row['sha256']:
                    raise RuntimeError('Existing file differs; preserved without overwriting: ' + str(target))
            else:
                download(target.parent, 8, row['url'], row['size'], row['sha256'], target.name, timeout=45, opener=opener)
            (OUT / (target.name + '.verified.json')).write_text(json.dumps({**row, 'path': str(target), 'verified': True, 'at': now()}, indent=2), encoding='utf-8')
            paths.append(str(target))
        (OUT / 'models-ready.json').write_text(json.dumps(paths, indent=2), encoding='utf-8')
        set_state(phase='models_ready_waiting_visual_probe', download_active=False, model_checks='all_sha256_verified')
        print('全部模型下载完成，SHA256校验通过。可继续工作室四张实测。', flush=True)
    except KeyboardInterrupt:
        set_state(phase='modelscope_download_paused', download_active=False)
        print('下载已暂停，断点已保留。再次双击同一BAT继续。', flush=True)
        raise SystemExit(130)
    except Exception as exc:
        set_state(phase='modelscope_download_needs_retry', download_active=False, download_error=str(exc))
        raise
    finally:
        sys.stdout, sys.stderr = original_stdout, original_stderr
        log.close(); lock.close()


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    try:
        main()
    except Exception as exc:
        print('ERROR: ' + str(exc), file=sys.stderr, flush=True)
        raise SystemExit(1)
