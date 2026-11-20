"""Compare the author's larger hand detectors on the actual studio output, using CUDA."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from comic_workflow import engine as e


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    with urllib.request.urlopen('https://huggingface.co/api/models/Bingsu/adetailer?blobs=true',timeout=30) as response:
        catalog = json.load(response)
    revision = catalog['sha']
    out = ROOT / 'data/test-results/hand-detector-comparison' / e.stamp()
    out.mkdir(parents=True)
    image = ROOT / 'data/test-results/native-studio-batch/20261007-095103-898360/base.png'
    records = []
    for name in ('hand_yolov8s.pt','hand_yolov9c.pt'):
        file = next(f for f in catalog['siblings'] if f['rfilename']==name)
        sha, size = file['lfs']['sha256'], file['lfs']['size']
        model = ROOT / 'data/hand-detector-diagnostic' / name
        model.parent.mkdir(parents=True,exist_ok=True)
        if not model.exists():
            partial = model.with_suffix('.pt.part')
            with urllib.request.urlopen(f'https://huggingface.co/Bingsu/adetailer/resolve/{revision}/{name}',timeout=60) as response, partial.open('wb') as stream:
                while chunk := response.read(1024*1024):
                    stream.write(chunk)
            with partial.open('rb') as stream:
                observed = hashlib.file_digest(stream,'sha256').hexdigest()
            e.need(partial.stat().st_size==size and observed==sha,'作者手部模型完整校验失败')
            partial.replace(model)
        with model.open('rb') as stream:
            e.need(hashlib.file_digest(stream,'sha256').hexdigest()==sha,'已存手部模型与作者校验不符')
        result = subprocess.run([str(ROOT/'.comfy-venv/Scripts/python.exe'),'-X','utf8',str(ROOT/'scripts/detect_hands.py'),
            '--model',str(model),'--image',str(image),'--output',str(out/(name+'.json')),'--confidence','0.3'],
            cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=90,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        (out/(name+'.log')).write_text(result.stdout+result.stderr,encoding='utf-8')
        e.need(result.returncode==0,'作者手部检测 GPU 对照失败，日志已保存')
        detection = e.read(out/(name+'.json'))
        e.need(detection['device']=='cuda:0','手部检测禁止回退 CPU')
        records.append({'file':model.relative_to(ROOT).as_posix(),'sha256':sha,'size':size,'revision':revision,
                        'detection':detection,'visual_acceptance':'pending_overlay_inspection'})
        e.save(out/'results.json',records)
        print(name+' 已完成 GPU 检测：'+str(len(detection['boxes']))+' 个候选区域',flush=True)
    e.save(out/'source-provenance.json',{'source':'https://huggingface.co/Bingsu/adetailer','revision':revision,
        'image':image.relative_to(ROOT).as_posix(),'default_detector_changed':False,
        'author_published_map50':{'hand_yolov8s.pt':.794,'hand_yolov9c.pt':.810},
        'note':'Author validation scores do not establish acceptance on our anime gloves. Actual boxes must be reviewed.'})
    e.save(ROOT/'data/test-results/hand-detector-comparison/latest.json',{'path':str(out),'acceptance':'pending_visual_review'})


if __name__=='__main__':
    main()
