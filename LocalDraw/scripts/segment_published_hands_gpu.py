"""Run published SAM hand masks on already reviewed CUDA hand detections.

Uses the authors' original SamPredictor API. Device checks observe its forward
calls; no segmentation or neural attention implementation is replaced.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', choices=['meta', 'anime'], required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    source = root / 'data/test-results/published-hires/20261007-102547-732245/result.png'
    detections = json.loads((root / 'data/test-results/hand-detection-resolution/20261007-104419-563445/results.json').read_text(encoding='utf-8'))[1]
    assert detections['image'] == source.relative_to(root).as_posix()
    assert detections['device'] == 'cuda:0'
    record = json.loads((root / f'data/lora-curation/research/hand-sam-{args.model}-verified.json').read_text(encoding='utf-8'))
    path = root / record['file']
    assert path.stat().st_size == record['size']
    with path.open('rb') as stream:
        assert hashlib.file_digest(stream, 'sha256').hexdigest() == record['sha256']
    import numpy as np
    from PIL import Image, ImageDraw
    import torch
    from segment_anything import sam_model_registry, SamPredictor
    if not torch.cuda.is_available():
        raise RuntimeError('手部遮罩模型需要 CUDA，禁止回退 CPU')
    torch.set_num_threads(2)
    model = sam_model_registry['vit_b'](checkpoint=None)
    if path.suffix == '.safetensors':
        from safetensors.torch import load_file
        state = load_file(str(path))
    else:
        state = torch.load(path, map_location='cpu', weights_only=True)
    model.load_state_dict(state)
    del state
    model.to('cuda').eval()
    if any(value.device.type != 'cuda' for value in list(model.parameters()) + list(model.buffers())):
        raise RuntimeError('手部遮罩模型未完整放入 CUDA')
    calls = {}

    def tensors(value):
        if isinstance(value, torch.Tensor):
            yield value
        elif isinstance(value, dict):
            for item in value.values():
                yield from tensors(item)
        elif isinstance(value, (tuple, list)):
            for item in value:
                yield from tensors(item)

    handles = []
    for name in ('image_encoder', 'prompt_encoder', 'mask_decoder'):
        def check(module, positional, keyword, name=name):
            if any(t.device.type != 'cuda' for t in tensors((positional, keyword))):
                raise RuntimeError('手部遮罩输入未在 CUDA，禁止回退 CPU')
            if any(p.device.type != 'cuda' for p in module.parameters()):
                raise RuntimeError('手部遮罩推理未在 CUDA，禁止回退 CPU')
            calls[name] = calls.get(name, 0) + 1
        handles.append(getattr(model, name).register_forward_pre_hook(check, with_kwargs=True))
    args.output.mkdir(parents=True, exist_ok=True)
    picture = Image.open(source).convert('RGB')
    rgb = np.array(picture)
    predictor = SamPredictor(model)
    records = []
    merged = np.zeros(rgb.shape[:2], dtype=np.uint8)
    try:
        with torch.inference_mode():
            predictor.set_image(rgb)
            for index, detection in enumerate(detections['boxes']):
                box = np.array(detection['box'], dtype=np.float32)
                masks, scores, _ = predictor.predict(box=box, multimask_output=False)
                mask = masks[0].astype(np.uint8) * 255
                Image.fromarray(mask).save(args.output / f'hand-{index}-mask.png')
                merged = np.maximum(merged, mask)
                records.append({'index': index, 'box': detection['box'],
                                'sam_score': float(scores[0]), 'mask_pixels': int((mask > 0).sum()),
                                'file': f'hand-{index}-mask.png', 'visual_acceptance': 'pending'})
    finally:
        for handle in handles:
            handle.remove()
    assert calls.get('image_encoder') and calls.get('mask_decoder') == len(records)
    Image.fromarray(merged).save(args.output / 'combined-mask.png')
    overlay = picture.convert('RGBA')
    tint = Image.new('RGBA', picture.size, (255, 40, 20, 90))
    overlay.paste(tint, (0, 0), Image.fromarray((merged > 0).astype(np.uint8) * 90))
    draw = ImageDraw.Draw(overlay)
    for item in records:
        box = item['box']
        draw.rectangle(box, outline='yellow', width=2)
        draw.text((box[0], box[1]), str(item['index']), fill='yellow')
    overlay.convert('RGB').save(args.output / 'overlay.png')
    report = {'model': record, 'source': source.relative_to(root).as_posix(),
              'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
              'device': 'cuda:0', 'cpu_model_fallback': False, 'guarded_forward_calls': calls,
              'hand_regions': records, 'production_adoption': False,
              'author_api': 'segment_anything.SamPredictor.predict(box=box,multimask_output=False)',
              'detector_source': 'deepghs/anime_hand_detection; frozen eight reviewed CUDA boxes',
              'acceptance': 'pending_visual_review'}
    (args.output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'model': args.model, 'device': 'cuda:0', 'hands': len(records), 'calls': calls}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
