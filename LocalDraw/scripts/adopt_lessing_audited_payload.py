"""Adopt one explicitly audited source file; retain its catalog discrepancy.

The ordinary downloader's catalog SHA256 rule remains unchanged. This single
exception needs the actual trainer payload hash, CUDA audit, primary training
base evidence and real GPU/visual review tied to the exact file digest.
"""
import hashlib
import json
import os
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import curate_character_loras as curator
from comic_workflow import engine as e
from comic_workflow.lora_inspection import inspect_lora

OBSERVED = 'f5c8ff9af743f19b6432cdd73e0c71235a8f4b2a309b5ee12367c743da2158bf'
CATALOG = 'e91c5cf1b4f8abf7bbc09df8864a113b30426032c6085b8b76344302c40f5529'
PAYLOAD = '7ee8561a918da41007ad8710592d85cc30b13cd055a74f1815efa3ddda073369'
REVIEW = ROOT / 'data/test-results/lessing-gpu/20261007-084106-317625'


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    manifest_path = ROOT / 'data/lora-curation/selection.json'
    manifest = e.read(manifest_path)
    row = next(r for r in manifest['characters'] if r['character']['name'] == '止颂')
    e.need(row['selected'] is None, '已有止颂选型，禁止重复覆盖')
    source = ROOT / 'data/backups/lora-download-diagnostic/Lessing-000009.safetensors'
    with source.open('rb') as stream:
        actual = hashlib.file_digest(stream, 'sha256').hexdigest()
    e.need(actual == OBSERVED, '已审核文件内容已改变')
    proof = e.read(ROOT / 'data/lora-curation/research/lessing-payload-integrity.json')
    e.need(proof['observed_sha256'] == actual and proof['catalog_sha256'] == CATALOG
           and proof['tensor_sha256'] == PAYLOAD and proof['tensor_checksum_match'] is True,
           '文件与已审核校验证据不一致')
    e.need(proof['numeric_audit_device'] == 'cuda:0' and proof['finite_tensors'] == 2958
           and not proof['invalid_tensors'], 'GPU 数值检查未通过')
    with source.open('rb') as stream:
        header_size = struct.unpack('<Q', stream.read(8))[0]
        metadata = json.loads(stream.read(header_size))['__metadata__']
        payload = hashlib.file_digest(stream, 'sha256').hexdigest()
    e.need(payload == PAYLOAD == metadata['sshs_model_hash'], '训练记录中的权重数据校验不符')
    e.need(metadata['ss_new_sd_model_hash'] == 'e6bb9ea85bbf7bf6478a7c6d18b71246f22e95d41bcdd80ed40aa212c33cfeff', '训练底模来源已改变')
    tags = json.loads(metadata['ss_tag_frequency'])
    e.need(any('lessing(arknights)' in group for group in tags.values()), '训练文件不含止颂身份词')
    inspection = inspect_lora(source)
    e.need(inspection['structurally_compatible'] is True, '文件不是兼容的 SDXL 权重')
    review = e.read(REVIEW / 'visual-review.json')
    e.need(review['gpu_load'] == 'passed' and review['visual_identity'] == 'passed_on_NoobAI_at_0.8', '缺少实际 GPU 角色检查')
    for seed in (71007, 71008):
        history = e.read(REVIEW / f'noob-{seed}-history.json')
        e.need(history['status']['status_str'] == 'success' and (REVIEW / f'noob-{seed}.png').is_file(), '实际 GPU 图像证据缺失')
    model = curator.fetch_json('https://civitai.com/api/v1/models/1429886', 'lessing-primary-model')
    version = next(v for v in model['modelVersions'] if v['id'] == 1616200)
    file = next(f for f in version['files'] if f['id'] == 1516504)
    e.need(file['name'] == 'Lessing-000009.safetensors' and file['hashes']['SHA256'].lower() == CATALOG, '目录证据发生变化')
    score, evidence = curator.rank_version(model, {**version, 'baseModel': 'SDXL 1.0'}, file)
    certificate = {
        'kind': 'single_file_audited_trainer_payload_exception', 'observed_full_file_sha256': actual,
        'catalog_full_file_sha256': CATALOG, 'catalog_checksum_match': False,
        'trainer_payload_sha256': payload, 'trainer_payload_checksum_match': True,
        'numeric_gpu_audit': 'data/lora-curation/research/lessing-payload-integrity.json',
        'training_base_evidence': 'data/lora-curation/sources/lessing-trained-base.json',
        'gpu_visual_review': str((REVIEW / 'visual-review.json').relative_to(ROOT).as_posix()),
        'visual_likeness': 'partial', 'full_task_accepted': False,
        'limitation': 'The cause of the catalog full-file hash difference is unproven; exact costume acceptance remains incomplete.',
    }
    chosen = {
        'model_id': model['id'], 'model': model['name'], 'version_id': version['id'], 'version': version['name'],
        'base_model': 'SDXL 1.0', 'api_base_model': version['baseModel'], 'original_name': file['name'],
        'file_id': file['id'], 'sha256': actual, 'catalog_sha256': CATALOG, 'bytes': source.stat().st_size,
        'url': file['downloadUrl'], 'page': 'https://civitai.com/models/1429886?modelVersionId=1616200',
        'triggers': [r'lessing\(arknights\)'], 'score': score,
        'relative_path': '明日方舟/止颂_' + file['name'],
        'evidence': {**evidence, 'verified_family_override': inspection, 'integrity_exception': certificate,
                     'visual_review': 'gpu_identity_passed_likeness_partial',
                     'trigger_source': {'type': 'verified_safetensors_training_header', 'sha256': actual, 'tokens': ['lessing(arknights)']}},
    }
    destination = ROOT / 'models/loras' / chosen['relative_path']
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        os.link(source, destination)
    else:
        with destination.open('rb') as stream:
            e.need(hashlib.file_digest(stream, 'sha256').hexdigest() == actual, '保留不同的已有文件并停止')
    row['candidates'].append(chosen)
    row.update(selected=chosen, status='selected_audited_payload_catalog_discrepancy')
    e.save(ROOT / 'data/lora-curation/research/lessing-adoption-certificate.json', certificate)
    e.save(manifest_path, manifest)
    e.save(ROOT / 'data/lora-curation/characters' / (row['character']['game']+'-'+row['character']['id']+'.json'), row)
    gaps_path = ROOT / 'data/lora-curation/missing-downloads.json'
    gaps = e.read(gaps_path)
    gaps['characters'] = [r for r in gaps['characters'] if r['name'] != '止颂']
    e.save(gaps_path, gaps)
    rejected_path = ROOT / 'data/lora-curation/rejected-files.json'
    rejected = e.read(rejected_path)
    for old in rejected:
        if old.get('file_id') == file['id']:
            old['resolution'] = certificate
    e.save(rejected_path, rejected)
    labels_path = ROOT / 'data/lora-labels.json'
    labels = e.read(labels_path)
    labels[chosen['relative_path']] = {**labels.get(chosen['relative_path'], {}), 'weight': .8, 'clip_weight': .8,
                                      'quality_review': 'identity_passed_costume_partial'}
    e.save(labels_path, labels)
    curator.finalize_library()
    print('止颂已加入可用库；目录校验差异和服装还原限制均保留，当前任务尚未验收完成', flush=True)


if __name__ == '__main__':
    main()
