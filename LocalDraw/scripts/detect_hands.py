"""GPU hand detection; fail explicitly when CUDA is unavailable."""

import argparse
import json
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--confidence", type=float, default=0.3)
    parser.add_argument("--imgsz", type=int, choices=[640, 1280], default=640)
    args = parser.parse_args()
    model_path = Path(args.model).resolve(strict=True)
    image_path = Path(args.image).resolve(strict=True)
    output = Path(args.output).resolve()
    config_dir = output.parent / "yolo-settings"
    config_dir.mkdir(parents=True, exist_ok=True)
    os.environ["YOLO_CONFIG_DIR"] = str(config_dir)
    os.environ.setdefault("YOLO_AUTOINSTALL", "false")
    os.environ.setdefault("OMP_NUM_THREADS", "2")
    from ultralytics import YOLO, settings
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError("手部检测需要 CUDA 显卡，禁止回退到 CPU")
    settings.update({"sync": False})
    detector = YOLO(str(model_path), task="detect")
    result = detector.predict(str(image_path), conf=args.confidence, device=0,
                              imgsz=args.imgsz, max_det=16, save=False, verbose=False)[0]
    if next(detector.model.parameters()).device.type != 'cuda':
        raise RuntimeError("手部检测模型未在 CUDA，禁止回退到 CPU")
    boxes = [{"box": [float(v) for v in box.xyxy[0].tolist()],
              "confidence": float(box.conf[0])} for box in result.boxes]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"size": [result.orig_shape[1], result.orig_shape[0]],
                                  "boxes": boxes, "device": "cuda:0", "image_size": args.imgsz}), encoding="utf-8")


if __name__ == "__main__":
    main()
