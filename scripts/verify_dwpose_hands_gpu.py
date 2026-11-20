"""Inspect author's TorchScript DWPose hands before using any pose for repair.

All model inference is CUDA; the author's ONNX/OpenCV fallback paths are never
selected. Drawing and JSON serialization are ordinary CPU image processing.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', default='data/test-results/published-hires/20261007-102547-732245/result.png')
    parser.add_argument('--studio-regions', action='store_true')
    args = parser.parse_args()
    source = ROOT / args.image
    author = ROOT / 'data/published-solutions/comfyui-controlnet-aux'
    revision = subprocess.check_output(['git', '-C', str(author), 'rev-parse', 'HEAD'], text=True).strip()
    if revision != '83463c2e4b04e729268e57f638b4212e0da4badc':
        raise RuntimeError('姿势检测作者版本未核对')
    sys.path.insert(0, str(author / 'src'))
    import torch
    import numpy as np
    from PIL import Image
    from custom_controlnet_aux.dwpose import DwposeDetector, draw_poses, encode_poses_as_dict
    from custom_controlnet_aux.dwpose.wholebody import Wholebody
    import custom_controlnet_aux.dwpose.wholebody as native_wholebody
    if not torch.cuda.is_available():
        raise RuntimeError('姿势检测必须使用 GPU，禁止回退到 CPU')
    models = json.loads((ROOT / 'data/lora-curation/research/dwpose-torch-models-verified.json').read_text(encoding='utf-8'))
    for item in models:
        if not item['file'].endswith('.torchscript.pt'):
            raise RuntimeError('禁止选择 ONNX/OpenCV CPU 后端')
        if hashlib.sha256((ROOT / item['file']).read_bytes()).hexdigest() != item['sha256']:
            raise RuntimeError('姿势模型完整校验失败')
    stamp = time.strftime('%Y%m%d-%H%M%S') + f'-{time.time_ns() % 1000000:06d}'
    out = ROOT / 'data/test-results/dwpose-hands' / stamp
    out.mkdir(parents=True)
    def save(name, value):
        (out / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    estimator = Wholebody(str(ROOT / models[0]['file']), str(ROOT / models[1]['file']), torchscript_device='cuda:0')
    for model in (estimator.det, estimator.pose):
        if not isinstance(model, torch.jit.RecursiveScriptModule):
            raise RuntimeError('禁止回退到 ONNX/OpenCV')
        if any(p.device.type != 'cuda' for p in model.parameters()):
            raise RuntimeError('姿势模型参数不在 GPU 上')
    image = np.array(Image.open(source).convert('RGB'))
    height, width = image.shape[:2]
    detector = DwposeDetector(estimator)
    known_regions = None
    if args.studio_regions:
        source_plan = ROOT / 'data/test-results/native-studio-batch/20261007-095103-898360/source-provenance.json'
        known_regions = json.loads(source_plan.read_text(encoding='utf-8'))['request']['characters']
        boxes = np.array([[r['region'][0] * width, r['region'][1] * height,
                           r['region'][2] * width, r['region'][3] * height] for r in known_regions])
        # Feed the existing studio regions to the author's unchanged pose path.
        # Only the bounding-box provider is replaced; no joints are constructed.
        with patch.object(native_wholebody, 'inference_jit_yolox', return_value=boxes):
            poses = detector.detect_poses(image)
    else:
        poses = detector.detect_poses(image)
    hands = draw_poses(poses, height, width, draw_body=False, draw_hand=True, draw_face=False)
    Image.fromarray(hands).save(out / 'hands.png')
    Image.fromarray(draw_poses(poses, height, width, draw_body=True, draw_hand=True, draw_face=False)).save(out / 'full-pose.png')
    overlay = image.copy()
    active = np.any(hands != 0, axis=2)
    overlay[active] = hands[active]
    Image.fromarray(overlay).save(out / 'hand-overlay.png')
    save('keypoints.json', encode_poses_as_dict(poses, height, width))
    hand_points = []
    for i, pose in enumerate(poses):
        hand_points.append({'person': i, 'left_hand_points': sum(p is not None for p in (pose.left_hand or [])),
                            'right_hand_points': sum(p is not None for p in (pose.right_hand or []))})
    save('report.json', {'acceptance': 'pending_visual_review', 'people_detected': len(poses),
        'hand_points': hand_points, 'model_device': 'cuda:0', 'cuda_name': torch.cuda.get_device_name(0),
        'source_image': source.relative_to(ROOT).as_posix(), 'models': models,
        'node_source': 'https://github.com/comfyorg/comfyui-controlnet-aux', 'revision': revision,
        'existing_studio_regions': known_regions,
        'inference': 'Unmodified author TorchScript detector and pose estimator; assert CUDA parameters, native input placement matches them.'})
    (out.parent / 'latest.json').write_text(json.dumps({'path': str(out), 'acceptance': 'pending_visual_review'}, indent=2), encoding='utf-8')
    print('作者 DWPose GPU 姿势检测完成：' + str(len(poses)) + ' 人，' + str(out), flush=True)


if __name__ == '__main__':
    main()
