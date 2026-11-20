"""Audit the quarantined Lessing payload without publishing a library weight."""
import hashlib
import json
from pathlib import Path
import struct
import sys

import torch
from safetensors import safe_open

ROOT = Path(__file__).resolve().parents[1]

def need(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    need(torch.cuda.is_available(), '权重数值检验必须使用 GPU')
    path = ROOT/'data/backups/lora-download-diagnostic/Lessing-000009.safetensors'
    with path.open('rb') as stream:
        header_length = struct.unpack('<Q', stream.read(8))[0]
        header = json.loads(stream.read(header_length))
        tensor_sha = hashlib.file_digest(stream, 'sha256').hexdigest()
    with path.open('rb') as stream:
        file_sha = hashlib.file_digest(stream, 'sha256').hexdigest()
    metadata = header['__metadata__']
    need(tensor_sha == metadata.get('sshs_model_hash'), '训练时保存的权重数据校验不符')
    dimensions = sorted({v['shape'][1] for k,v in header.items()
                         if 'attn2_to_k' in k and 'lora_down' in k and len(v['shape']) == 2})
    need(2048 in dimensions, '不是 SDXL 权重')
    inspection = {'family': 'SDXL', 'structurally_compatible': True, 'cross_attention_dimensions': dimensions}
    base = json.loads((ROOT/'data/lora-curation/sources/lessing-trained-base.json').read_text(encoding='utf-8'))
    need(base['baseModel'] == 'SDXL 1.0' and any(
        f['hashes']['SHA256'].lower() == metadata['ss_new_sd_model_hash'].lower() for f in base['files']),
        '训练底模来源无法核对')
    count, invalid, largest = 0, [], 0.0
    with safe_open(path, framework='pt', device='cuda:0') as weights:
        for name in weights.keys():
            tensor = weights.get_tensor(name)
            need(tensor.device.type == 'cuda', '禁止 CPU 权重数值检验')
            if not bool(torch.isfinite(tensor).all()):
                invalid.append(name)
            largest = max(largest, float(tensor.abs().max()))
            count += 1
    need(not invalid, '权重含无效数值')
    proof = {'status': 'payload_and_structure_passed_pending_visual_review',
             'file': str(path), 'observed_sha256': file_sha,
             'catalog_sha256': 'e91c5cf1b4f8abf7bbc09df8864a113b30426032c6085b8b76344302c40f5529',
             'catalog_checksum_match': False,
             'tensor_sha256': tensor_sha, 'trainer_embedded_tensor_sha256': metadata['sshs_model_hash'],
             'tensor_checksum_match': True, 'finite_tensors': count, 'invalid_tensors': invalid,
             'maximum_absolute_value': largest, 'numeric_audit_device': 'cuda:0',
             'inspection': inspection, 'actual_training_family': 'SDXL 1.0',
             'api_family_label': 'Pony',
             'training_base_source': 'https://civitai.com/models/'+str(base['modelId'])+'?modelVersionId='+str(base['id']),
             'unique_training_images': 3,
             'training_tags': json.loads(metadata['ss_tag_frequency']),
             'limitation': 'Embedded payload integrity is verified; the cause of the catalog full-file checksum difference is not proven.'}
    (ROOT/'data/lora-curation/research/lessing-payload-integrity.json').write_text(
        json.dumps(proof, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'{count} 个权重张量已在 GPU 核对，数据校验和结构通过；尚未发布到可用库', flush=True)


if __name__ == '__main__':
    main()
