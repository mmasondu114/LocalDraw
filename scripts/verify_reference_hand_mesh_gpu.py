"""Inspect existing published hand mesh on a reviewed real hand reference.

No new weights and no neural CPU fallback. The resulting depth is only a
candidate until its silhouette and projected joints have been viewed.
"""
import hashlib
import argparse
import json
from pathlib import Path
import sys
import time

from published_mesh_hand_runtime import load_native_runtime

ROOT = Path(__file__).resolve().parents[1]


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser()
    parser.add_argument('--source-image',type=Path)
    parser.add_argument('--hand-box',type=int,nargs=4)
    parser.add_argument('--reference-hand',choices=['right','left'],default='right')
    args=parser.parse_args()
    assert bool(args.source_image) == bool(args.hand_box), 'Source image and reviewed box must be supplied together'
    from datetime import datetime
    out = ROOT/'data/test-results/reference-hand-mesh'/datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    out.mkdir(parents=True)
    source = args.source_image.resolve() if args.source_image else ROOT/'data/lora-curation/research/photo-hug-references/pair-crop.jpg'
    reference = json.loads((ROOT/'data/test-results/dwpose-hands/20261007-212708-989400/keypoints.json').read_text(encoding='utf-8'))
    points = reference['people'][0]['hand_'+args.reference_hand+'_keypoints_2d']
    runtime, loading = load_native_runtime()
    import torch
    import numpy as np
    import cv2
    from PIL import Image, ImageDraw
    rgb = np.asarray(Image.open(source).convert('RGB'))
    joints = np.asarray(points).reshape(21, 3)
    valid = joints[joints[:, 2] > .3, :2]
    assert len(valid) == 21
    low, high = (np.asarray(args.hand_box[:2]),np.asarray(args.hand_box[2:])) if args.hand_box else (valid.min(axis=0), valid.max(axis=0))
    center = (low+high)/2
    length = int(max(high-low)*1.6)//2*2
    left, top = [int(v-length/2) for v in center]
    height,width=rgb.shape[:2]
    padded=np.zeros((height*2,width*2,3),dtype=np.uint8)
    padded[height//2:height//2+height,width//2:width//2+width]=rgb
    crop = padded[top+height//2:top+height//2+length,left+width//2:left+width//2+length].copy()
    assert crop.shape == (length, length, 3)
    Image.fromarray(crop).save(out/'source-hand.png')
    calls = {'count':0}

    def tensors(value):
        if isinstance(value, torch.Tensor):
            yield value
        elif isinstance(value, (list, tuple)):
            for item in value:
                yield from tensors(item)
        elif isinstance(value, dict):
            for item in value.values():
                yield from tensors(item)

    def guard(module, args, kwargs):
        values = list(tensors(args))+list(tensors(kwargs))+list(module.parameters(recurse=False))+list(module.buffers(recurse=False))
        assert all(v.device.type == 'cuda' for v in values), 'CPU neural execution forbidden'
        calls['count'] += 1

    handles = [m.register_forward_pre_hook(guard, with_kwargs=True) for model in (runtime._model, runtime.mano_model) for m in model.modules()]
    report = {'source_image':source.relative_to(ROOT).as_posix(), 'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
              'source_credit':(json.loads(source.with_suffix(source.suffix+'.json').read_text(encoding='utf-8')) if source.with_suffix(source.suffix+'.json').exists() else 'Local studio generated image') if args.source_image else 'Sandro Halank, Wikimedia Commons, CC BY-SA 4.0; recorded in photo-hug-references',
              'source_hand':'visually reviewed hand box' if args.hand_box else 'visually reviewed 21-point '+args.reference_hand+' hand, no source body pose used',
              'source_crop_box':[left, top, left+length, top+length], 'loading':loading,
              'gpu_only':True, 'production_adoption':False, 'acceptance':'pending_visual_review', 'results':[]}
    save(out/'report.json', report)
    for mirrored in (False, True):
        label = 'mirrored' if mirrored else 'original'
        model_crop = cv2.flip(crop, 1) if mirrored else crop
        model_input = cv2.resize(model_crop, (224,224), interpolation=cv2.INTER_AREA)
        started = time.perf_counter()
        depth, projected = runtime.run_inference(model_input, runtime._model, runtime.mano_model, runtime.mesh_sampler, length/224, length)
        torch.cuda.synchronize()
        item = {'label':label, 'seconds':time.perf_counter()-started, 'valid':False}
        if depth is not None and np.isfinite(depth).all() and projected is not None and torch.isfinite(projected).all().item():
            projected = projected.numpy()
            if mirrored:
                depth = cv2.flip(depth, 1)
                projected[:,0] = length-1-projected[:,0]
            depth = np.clip(depth,0,255).astype(np.uint8)
            Image.fromarray(depth).save(out/(label+'-depth.png'))
            overlay = crop.copy()
            selected = depth > 0
            overlay[selected] = (.5*overlay[selected]+.5*np.array([0,255,80])).astype(np.uint8)
            overlay = Image.fromarray(overlay)
            draw = ImageDraw.Draw(overlay)
            for index,(x,y) in enumerate(projected):
                draw.ellipse((x-2,y-2,x+2,y+2),fill='red')
                draw.text((x+3,y-5),str(index),fill='red',stroke_width=1,stroke_fill='white')
            overlay.save(out/(label+'-overlay.png'))
            item.update(valid=True, projected_joints=projected.tolist(), foreground_pixels=int(selected.sum()))
        report['results'].append(item)
        save(out/'report.json',report)
        print(label, 'finite depth:', item['valid'], flush=True)
    for handle in handles:
        handle.remove()
    report['guarded_cuda_forward_calls'] = calls['count']
    save(out/'report.json',report)
    save(out.parent/'latest.json',{'path':str(out),'acceptance':'pending_visual_review'})
    print(out.relative_to(ROOT).as_posix(),flush=True)


if __name__ == '__main__':
    main()
