"""Evaluate the author's larger hand model using the existing studio repair flow."""
from pathlib import Path
import argparse
import shutil
import sys
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from comic_workflow import comfyui,engine as e,hand_detail


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser()
    parser.add_argument('--anime',action='store_true')
    parser.add_argument('--source')
    args=parser.parse_args()
    comparison=Path(e.read(ROOT/'data/test-results/hand-detector-comparison/latest.json')['path'])
    chosen=next(r for r in e.read(comparison/'results.json') if r['file'].endswith('hand_yolov8s.pt'))
    if args.anime:
        chosen=e.read(ROOT/'data/lora-curation/research/anime-hand-detectors-verified.json')[0]
    out=ROOT/'data/test-results/published-hand-repair'/e.stamp();out.mkdir(parents=True)
    source=ROOT/'data/test-results/native-studio-batch/20261007-095103-898360/base.png'
    if args.source:
        source=ROOT/args.source
    shutil.copyfile(source,out/'result.png')
    settings={**hand_detail.config(),'detector':chosen['file'],'sha256':chosen['sha256'],
              'revision':chosen['revision'],'source':chosen.get('source','https://huggingface.co/Bingsu/adetailer')}
    if args.anime:
        settings.update(confidence=.5,image_size=1280,max_regions=8)
    e.save(out/'source-provenance.json',{'source':chosen.get('source','https://huggingface.co/Bingsu/adetailer'),
        'detector':chosen,'settings':settings,'base_image':source.relative_to(ROOT).as_posix(),
        'scope':'Actual source image; published author detector, reviewed confidence and input scale, existing GPU inpainting flow. Production settings unchanged.'})
    manager=comfyui.ComfyManager()
    try:
        manager.start()
        e.save(out/'gpu-system.json',comfyui.api('/system_stats'))
        inventory=comfyui.discover()['lora_files']
        style=[{'file':inventory['原神/动画截图风格_Eufoniuzs_Anime_Screencap_illustrious_3-000010.safetensors'],
                'weight':.35,'clip_weight':.35}]
        with patch.object(hand_detail,'config',return_value=settings):
            report=hand_detail.enhance(out/'result.png','waiIllustriousSDXL_v170.safetensors',comfyui.config(),71008,style,lambda event:None)
        e.save(out/'report.json',report)
        e.save(ROOT/'data/test-results/published-hand-repair/latest.json',{'path':str(out),'acceptance':'pending_visual_review'})
        print('作者较大手部模型已执行 GPU 局部修复：'+str(report['status'])+'，'+str(len(report['passes']))+' 个区域',flush=True)
    finally:manager.stop()


if __name__=='__main__':main()
