"""Download the creator's newer pack into an isolated comparison directory."""
import argparse
import hashlib
import importlib.util
import os
from pathlib import Path
import struct
import re
import sys
import urllib.request
import json

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from comic_workflow import engine as e


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    model = e.read(ROOT / 'data/lora-curation/sources/model-462775.json')
    version = next(v for v in model['modelVersions'] if v['id'] == 637867)
    file = next(f for f in version['files'] if f['id'] == 552609)
    sha = file['hashes']['SHA256'].lower()
    e.need(sha == '2594792e5882f0b7a4a81cb8b1dbb6a6ff7413104612048a9f81de3c9d4673f5', '新版模型来源不符')
    spec = importlib.util.spec_from_file_location('safe_civitai_downloader', ROOT / 'Chenkin_LoRA/download.py')
    downloader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(downloader)
    staging = ROOT / 'data/lora-pack-diagnostic'
    staging.mkdir(parents=True, exist_ok=True)
    downloader.ROOT = staging
    downloader.ARIA_EXE = ROOT / 'Chenkin_LoRA/tools/aria2-1.36.0/aria2c.exe'
    entry = {'relative_path': file['name'], 'original_name': file['name'], 'bytes': round(file['sizeKB']*1024),
             'sha256': sha, 'url': file['downloadUrl'], 'version_id': version['id'], 'file_id': file['id']}
    pending = downloader.pending_files([entry])
    token = (ROOT / 'Chenkin_LoRA/civitai-token.txt').read_text(encoding='utf-8').strip()
    proxy = urllib.request.getproxies().get('https') or urllib.request.getproxies().get('http')
    if pending:
        result, failures = downloader.download_with_retries([entry], pending, token,
                                                          argparse.Namespace(jobs=1, connections=8), proxy)
        e.need(result == 0 and not failures, '新版模型下载未完成，隔离目录保留进度')
    local = staging / file['name']
    e.need(downloader.digest(local) == sha, '新版模型完整校验失败')
    target = ROOT / 'data/comfy-lora-diagnostic/明日方舟' / ('明日方舟全角色_' + file['name'])
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        os.link(local, target)
    e.need(downloader.digest(target) == sha, '隔离模型与下载文件不符')
    with local.open('rb') as stream:
        size = struct.unpack('<Q', stream.read(8))[0]
        header = json.loads(stream.read(size))
    metadata = header.get('__metadata__', {})
    terms = ['gnosis', 'executor_the_ex_foedere', 'vigil', 'sharp', 'stormeye', 'pith', 'tulip', 'ash']
    tag_sources = {}
    def normalized_tag(tag):
        tag = tag.lower().removeprefix('character:').strip()
        return re.sub(r'[^a-z0-9]', '', tag)
    for key in ('ss_tag_frequency', 'ss_dataset_dirs'):
        try:
            parsed = json.loads(metadata.get(key, '{}'))
        except ValueError:
            parsed = {}
        matches = {term: [] for term in terms}
        for folder, values in parsed.items():
            for term in terms:
                expected = normalized_tag(term + '(arknights)')
                if key == 'ss_tag_frequency' and isinstance(values, dict):
                    for tag, count in values.items():
                        if normalized_tag(tag) == expected:
                            matches[term].append({'folder': folder, 'tag': tag, 'count': count})
                elif normalized_tag(re.sub(r'^\d+[_ ]*', '', folder)) == expected:
                    matches[term].append({'folder': folder, 'values': values})
        tag_sources[key] = matches
    e.save(ROOT / 'data/lora-curation/research/newer-arknights-pack.json', {
        'source': 'https://civitai.com/models/462775?modelVersionId=637867',
        'version': version['name'], 'file': target.relative_to(ROOT).as_posix(),
        'sha256': sha, 'complete': True, 'original_file': file['name'],
        'training_base': metadata.get('ss_sd_model_name'),
        'training_base_sha256': metadata.get('ss_sd_model_hash'),
        'tag_sources': tag_sources,
        'acceptance': 'pending_actual_cuda_render_and_official_art_comparison',
        'production_adopted': False,
        'matching_scope': 'Exact normalized character tags/folders only; sharp teeth, tulip flowers, SilverAsh and Whislash do not count as the missing identities.',
    })
    print('作者新版通用模型已完整校验，留在隔离区；尚未计入角色覆盖率', flush=True)


if __name__ == '__main__':
    main()
