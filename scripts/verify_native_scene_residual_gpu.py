"""Check the native shared-scene combiner on CUDA, without neural inference."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, 'E:/AI/ComfyUI')
sys.argv = [sys.argv[0], '--cuda-device', '0', '--gpu-only']
import torch
from comfy.samplers import finalize_default_conds, get_area_and_mult


def main():
    if not torch.cuda.is_available():
        raise RuntimeError('CUDA is required; no CPU fallback.')
    latent = torch.zeros((1, 4, 4, 24), device='cuda:0')
    timestep = torch.tensor([900.], device='cuda:0')
    results = []
    for count in (3, 4):
        for strength in (1., .85, .70):
            branches = {None: []}
            for index in range(count):
                mask = torch.zeros((1, 4, 24), device='cuda:0')
                mask[:, :, index * 24 // count:(index+1) * 24 // count] = 1.
                cond = {'uuid': f'role-{index}', 'model_conds': {},
                        'mask': mask, 'mask_strength': strength}
                branches[None].append((get_area_and_mult(cond, latent, timestep), 0))
            default = {'uuid': 'shared-scene', 'model_conds': {}}
            finalize_default_conds(None, branches, [[default]], latent, timestep, {})
            scene = [cond for cond, _ in branches[None] if cond.uuid == 'shared-scene']
            if strength == 1.:
                assert not scene
                row = {'people': count, 'regional_strength': strength, 'scene_branch_used': False}
            else:
                assert len(scene) == 1 and scene[0].mult.device.type == 'cuda'
                assert torch.allclose(scene[0].mult, torch.full_like(latent, 1. - strength))
                row = {'people': count, 'regional_strength': strength, 'scene_branch_used': True,
                       'minimum_scene_weight': scene[0].mult.min().item(),
                       'maximum_scene_weight': scene[0].mult.max().item()}
            results.append(row)
    source = Path('E:/AI/ComfyUI/comfy/samplers.py')
    report = {'checked_at': datetime.now().isoformat(timespec='seconds'), 'device': 'cuda:0',
              'method': 'Unmodified ComfyUI finalize_default_conds and get_area_and_mult',
              'source': str(source), 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
              'tests': results, 'neural_inference': False,
              'scope': 'Numerical conditioning validation only; image quality is reviewed separately.',
              'cpu_neural_fallback': False, 'acceptance': 'native_shared_scene_weight_verified'}
    target = ROOT / 'data/lora-curation/research/native-shared-scene-weight.json'
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('Native CUDA scene combiner verified for three and four characters.')


if __name__ == '__main__':
    main()
