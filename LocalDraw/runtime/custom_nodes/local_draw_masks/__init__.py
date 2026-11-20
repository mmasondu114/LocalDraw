"""Studio-managed ComfyUI node: GPU segmentation within an explicit role region."""
import hashlib
import json
import os
from pathlib import Path

os.environ.setdefault('YOLO_AUTOINSTALL', 'false')
os.environ.setdefault('OMP_NUM_THREADS', '2')


class LocalDrawPersonMask:
    RETURN_TYPES = ('MASK',)
    FUNCTION = 'segment'
    CATEGORY = 'local_draw'
    _predictor = None

    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'image': ('IMAGE',),
                'x': ('INT', {'default': 0, 'min': 0}),
                'y': ('INT', {'default': 0, 'min': 0}),
                'width': ('INT', {'default': 512, 'min': 1}),
                'height': ('INT', {'default': 512, 'min': 1})}}

    def segment(self, image, x, y, width, height):
        import torch
        import numpy as np
        import cv2
        from ultralytics import SAM, settings
        if not torch.cuda.is_available():
            raise RuntimeError('人物分割需要 CUDA 显卡，禁止回退到 CPU')
        settings.update({'sync': False})
        cfg = json.loads(Path(__file__).with_name('settings.json').read_text(encoding='utf-8'))
        masks = []
        for pixels in image:
            h, w = pixels.shape[:2]
            right, bottom = min(w, x + width), min(h, y + height)
            if x >= right or y >= bottom:
                raise RuntimeError('人物分割区域超出画面')
            # Ultralytics consumes BGR; image conversion is not model inference.
            bgr = (pixels.detach().cpu().numpy()[..., ::-1] * 255).clip(0, 255).astype(np.uint8)
            cutout = x == 0 and y == 0 and right == w and bottom == h
            if cutout:
                # SAM can mistake pale anime clothes and skin for background.
                # A dedicated anime network preserves the complete foreground.
                from .anime_mask import segment
                alpha = segment(pixels, cfg['anime'])
                support = (alpha > .5).astype(np.uint8)
                count, labels, stats, _ = cv2.connectedComponentsWithStats(support, 8)
                if count < 2:
                    raise RuntimeError('未识别出动漫角色轮廓，请换种子重试')
                largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
                body = (labels == largest).astype(np.uint8)
                ratio = body.sum() / (w*h)
                if not .015 <= ratio <= .95:
                    raise RuntimeError('动漫角色轮廓不明确，已停止合成以保护背景')
                # Retain the model's soft alpha at the main body's edge.
                edge = cv2.dilate(body, np.ones((5,5), np.uint8))
                masks.append(torch.from_numpy((alpha*edge).astype(np.float32)))
                continue
            if self.__class__._predictor is None:
                model = Path(cfg['model'])
                with model.open('rb') as stream:
                    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
                if digest != cfg['sha256']:
                    raise RuntimeError('人物分割模型校验失败')
                self.__class__._predictor = SAM(str(model))
            predictor = self.__class__._predictor
            if predictor.predictor is not None:
                predictor.predictor.reset_image()
            result = predictor.predict(bgr, bboxes=[[x, y, right, bottom]],
                                       device=0, retina_masks=True, verbose=False)[0]
            if next(predictor.predictor.model.parameters()).device.type != 'cuda':
                raise RuntimeError('人物分割未使用 CUDA，禁止继续')
            if result.masks is None or len(result.masks.data) != 1:
                raise RuntimeError('未识别出角色轮廓，请调整角色位置或换种子重试')
            raw = result.masks.data[0].detach().cpu().numpy().astype(np.uint8)
            bounded = np.zeros((h, w), np.uint8)
            bounded[y:bottom, x:right] = raw[y:bottom, x:right]
            count, labels, stats, _ = cv2.connectedComponentsWithStats(bounded, 8)
            if count < 2:
                raise RuntimeError('角色轮廓为空，请调整角色位置或换种子重试')
            largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
            mask = (labels == largest).astype(np.uint8)
            ratio = mask.sum() / ((right-x)*(bottom-y))
            if not .015 <= ratio <= .95:
                raise RuntimeError('角色轮廓不明确，已停止局部绘制以保护背景')
            padding = max(6, round(min(width, height) * .035))
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (padding*2+1, padding*2+1))
            mask = cv2.dilate(mask, kernel).astype(np.float32)
            mask = cv2.GaussianBlur(mask, (0, 0), max(1, padding/4))
            final = np.zeros((h, w), np.float32)
            final[y:bottom, x:right] = mask[y:bottom, x:right]
            # Native SolidMask / MaskComposite use CPU masks even in --gpu-only;
            # this is compositing data, while all SAM inference above stays CUDA.
            masks.append(torch.from_numpy(final))
        return (torch.stack(masks),)


class LocalDrawPersonLayer:
    """Fit a solo portrait into its region with one detected face and intact alpha."""
    RETURN_TYPES = ('IMAGE', 'MASK')
    FUNCTION = 'layout'
    CATEGORY = 'local_draw'
    _detector = None

    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'image': ('IMAGE',),
                'width': ('INT', {'default':512, 'min':1}),
                'height': ('INT', {'default':832, 'min':1}),
                'framing': (['portrait', 'full body'],)}}

    def layout(self, image, width, height, framing):
        import cv2
        import numpy as np
        import torch
        from ultralytics import YOLO
        if not torch.cuda.is_available():
            raise RuntimeError('人物检测需要 CUDA 显卡，禁止回退到 CPU')
        cfg = json.loads(Path(__file__).with_name('settings.json').read_text(encoding='utf-8'))['face']
        if self.__class__._detector is None:
            path = Path(cfg['model'])
            with path.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            if digest != cfg['sha256']:
                raise RuntimeError('人物检测模型校验失败')
            self.__class__._detector = YOLO(str(path), task='detect')
        detector = self.__class__._detector
        images, masks = [], []
        for pixels in image:
            h, w = pixels.shape[:2]
            alpha = LocalDrawPersonMask().segment(pixels[None], 0, 0, w, h)[0][0].numpy()
            rgb = pixels.detach().cpu().numpy()
            results = detector.predict((rgb[..., ::-1]*255).astype(np.uint8),
                       device=0, conf=cfg.get('confidence', .3), imgsz=640,
                       max_det=8, verbose=False)[0]
            if next(detector.predictor.model.parameters()).device.type != 'cuda':
                raise RuntimeError('人物检测未使用 CUDA，禁止继续')
            if len(results.boxes) != 1:
                raise RuntimeError('独立角色图未识别出恰好一张脸，请换种子重试以避免多余人物')
            fx1, fy1, fx2, fy2 = results.boxes.xyxy[0].detach().cpu().tolist()
            support = alpha > .5
            ys, xs = np.nonzero(support)
            if framing == 'full body':
                scale = min(width*.96/(xs.max()-xs.min()+1), height*.96/(ys.max()-ys.min()+1))
                tx = width/2 - (xs.min()+xs.max())/2*scale
                ty = height*.98 - ys.max()*scale
            else:
                # Face height, rather than the training image's zoom, sets scale.
                # Keep the head's upper silhouette within the explicit region.
                hy, hx = np.nonzero(support[:min(h, int(fy2)+1)])
                scale = min(height*.23/(fy2-fy1), width*.96/(hx.max()-hx.min()+1))
                tx = width/2 - (fx1+fx2)/2*scale
                tx = max(-hx.min()*scale, min(tx, width-1-hx.max()*scale))
                ty = max(height*.02-hy.min()*scale, height*.34-(fy1+fy2)/2*scale)
            transform = np.array([[scale, 0, tx], [0, scale, ty]], dtype=np.float32)
            images.append(torch.from_numpy(cv2.warpAffine(rgb, transform, (width, height), flags=cv2.INTER_LINEAR)))
            masks.append(torch.from_numpy(cv2.warpAffine(alpha, transform, (width, height), flags=cv2.INTER_LINEAR)))
        return (torch.stack(images), torch.stack(masks))


NODE_CLASS_MAPPINGS = {'LocalDrawPersonMask': LocalDrawPersonMask,
                      'LocalDrawPersonLayer': LocalDrawPersonLayer}
NODE_DISPLAY_NAME_MAPPINGS = {'LocalDrawPersonMask': '人物轮廓（GPU）',
                             'LocalDrawPersonLayer': '合照人物比例（GPU）'}
