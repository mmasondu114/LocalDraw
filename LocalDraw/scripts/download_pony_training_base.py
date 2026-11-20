"""Fetch the exact published training base for the existing Arknights pack."""
import argparse
import importlib.util
import os
from pathlib import Path
import shutil
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from comic_workflow import engine as e


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    version = e.read(ROOT / 'data/lora-curation/sources/existing-allv2-training-base.json')
    file = next(f for f in version['files'] if f['id'] == 228616)
    sha = file['hashes']['SHA256'].lower()
    e.need(version['id'] == 290640 and sha == '67ab2fd8ec439a89b3fedb15cc65f54336af163c7eb5e4f2acc98f090a29b0b3', '训练底模来源不符')
    spec = importlib.util.spec_from_file_location('safe_civitai_downloader', ROOT / 'Chenkin_LoRA/download.py')
    downloader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(downloader)
    staging = ROOT / 'data/checkpoint-diagnostic'
    staging.mkdir(parents=True, exist_ok=True)
    downloader.ROOT = staging
    downloader.ARIA_EXE = ROOT / 'Chenkin_LoRA/tools/aria2-1.36.0/aria2c.exe'
    entry = {'relative_path': file['name'], 'original_name': file['name'], 'bytes': round(file['sizeKB']*1024),
             'sha256': sha, 'url': file['downloadUrl'], 'version_id': version['id'], 'file_id': file['id']}
    target = ROOT / 'models/checkpoints' / file['name']
    if target.exists() and downloader.digest(target) == sha:
        print('精确训练底模已存在并通过完整校验', flush=True)
        return
    e.need(shutil.disk_usage(staging).free > entry['bytes'] + 2*1024**3, '磁盘空间不足以隔离下载训练底模')
    pending = downloader.pending_files([entry])
    token = (ROOT / 'Chenkin_LoRA/civitai-token.txt').read_text(encoding='utf-8').strip()
    proxy = urllib.request.getproxies().get('https') or urllib.request.getproxies().get('http')
    if pending:
        result, failures = downloader.download_with_retries([entry], pending, token,
                                                          argparse.Namespace(jobs=1, connections=8), proxy)
        e.need(result == 0 and not failures, '训练底模尚未完整下载；进度保留在隔离目录')
    local = staging / file['name']
    e.need(downloader.digest(local) == sha, '训练底模最终完整文件校验失败')
    e.need(not target.exists(), '已有不同的同名底模，保留并停止')
    os.link(local, target)
    e.save(ROOT / 'data/lora-curation/research/pony-training-base-download.json', {
        'source': 'https://civitai.com/models/257749?modelVersionId=290640',
        'training_base_header_match': True, 'file': target.relative_to(ROOT).as_posix(),
        'sha256': sha, 'actual_bytes': local.stat().st_size, 'complete': True,
        'purpose': 'Diagnostic comparison of existing Pony-trained LoRAs; production checkpoint unchanged.',
        'gpu_only_inference_required': True,
    })
    print('Pony 精确训练底模已完整下载并通过校验；仅供 GPU 对照试验', flush=True)


if __name__ == '__main__':
    main()
