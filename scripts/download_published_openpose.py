"""Download the author's dedicated SDXL OpenPose checkpoint as an isolated trial."""
import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from comic_workflow import engine as e
from comic_workflow.downloader import download

if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    model = {
        'name': 'xinsir-controlnet-openpose-sdxl-1.0.safetensors',
        'page': 'https://huggingface.co/xinsir/controlnet-openpose-sdxl-1.0',
        'revision': '23f966cd5cfdd3f7729c903e243d87152162d2b7',
        'url': 'https://huggingface.co/xinsir/controlnet-openpose-sdxl-1.0/resolve/23f966cd5cfdd3f7729c903e243d87152162d2b7/diffusion_pytorch_model.safetensors',
        'size': 2502139104,
        'sha256': 'b8524e557a7df60d081f5d4a0eb109967d107df217943bf88c2d99b9ebcc06c5',
        'author_scale': 1.0,
        'production_defaults_changed': False,
        'complete_sha256_verified': False,
    }
    destination = ROOT / 'models/controlnet'
    download(destination, 8, url=model['url'], size=model['size'], sha=model['sha256'], name=model['name'])
    with (destination / model['name']).open('rb') as stream:
        e.need(hashlib.file_digest(stream, 'sha256').hexdigest() == model['sha256'], '公开 OpenPose 权重完整校验未通过')
    model['complete_sha256_verified'] = True
    e.save(ROOT / 'data/lora-curation/research/xinsir-openpose-download.json', model)
    print('作者专用 OpenPose 权重已完整校验；尚未修改默认绘图流程。', flush=True)
