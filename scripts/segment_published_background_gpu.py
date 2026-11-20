"""Inspect a published anime foreground mask before background inpainting."""
import argparse
import ast
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, default=ROOT / 'data/test-results/native-studio-batch/20261007-151352-998257/result.png')
    args = parser.parse_args()
    import torch
    import cv2
    import numpy as np
    from PIL import Image
    from safetensors.torch import load_file
    from vendor.anime_seg.isnet import ISNetDIS
    if not torch.cuda.is_available():
        raise RuntimeError('Anime segmentation requires CUDA; CPU fallback forbidden.')
    torch.set_num_threads(2)
    cfg = json.loads((ROOT / 'config/anime-person-mask.json').read_text(encoding='utf-8'))
    weights = ROOT / cfg['model']
    with weights.open('rb') as stream:
        assert hashlib.file_digest(stream, 'sha256').hexdigest() == cfg['sha256']
    # Retain the author's original preprocessing function as pinned source.
    url = f'https://raw.githubusercontent.com/SkyTNT/anime-segmentation/{cfg["code_revision"]}/inference.py'
    source_path = ROOT / 'data/published-solutions/anime-segmentation-inference.py'
    if not source_path.exists():
        with urllib.request.urlopen(url, timeout=30) as response:
            source_path.write_bytes(response.read())
    tree = ast.parse(source_path.read_text(encoding='utf-8'))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'get_mask')
    namespace = {'np': np, 'cv2': cv2, 'torch': torch, 'amp': torch.cuda.amp}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(source_path), 'exec'), namespace)

    class AuthorInference(torch.nn.Module):
        def __init__(self):
            super().__init__()
            with torch.device('cuda:0'):
                self.net = ISNetDIS()

        @property
        def device(self):
            return next(self.parameters()).device

        def forward(self, x):
            # The exact ISNet branch of the author's AnimeSegmentation.forward.
            return self.net(x)[0][0].sigmoid()

    model = AuthorInference().eval()
    state = {key.removeprefix('net.'): value for key, value in load_file(str(weights), device='cuda:0').items() if key.startswith('net.')}
    model.net.load_state_dict(state, strict=True)
    del state
    counts = Counter()
    handles = []
    def check(module, inputs):
        if any(item.device.type != 'cuda' for item in inputs if isinstance(item, torch.Tensor)):
            raise RuntimeError('Segmentation input left CUDA; CPU inference forbidden.')
        if any(item.device.type != 'cuda' for item in list(module.parameters(recurse=False)) + list(module.buffers(recurse=False))):
            raise RuntimeError('Segmentation parameters left CUDA; CPU inference forbidden.')
        counts[type(module).__name__] += 1
    for module in model.modules():
        handles.append(module.register_forward_pre_hook(check))
    picture = Image.open(args.source).convert('RGB')
    rgb = np.array(picture)
    alpha = namespace['get_mask'](model, rgb, use_amp=True, s=cfg['size'])[:, :, 0]
    assert np.isfinite(alpha).all() and counts['ISNetDIS'] == 1
    for handle in handles:
        handle.remove()
    # Conservative protection retains every foreground component, including all
    # four people and their accessories. It never selects only the largest one.
    foreground = (alpha > .1).astype(np.uint8)
    protected = cv2.dilate(foreground, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (33, 33)))
    background = (1 - protected) * 255
    out = ROOT / 'data/test-results/published-background-mask' / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    out.mkdir(parents=True)
    Image.fromarray((alpha * 255).clip(0, 255).astype(np.uint8)).save(out / 'author-alpha.png')
    Image.fromarray(protected * 255).save(out / 'protected-people.png')
    Image.fromarray(background).save(out / 'background-mask.png')
    overlay = rgb.copy()
    selected = background > 0
    overlay[selected] = (overlay[selected] * .65 + np.array([40, 255, 70]) * .35).astype(np.uint8)
    Image.fromarray(overlay).save(out / 'overlay.png')
    report = {'source': args.source.resolve().relative_to(ROOT).as_posix(),
              'source_sha256': hashlib.sha256(args.source.read_bytes()).hexdigest(),
              'model': cfg, 'author_preprocessing': url,
              'preprocessing_sha256': hashlib.sha256(source_path.read_bytes()).hexdigest(),
              'device': 'cuda:0', 'cpu_neural_fallback': False, 'guarded_forward_calls': dict(counts),
              'all_foreground_components_retained': True, 'threshold': .1, 'protection_padding_pixels': 16,
              'background_pixel_fraction': float(selected.mean()), 'production_adoption': False,
              'acceptance': 'pending_visual_review'}
    (out / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(out.relative_to(ROOT).as_posix(), flush=True)


if __name__ == '__main__':
    main()
