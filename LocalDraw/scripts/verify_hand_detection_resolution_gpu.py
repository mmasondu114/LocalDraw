"""Compare author hand models and documented YOLO input scales on actual renders."""
import datetime
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    import sys
    import argparse
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--anime-models', action='store_true')
    parser.add_argument('--only-latest', action='store_true')
    parser.add_argument('--confidence', type=float)
    parser.add_argument('--image-size', type=int, choices=[640,1280])
    args = parser.parse_args()
    out = ROOT / 'data/test-results/hand-detection-resolution' / datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    out.mkdir(parents=True)
    (out / 'settings').mkdir()
    os.environ['YOLO_CONFIG_DIR'] = str(out / 'settings')
    os.environ['YOLO_AUTOINSTALL'] = 'false'
    from ultralytics import YOLO, settings
    from PIL import Image, ImageDraw
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError('手部检测仅允许 CUDA，禁止 CPU 回退')
    settings.update({'sync': False})
    images = [ROOT / 'data/test-results/native-studio-batch/20261007-095103-898360/base.png',
              ROOT / 'data/test-results/published-hires/20261007-102547-732245/result.png',
              ROOT / 'data/test-results/published-hires/20261007-102920-741029/result.png']
    models = [(ROOT / 'models/hand_detection/hand_yolov8n.pt', '3991202eb69e9ddcb3b9ba80cdeb41e734ffaf844403d6c9f47d515cd88c6f29', .3),
              (ROOT / 'data/hand-detector-diagnostic/hand_yolov8s.pt', '70b540063fbc385736d8258970744a4afbc4cbf7932134bae3b24cdadeadec06', .3)]
    if args.anime_models:
        sources = json.loads((ROOT / 'data/lora-curation/research/anime-hand-detectors-verified.json').read_text(encoding='utf-8'))
        models = [(ROOT / r['file'], r['sha256'], r['author_threshold']) for r in sources]
        if args.only_latest:
            models = models[:1]
    records = []
    for path, sha, confidence in models:
        if args.confidence is not None:
            confidence = args.confidence
        assert hashlib.sha256(path.read_bytes()).hexdigest() == sha
        detector = YOLO(str(path), task='detect')
        for image_index, image in enumerate(images):
            for size in ([args.image_size] if args.image_size else (640,1280)):
                result = detector.predict(str(image), conf=confidence, device=0, imgsz=size,
                                          max_det=16, save=False, verbose=False)[0]
                device = str(next(detector.model.parameters()).device)
                assert device == 'cuda:0'
                boxes = [{'box': [float(v) for v in box.xyxy[0].tolist()],
                          'confidence': float(box.conf[0])} for box in result.boxes]
                key = f'image-{image_index}-{path.stem}-{size}'
                with Image.open(image) as source:
                    annotated = source.convert('RGB')
                draw = ImageDraw.Draw(annotated)
                for index, box in enumerate(boxes, 1):
                    draw.rectangle(box['box'], outline='lime', width=3)
                    draw.text(box['box'][:2], str(index), fill='red', stroke_width=2, stroke_fill='white')
                annotated.save(out / (key + '.png'))
                records.append({'key': key, 'image': image.relative_to(ROOT).as_posix(),
                                'model': path.relative_to(ROOT).as_posix(), 'sha256': sha,
                                'image_size': size, 'confidence': confidence, 'device': device, 'boxes': boxes,
                                'visual_review': 'pending'})
                (out / 'results.json').write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
                print(key + ': ' + str(len(boxes)) + ' candidates on ' + device, flush=True)
    (out / 'source-provenance.json').write_text(json.dumps({
        'model_source': 'https://huggingface.co/deepghs/anime_hand_detection' if args.anime_models else 'https://huggingface.co/Bingsu/adetailer',
        'inference_parameters': 'https://docs.ultralytics.com/modes/predict/',
        'scope': 'Same actual images, author detector weights, author recommended confidence for anime or .3 for general hands. Compare model version and inference input scale. Three related four-person renders; no general accuracy claim.',
        'production_detector_changed': False, 'gpu_only': True}, indent=2), encoding='utf-8')
    (ROOT / 'data/test-results/hand-detection-resolution/latest.json').write_text(
        json.dumps({'path': str(out), 'acceptance': 'pending_overlay_inspection'}, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
