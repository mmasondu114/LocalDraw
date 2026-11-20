"""Hash-pinned anime foreground segmentation; every inference runs on CUDA.

ISNet architecture and preprocessing: SkyTNT/anime-segmentation, Apache-2.0,
commit 55d874013a2811cdf59c365059174c7823acf5b4. See anime_isnet.LICENSE.
"""
import hashlib
from pathlib import Path

_model = None


def segment(pixels, cfg):
    import torch
    import torch.nn.functional as F
    from safetensors import safe_open
    from .anime_isnet import ISNetDIS

    if not torch.cuda.is_available():
        raise RuntimeError('动漫人物分割需要 CUDA 显卡，禁止回退到 CPU')
    global _model
    if _model is None:
        path = Path(cfg['model'])
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if digest != cfg['sha256']:
            raise RuntimeError('动漫人物分割模型校验失败')
        with torch.device('cuda:0'):
            model = ISNetDIS()
        with safe_open(str(path), framework='pt', device='cuda:0') as weights:
            state = {key.removeprefix('net.'): weights.get_tensor(key)
                     for key in weights.keys() if key.startswith('net.')}
        model.load_state_dict(state, strict=True)
        _model = model.eval()
    if next(_model.parameters()).device.type != 'cuda':
        raise RuntimeError('动漫人物分割未使用 CUDA，禁止继续')
    h, w = pixels.shape[:2]
    size = int(cfg.get('size', 1024))
    rh, rw = (size, max(1, int(size*w/h))) if h > w else (max(1, int(size*h/w)), size)
    py, px = (size-rh)//2, (size-rw)//2
    rgb = pixels.permute(2, 0, 1).unsqueeze(0).to(device='cuda:0', dtype=torch.float32)
    padded = torch.zeros((1, 3, size, size), device='cuda:0')
    padded[:, :, py:py+rh, px:px+rw] = F.interpolate(rgb, size=(rh, rw), mode='bilinear', align_corners=False)
    with torch.inference_mode(), torch.autocast('cuda', dtype=torch.float16):
        predicted = _model(padded)[0][0].sigmoid().float()
    predicted = predicted[:, :, py:py+rh, px:px+rw]
    alpha = F.interpolate(predicted, size=(h, w), mode='bilinear', align_corners=False)[0, 0]
    return alpha.detach().cpu().numpy()
