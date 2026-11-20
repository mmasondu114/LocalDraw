"""Visual trial of native whole-image masked latent inpainting, on CUDA only.

Published workflow: https://comfyanonymous.github.io/ComfyUI_examples/inpaint/
This trial does not crop, paste, or composite generated character images.
"""
import argparse
import json
from pathlib import Path
import shutil
import sys
import time
import urllib.parse
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from comic_workflow import comfyui,engine as e
from verify_anime_cohesion_gpu import NEGATIVE,STYLE


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',required=True)
    parser.add_argument('--denoise',type=float,default=.65)
    parser.add_argument('--erase',action='store_true')
    args=parser.parse_args()
    source=Path(args.source).resolve()
    e.need(source.is_file(),'待核对的原图不存在')
    target=ROOT/'data/comfy-input/qa-inpaint'/source.name
    target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(source,target)
    out=ROOT/'data/test-results/anime-inpaint'/e.stamp();out.mkdir(parents=True)
    selected={r['character']['name']:r['selected'] for r in e.read(ROOT/'data/lora-curation/selection.json')['characters'] if r['selected']}
    roles=[('甘雨',0,'g4n7u, blue hair, long hair, purple eyes, red and black horns, neck bell, bare shoulders, detached white sleeves, black leotard, black pantyhose, white and gold tabard, gold trim, chinese knot'),
           ('刻晴',1,selected['刻晴']['triggers'][0]),
           ('琴',2,selected['琴']['triggers'][1]+', blue eyes'),
           ('莫娜',3,'zzMona, blue eyes, purple hair, twintails, large blue witch hat, purple leotard, black pantyhose, purple gloves, blue cape, red cape lining')]
    cfg={**comfyui.config(),'width':1216,'height':832}
    g={'1':{'class_type':'CheckpointLoaderSimple','inputs':{'ckpt_name':'Illustrious-XL-v2.0.safetensors'}},
       '10':{'class_type':'LoadImage','inputs':{'image':'qa-inpaint/'+source.name}}}
    image=['10',0];manager=comfyui.ComfyManager()
    try:
        manager.start()
        e.save(out/'gpu-system.json',comfyui.api('/system_stats'))
        native=comfyui.discover()['lora_files']
        for i,(name,index,prompt) in enumerate(roles):
            base=20+i*20
            def node(offset,kind,**inputs):
                key=str(base+offset);g[key]={'class_type':kind,'inputs':inputs};return [key,0]
            lora=node(0,'LoraLoader',model=['1',0],clip=['1',1],lora_name=native[selected[name]['relative_path']],strength_model=.8,strength_clip=.8)
            positive=node(1,'CLIPTextEncode',clip=[str(base),1],text='4girls, standing side by side, full body, outdoors, park, clear sky, daylight, '+STYLE+', masterpiece, best quality, '+prompt)
            negative=node(2,'CLIPTextEncode',clip=[str(base),1],text=NEGATIVE)
            mask=node(3,'LocalDrawPersonMask',image=image,x=304*index,y=0,width=304,height=832)
            node(9,'SaveImage',images=['10',0],filename_prefix='qa-inpaint-source') if i==0 else None
            grown=node(4,'GrowMask',mask=mask,expand=8,tapered_corners=True)
            latent=node(5,'VAEEncode',pixels=image,vae=['1',2])
            masked=node(6,'SetLatentNoiseMask',samples=latent,mask=grown)
            if args.erase:
                masked=node(11,'LocalDrawGpuInpaintEncode',pixels=image,vae=['1',2],mask=grown)
            mask_image=node(12,'MaskToImage',mask=grown)
            node(13,'SaveImage',images=mask_image,filename_prefix='qa-inpaint-mask-'+name)
            sampled=node(7,'KSampler',model=lora,positive=positive,negative=negative,latent_image=masked,
                         seed=71008+i,steps=28,cfg=5.0,sampler_name='euler_ancestral',scheduler='normal',denoise=args.denoise)
            image=node(8,'VAEDecode',samples=sampled,vae=['1',2])
            node(10,'SaveImage',images=image,filename_prefix='qa-inpaint-'+name)
        g['7']={'class_type':'SaveImage','inputs':{'images':image,'filename_prefix':'qa-inpaint-final'}}
        assert not any(n['class_type'] in ('ImageCrop','ImageCompositeMasked','LocalDrawPersonLayer') for n in g.values())
        e.save(out/'workflow.json',g)
        started=time.monotonic();job=comfyui.api('/prompt',{'prompt':g})
        e.need(not job.get('node_errors'),str(job.get('node_errors')))
        history=None
        while time.monotonic()-started<900:
            history=comfyui.api('/history/'+job['prompt_id']).get(job['prompt_id'])
            if history:break
            time.sleep(.5)
        e.save(out/'history.json',history)
        e.need(history and history.get('status',{}).get('status_str')=='success','局部重绘试验失败')
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for key,output in history['outputs'].items():
            for descriptor in output.get('images',[]):
                with opener.open(cfg['endpoint']+'/view?'+urllib.parse.urlencode(descriptor),timeout=30) as response:
                    (out/(key+'.png')).write_bytes(response.read())
        e.save(out/'review.json',{'source':str(source),'denoise':args.denoise,'erase':args.erase,'acceptance':'pending_visual_review','expected_people':4})
        print(out,flush=True)
    finally:
        manager.stop()


if __name__=='__main__':main()
