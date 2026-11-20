"""Inspect author HandRefiner mesh outputs on eight reviewed CUDA hand boxes.

Original author networks, projection and ray casting are used without changes.
The failed MediaPipe front end is replaced by reviewed anime YOLO detections;
both handedness orientations are inspected instead of fabricating landmarks.
"""
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import time

from published_mesh_hand_runtime import load_native_runtime

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'data/test-results/published-hires/20261007-102547-732245/result.png'
SOURCE_SHA = 'd38243a051a69ddf4b7a9c5e2720760c49c6557732d94e3a96d794c4469327c6'


class GPUExecutionError(RuntimeError):
    pass


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main():
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    out = ROOT / 'data/test-results/published-hand-mesh' / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    out.mkdir(parents=True)
    runtime, loading = load_native_runtime()
    import torch
    import cv2
    import numpy as np
    from PIL import Image, ImageDraw

    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == SOURCE_SHA
    detections = json.loads((ROOT / 'data/test-results/hand-detection-resolution/20261007-104419-563445/results.json').read_text(encoding='utf-8'))[1]
    assert detections['device'] == 'cuda:0' and len(detections['boxes']) == 8
    assert detections['image'] == SOURCE.relative_to(ROOT).as_posix()
    source = np.asarray(Image.open(SOURCE).convert('RGB'))
    height, width = source.shape[:2]
    padded = np.zeros((height * 2, width * 2, 3), dtype=np.uint8)
    padded[height // 2:height // 2 + height, width // 2:width // 2 + width] = source
    calls = Counter()
    handles = []

    def tensors(value):
        if isinstance(value, torch.Tensor):
            yield value
        elif isinstance(value, (tuple, list)):
            for item in value:
                yield from tensors(item)
        elif isinstance(value, dict):
            for item in value.values():
                yield from tensors(item)

    def guard(label):
        def check(module, args, kwargs):
            for value in list(tensors(args)) + list(tensors(kwargs)) + list(module.parameters(recurse=False)) + list(module.buffers(recurse=False)):
                if value.device.type != 'cuda':
                    raise GPUExecutionError(f'CPU neural execution forbidden: {label} {value.device}')
            calls[label] += 1
        return check

    for group, network in [('graphormer', runtime._model), ('mano', runtime.mano_model)]:
        for module in network.modules():
            handles.append(module.register_forward_pre_hook(guard(group + ':' + type(module).__name__), with_kwargs=True))
    report = {'source_image': SOURCE.relative_to(ROOT).as_posix(), 'source_sha256': SOURCE_SHA,
              'loading': loading, 'device': torch.cuda.get_device_name(0),
              'cpu_neural_fallback': False, 'detections': detections,
              'adaptation': 'Reviewed CUDA anime YOLO boxes replace MediaPipe front end. Both original and mirrored orientations are evaluated, with unchanged author mesh networks and projection.',
              'crop_method': 'Author even-sized 1.6 bounding-box crop and native 224-pixel interpolation.',
              'results': [], 'production_adoption': False, 'acceptance': 'pending_visual_review'}
    sheet = Image.new('RGB', (6 * 256, 8 * 286), 'white')
    sheet_draw = ImageDraw.Draw(sheet)
    save(out / 'report.json', report)
    print('Native author GPU mesh loaded; inspecting eight hands in both orientations.', flush=True)
    for index, detection in enumerate(detections['boxes']):
        x_min, y_min, x_max, y_max = [int(value) for value in detection['box']]
        cx, cy = (x_min + x_max) // 2, (y_min + y_max) // 2
        crop_len = int((max(x_max - x_min, y_max - y_min) * 1.6) // 2 * 2)
        px = int(cx - (crop_len / 2 - 1) + width / 2)
        py = int(cy - (crop_len / 2 - 1) + height / 2)
        crop = padded[py:py + crop_len, px:px + crop_len].copy()
        assert crop.shape == (crop_len, crop_len, 3)
        Image.fromarray(crop).save(out / f'hand-{index}-original.png')
        for flip in (False, True):
            label = f'hand-{index}-' + ('mirrored' if flip else 'original')
            model_crop = cv2.flip(crop, 1) if flip else crop
            model_input = cv2.resize(model_crop, (224, 224), interpolation=cv2.INTER_CUBIC if crop_len < 224 else cv2.INTER_AREA)
            Image.fromarray(model_input).save(out / (label + '-input.png'))
            before = dict(calls)
            started = time.perf_counter()
            depth, points = runtime.run_inference(model_input, runtime._model, runtime.mano_model, runtime.mesh_sampler, crop_len / 224, crop_len)
            torch.cuda.synchronize()
            item = {'index': index, 'mirrored_input': flip, 'crop_len': crop_len,
                    'original_crop_box': [px - width // 2, py - height // 2, px - width // 2 + crop_len, py - height // 2 + crop_len],
                    'seconds': time.perf_counter() - started,
                    'gpu_forward_calls': {key: count - before.get(key, 0) for key, count in calls.items() if count > before.get(key, 0)},
                    'depth_nonempty': depth is not None, 'visual_review': 'pending'}
            base = Image.fromarray(crop)
            overlay = base.copy()
            valid_depth = depth is not None and np.isfinite(depth).all()
            valid_points = points is not None and tuple(points.shape) == (21, 2) and torch.isfinite(points).all().item()
            item.update(depth_finite=bool(valid_depth), projected_points_finite=bool(valid_points))
            if valid_depth and valid_points:
                points = points.numpy()
                if flip:
                    depth = cv2.flip(depth, 1)
                    points[:, 0] = crop_len - 1 - points[:, 0]
                depth_u8 = np.clip(depth, 0, 255).astype(np.uint8)
                depth_image = Image.fromarray(depth_u8).convert('RGB')
                depth_image.save(out / (label + '-depth.png'))
                colored = np.asarray(base).copy()
                selected = depth_u8 > 0
                colored[selected] = (colored[selected] * .5 + np.array([0, 255, 80]) * .5).astype(np.uint8)
                overlay = Image.fromarray(colored)
                draw = ImageDraw.Draw(overlay)
                for joint, (x, y) in enumerate(points):
                    draw.ellipse((x-1.5, y-1.5, x+1.5, y+1.5), fill='red')
                    draw.text((x+2, y-5), str(joint), fill='red', stroke_width=1, stroke_fill='white')
                item.update(predicted_21_joints=points.tolist(), depth_foreground_pixels=int(selected.sum()),
                            joints_inside_crop=int(((points >= 0) & (points < crop_len)).all(axis=1).sum()))
            else:
                depth_image = Image.new('RGB', base.size, 'black')
                item['failure'] = 'Author reconstruction produced no usable finite depth; original output rejected without modification.'
            overlay.save(out / (label + '-overlay.png'))
            col = 3 if flip else 0
            for offset, picture in enumerate((base, depth_image, overlay)):
                sheet.paste(picture.resize((256, 256), Image.Resampling.NEAREST), ((col + offset) * 256, index * 286 + 30))
            sheet_draw.text((col * 256 + 5, index * 286 + 7), f'{label}: source / author depth / projected points', fill='black')
            report['results'].append(item)
            report['observed_gpu_forward_calls'] = dict(calls)
            save(out / 'report.json', report)
            sheet.save(out / 'contact-sheet.png')
            print(label, 'depth', depth is not None, 'seconds', round(item['seconds'], 2), flush=True)
    for handle in handles:
        handle.remove()
    assert calls['graphormer:Graphormer_Hand_Network'] == 16
    report.update(inference_complete=True, neural_execution_verified='all observed module forwards used CUDA tensors and CUDA parameters',
                  visual_review='pending', peak_cuda_memory_bytes=torch.cuda.max_memory_allocated())
    save(out / 'report.json', report)
    for name in ('graphormer', 'hrnet'):
        record_path = ROOT / f'data/lora-curation/research/mesh-hand-{name}-verified.json'
        record = json.loads(record_path.read_text(encoding='utf-8'))
        record.update(inference_run=True, gpu_inference_report=(out / 'report.json').relative_to(ROOT).as_posix())
        save(record_path, record)
    print('Saved mesh diagnostic:', out.relative_to(ROOT).as_posix(), flush=True)


if __name__ == '__main__':
    main()
