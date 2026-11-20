"""Download pinned MeshGraphormer models for isolated CUDA diagnostics."""
import argparse
import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from comic_workflow import engine as e
from comic_workflow.downloader import download


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', choices=['graphormer', 'hrnet'], required=True)
    args = parser.parse_args()
    filename, size, sha = {
        'graphormer': ('graphormer_hand_state_dict.bin', 855658184, 'b78081d105dc730c179eb0e2366bea999ceb4363070bb3aa1ae31fcf878a8a41'),
        'hrnet': ('hrnetv2_w64_imagenet_pretrained.pth', 513111608, '41ed675bcd1f4f4b62a49bad64901f08f8b67ed744b715da87738f926dae685c'),
    }[args.model]
    revision = 'f0917f0595ecb7f6435f49e4b2b28f8dd68ab0cb'
    url = f'https://huggingface.co/hr16/ControlNet-HandRefiner-pruned/resolve/{revision}/{filename}'
    destination = ROOT / 'data/mesh-hand-diagnostic'
    download(destination, 8, url=url, size=size, sha=sha, name=filename)
    path = destination / filename
    with path.open('rb') as stream:
        e.need(hashlib.file_digest(stream, 'sha256').hexdigest() == sha, '手部网格权重完整 SHA256 校验失败')
    e.save(ROOT / f'data/lora-curation/research/mesh-hand-{args.model}-verified.json', {
        'filename': filename, 'size': size, 'sha256': sha, 'revision': revision,
        'file': path.relative_to(ROOT).as_posix(), 'complete_sha256_verified': True,
        'author': 'https://github.com/microsoft/MeshGraphormer',
        'port': 'https://github.com/Fannovel16/comfyui_controlnet_aux',
        'model_repository': 'https://huggingface.co/hr16/ControlNet-HandRefiner-pruned',
        'inference_run': False, 'production_adoption': False})
    print('公开手部网格模型已完整校验：' + args.model, flush=True)


if __name__ == '__main__':
    main()
