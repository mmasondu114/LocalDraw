"""Inspect the author's unchanged hand CNN on verified anime hand boxes.

Only model placement, existing box inputs and drawing are integrated here.
No missing joints are constructed, and no pose is approved automatically.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--crop-factor', type=float, default=1.5)
    args = parser.parse_args()
    if not 1 <= args.crop_factor <= 3:
        raise ValueError('裁剪范围不明确')
    author = ROOT / 'data/published-solutions/comfyui-controlnet-aux'
    revision = subprocess.check_output(['git', '-C', str(author), 'rev-parse', 'HEAD'], text=True).strip()
    if revision != '83463c2e4b04e729268e57f638b4212e0da4badc':
        raise RuntimeError('作者版本未核对')
    sys.path.insert(0, str(author / 'src'))
    import numpy as np
    from PIL import Image
    import torch
    from custom_controlnet_aux.open_pose.hand import Hand
    from custom_controlnet_aux.open_pose.body import Keypoint
    from custom_controlnet_aux.open_pose.util import draw_handpose
    if not torch.cuda.is_available():
        raise RuntimeError('手部关键点必须使用 CUDA，禁止回退到 CPU')
    model_record = json.loads((ROOT / 'data/lora-curation/research/openpose-hand-model-verified.json').read_text(encoding='utf-8'))
    model_file = ROOT / model_record['file']
    with model_file.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != model_record['sha256']:
            raise RuntimeError('模型完整 SHA256 校验失败')
    source = ROOT / 'data/test-results/published-hires/20261007-102547-732245/result.png'
    detection = json.loads((ROOT / 'data/test-results/hand-detection-resolution/20261007-104419-563445/results.json').read_text(encoding='utf-8'))[1]
    if detection['image'] != source.relative_to(ROOT).as_posix() or detection['device'] != 'cuda:0':
        raise RuntimeError('检测输入未核对')
    image = np.array(Image.open(source).convert('RGB'))
    height, width = image.shape[:2]
    out = ROOT / 'data/test-results/openpose-hand-crops' / (time.strftime('%Y%m%d-%H%M%S') + f'-{time.time_ns()%1000000:06d}')
    out.mkdir(parents=True)
    detector = Hand(str(model_file)).to('cuda:0')
    inference_calls = []

    def check_cuda(module, inputs):
        if any(p.device.type != 'cuda' for p in module.parameters()) or inputs[0].device.type != 'cuda':
            raise RuntimeError('手部模型或输入未在 GPU，禁止回退到 CPU')
        inference_calls.append(True)

    guard = detector.model.register_forward_pre_hook(check_cuda)
    pose_canvas = np.zeros_like(image)
    rows = []
    try:
        for i, detection_box in enumerate(detection['boxes']):
            x1, y1, x2, y2 = detection_box['box']
            side = max(x2-x1, y2-y1) * args.crop_factor
            cx, cy = (x1+x2)/2, (y1+y2)/2
            box = [max(0, round(cx-side/2)), max(0, round(cy-side/2)),
                   min(width, round(cx+side/2)), min(height, round(cy+side/2))]
            crop = image[box[1]:box[3], box[0]:box[2]].copy()
            # The author CNN expects BGR pixels, as its native OpenPose caller.
            points = detector(crop[:, :, ::-1].copy())
            normalized = [Keypoint((p[0]+box[0])/width, (p[1]+box[1])/height)
                          if p[0] != 0 and p[1] != 0 else Keypoint(0, 0) for p in points]
            draw_handpose(pose_canvas, normalized)
            overlay = image.copy()
            draw_handpose(overlay, normalized)
            Image.fromarray(crop).save(out / f'hand-{i+1}-original.png')
            Image.fromarray(overlay[box[1]:box[3], box[0]:box[2]]).save(out / f'hand-{i+1}-overlay.png')
            rows.append({'hand':i+1, 'detector_box':detection_box, 'crop':box,
                         'valid_keypoints':int(sum(p.x != 0 and p.y != 0 for p in normalized)),
                         'keypoints':[[p.x*width, p.y*height, int(p.x != 0 and p.y != 0)] for p in normalized]})
            print(f'第 {i+1} 只手：作者模型 GPU 推理完成，{rows[-1]["valid_keypoints"]}/21 点，待实际看图', flush=True)
    finally:
        guard.remove()
    if len(inference_calls) != 4 * len(rows):
        raise RuntimeError('作者多尺度手部模型未完整执行')
    Image.fromarray(pose_canvas).save(out / 'hands.png')
    active = np.any(pose_canvas != 0, axis=2)
    overlay = image.copy()
    overlay[active] = pose_canvas[active]
    Image.fromarray(overlay).save(out / 'overlay.png')
    report = {'acceptance':'pending_visual_review', 'source_image':source.relative_to(ROOT).as_posix(),
              'crop_factor':args.crop_factor, 'model':model_record, 'model_inference_device':'cuda:0',
              'actual_cuda_forward_calls':len(inference_calls), 'hands':rows,
              'author_source':'https://github.com/comfyorg/comfyui-controlnet-aux', 'revision':revision,
              'scope':'Verified anime hand detections feed unchanged author CNN; no body detector or constructed joints.'}
    (out / 'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (out.parent / 'latest.json').write_text(json.dumps({'path':str(out),'acceptance':'pending_visual_review'},indent=2),encoding='utf-8')
    print(str(out), flush=True)


if __name__ == '__main__':
    main()
