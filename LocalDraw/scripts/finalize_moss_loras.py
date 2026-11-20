"""Check every adapter against the local MOSS tensor shapes and register labels."""
import json
import re
import shutil
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from comic_workflow.lora_inspection import inspect_lora

OUT = ROOT / 'data/moss-loras'


def header(path):
    with path.open('rb') as f:
        return json.loads(f.read(struct.unpack('<Q', f.read(8))[0]))


def main():
    rows = json.loads((OUT / 'complete.json').read_text(encoding='utf-8'))
    base = header(ROOT / 'models/diffusion_models/moss_image_sft_bf16.safetensors')
    shapes = {}
    for key, tensor in base.items():
        if not key.endswith('.weight'):
            continue
        module = key.removesuffix('.weight')
        shapes[module] = tensor['shape']
        shapes['diffusion_model.' + module] = tensor['shape']
        shapes['lora_unet_' + module.replace('.', '_')] = tensor['shape']
        if module.endswith('.proj.qkv'):
            for projection in 'qkv':
                split = module[:-3] + projection
                shape = [tensor['shape'][0] // 3, tensor['shape'][1]]
                shapes[split] = shape
                shapes['diffusion_model.' + split] = shape
                shapes['lora_unet_' + split.replace('.', '_')] = shape
    checks = []
    for row in rows:
        p = ROOT / row['path']
        weights = header(p)
        pairs = 0
        for key, tensor in weights.items():
            match = re.fullmatch(r'(.*)\.lora_(A|down)\.weight', key)
            if not match:
                continue
            module, side = match.groups()
            shape = shapes.get(module)
            partner = module + '.lora_' + ('B' if side == 'A' else 'up') + '.weight'
            down, up = tensor['shape'], weights[partner]['shape']
            assert shape and down[1] == shape[1] and up[0] == shape[0] and down[0] == up[1], (p.name, key, shape)
            pairs += 1
        assert pairs > 0, p
        inspection = inspect_lora(p, 'moss_image_sft_bf16.safetensors')
        assert inspection['structurally_compatible'] is True, (p.name, inspection)
        checks.append({'file': row['path'], 'projection_pairs': pairs, 'inspection': inspection})
    (OUT / 'compatibility.json').write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding='utf-8')
    label_path = ROOT / 'data/lora-labels.json'
    backup = OUT / 'lora-labels-before.json'
    if label_path.exists() and not backup.exists():
        shutil.copy2(label_path, backup)
    labels = json.loads(label_path.read_text(encoding='utf-8')) if label_path.exists() else {}
    for row in rows:
        key = row['path'].removeprefix('models/loras/')
        if key not in labels:
            labels[key] = {'name': Path(key).stem.split('_')[0], 'character_id': '', 'character': '',
                           'trigger_words': row['trigger'], 'weight': row['strength'],
                           'checkpoint': 'moss_image_sft_bf16.safetensors', 'confirmed': False}
    temp = label_path.with_suffix('.moss-tmp')
    temp.write_text(json.dumps(labels, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(label_path)
    print('Validated and registered', len(checks), 'adapters;', sum(r['size'] for r in rows), 'bytes')


if __name__ == '__main__':
    main()
