"""Download four official portraits solely for multi-character verification."""
import hashlib
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from curate_character_loras import fetch_json
from comic_workflow import engine as e


def main():
    destination=ROOT/'data/comfy-input/qa-official-references'
    destination.mkdir(parents=True,exist_ok=True)
    records=[]
    for name in ('Ganyu','Keqing','Jean','Mona'):
        data=fetch_json('https://genshin-db-api.vercel.app/api/v5/characters?query='+name+'&resultLanguage=English',name.casefold()+'-reference')
        url=data['images']['cover1']
        host=urllib.parse.urlsplit(url).hostname
        e.need(host and host.endswith(('.hoyoverse.com','.mihoyo.com')),'参考图须来自官方资源域名')
        path=destination/(name+'.png')
        if not path.exists():
            with urllib.request.urlopen(url,timeout=40) as response:path.write_bytes(response.read())
        image=Image.open(path).convert('RGBA')
        image=image.crop(image.getchannel('A').getbbox())
        # CLIP's center crop keeps the complete silhouette only on a square.
        # Alpha trimming removes transparent pixels, never character pixels.
        side=max(image.size)
        background=Image.new('RGBA',(side,side),'white')
        background.alpha_composite(image,((side-image.width)//2,(side-image.height)//2))
        prepared=destination/(name+'-clip.png')
        background.convert('RGB').save(prepared)
        records.append({'name':name,'official_url':url,'file':'qa-official-references/'+path.name,
                        'clip_file':'qa-official-references/'+prepared.name,
                        'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                        'clip_sha256':hashlib.sha256(prepared.read_bytes()).hexdigest(),
                        'clip_preprocessing':'Alpha bounds, centered on a white square; no character pixels cropped.',
                        'purpose':'多人测试；未导入工作室角色库'})
    e.save(ROOT/'data/lora-curation/research/qa-official-references.json',records)
    print('四张官方参考图已保存用于测试',flush=True)


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8');main()
