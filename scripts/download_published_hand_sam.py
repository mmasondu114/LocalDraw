"""Obtain author SAM checkpoints for isolated GPU hand-mask comparisons."""
import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from comic_workflow import engine as e
from comic_workflow.downloader import download


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', choices=['meta', 'anime'], required=True)
    args = parser.parse_args()
    if args.model == 'meta':
        model = {'name': 'sam_vit_b_01ec64.pth', 'size': 375042383,
                 'sha256': 'ec2df62732614e57411cdcf32a23ffdf28910380d03139ee0f4fcbe91eb8c912',
                 'author_source': 'https://github.com/facebookresearch/segment-anything',
                 'author_url': 'https://dl.fbaipublicfiles.com/segment_anything/sam_vit_b_01ec64.pth',
                 'mirror_source': 'https://huggingface.co/ybelkada/segment-anything',
                 'revision': '7790786db131bcdc639f24a915d9f2c331d843ee',
                 'url': 'https://huggingface.co/ybelkada/segment-anything/resolve/7790786db131bcdc639f24a915d9f2c331d843ee/checkpoints/sam_vit_b_01ec64.pth'}
    else:
        model = {'name': 'handsegnet_vit_b_best.safetensors', 'size': 374979240,
                 'sha256': '64b35e5ee09aac8737e2554f15e73503f94ce9bf443dde4864255e14b7ca9c14',
                 'author_source': 'https://github.com/Ov3rLoRd-MLEngineer/handsegnet-anime-sdxl',
                 'revision': '77ff734683306141e56aef9d491958a82508b41a',
                 'url': 'https://huggingface.co/Ov3rLoRd-MLEngineer/handsegnet-anime-sdxl/resolve/77ff734683306141e56aef9d491958a82508b41a/handsegnet_vit_b_best.safetensors'}
    destination = ROOT / 'data/hand-segmentation-diagnostic'
    download(destination, 8, url=model['url'], size=model['size'], sha=model['sha256'], name=model['name'])
    with (destination / model['name']).open('rb') as stream:
        e.need(hashlib.file_digest(stream, 'sha256').hexdigest() == model['sha256'], '手部遮罩权重完整 SHA256 校验失败')
    model.update(complete_sha256_verified=True, model_inference_run=False, production_adoption=False,
                 file=(destination / model['name']).relative_to(ROOT).as_posix())
    e.save(ROOT / f'data/lora-curation/research/hand-sam-{args.model}-verified.json', model)
    print('公开手部遮罩模型已完整校验：' + args.model, flush=True)


if __name__ == '__main__':
    main()
