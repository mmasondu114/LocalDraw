"""Actual GPU renders before/after regional refinement, with saved workflows."""
import copy
import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from PIL import Image, ImageDraw

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from comic_workflow import comfyui, engine as e, lora_binding, regional_prompt


def legacy_passes(graph,passes,cfg,seed,negative):
    """The pre-fix full-frame, context-free, high-denoise implementation."""
    previous=graph['7']['inputs']['images']
    for n,spec in enumerate(passes):
        b=300+n*40
        def node(o,t,**inputs):
            k=str(b+o);graph[k]={'class_type':t,'inputs':inputs};return [k,0]
        model,clip=['1',0],['1',1]
        for i,l in enumerate(spec['loras']):
            model=node(i,'LoraLoader',model=model,clip=clip,lora_name=l['file'],strength_model=l['weight'],strength_clip=l['clip_weight']);clip=[model[0],1]
        pos=node(10,'CLIPTextEncode',text=spec['positive'],clip=clip);neg=node(11,'CLIPTextEncode',text=negative,clip=clip)
        w,h=cfg['width'],cfg['height'];x1,y1,x2,y2=spec['region'];x,y=round(w*x1),round(h*y1)
        blank=node(12,'SolidMask',value=0.0,width=w,height=h)
        rect=node(13,'SolidMask',value=1.0,width=round(w*x2)-x,height=round(h*y2)-y)
        mask=node(14,'MaskComposite',destination=blank,source=rect,x=x,y=y,operation='add')
        pixels=node(16,'VAEEncode',pixels=previous,vae=['1',2]);latent=node(17,'SetLatentNoiseMask',samples=pixels,mask=mask)
        samples=node(18,'KSampler',model=model,positive=pos,negative=neg,latent_image=latent,seed=seed+n+1,steps=cfg['steps'],cfg=cfg['cfg'],sampler_name=cfg['sampler_name'],scheduler=cfg['scheduler'],denoise=.65)
        im=node(19,'VAEDecode',samples=samples,vae=['1',2]);previous=node(20,'ImageCompositeMasked',destination=previous,source=im,x=0,y=0,resize_source=False,mask=mask)
    graph['7']['inputs']['images']=previous


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    out=ROOT/'data/test-results/multi-character-gpu'/e.stamp();out.mkdir(parents=True)
    selection=e.read(ROOT/'data/lora-curation/selection.json')['characters']
    selected={r['character']['name']:r['selected'] for r in selection if r['selected']}
    definitions=[
        ('甘雨',r'ganyu \(genshin impact\), 1girl, blue hair, long hair, red and black horns, purple eyes, cowbell, black gloves, white and blue dress'),
        ('刻晴',selected['刻晴']['triggers'][0]+', 1girl'),
        ('琴',r'jean \(genshin impact\), 1girl, blonde hair, ponytail, blue eyes, white pants, blue and white vest, black gloves'),
        ('莫娜',r'mona \(genshin impact\), 1girl, dark purple hair, twintails, green eyes, red witch hat, purple bodysuit, purple cape'),
    ]
    cfg={**comfyui.config(),'width':1216,'height':832,'steps':28,'cfg':5,'sampler_name':'dpmpp_2m','scheduler':'karras'}
    checkpoint='waiIllustriousSDXL_v170.safetensors';records=[]
    manager=comfyui.ComfyManager()
    try:
        manager.start();stats=comfyui.api('/system_stats');e.save(out/'gpu-system.json',stats)
        queue=comfyui.api('/queue');e.need(not queue.get('queue_running') and not queue.get('queue_pending'),'等待已有绘图结束后再验证')
        native=comfyui.discover()['lora_files']
        parser=argparse.ArgumentParser();parser.add_argument('--counts',nargs='+',type=int,default=[2,3,4]);args=parser.parse_args()
        for count in args.counts:
            chars=[];passes=[]
            for i,(name,prompt) in enumerate(definitions[:count]):
                file=selected[name]['relative_path'];path=ROOT/'models/loras'/file
                e.need(path.is_file() and path.stat().st_size==selected[name]['bytes'],name+' LoRA 尚未完整')
                region=[i/count,0,(i+1)/count,1]
                chars.append({'name':name,'prompt':prompt,'region':region})
                passes.append({'name':name,'positive':prompt,'region':region,'loras':[{'file':native[file],'weight':.65,'clip_weight':.65,'trigger_words':''}]})
            text=f'{count}girls, standing side by side, medium shot, outdoors, park, lawn, trees, clear blue sky, daylight, eye level, masterpiece, best quality, absurdres'
            negative='lowres, worst quality, low quality, bad anatomy, bad hands, extra digits, fewer digits, text, watermark, blurry'
            for seed in ([71007,81007] if count==2 else [71007]):
                for mode in ['before','after']:
                    g=comfyui.graph(checkpoint,text,negative,seed,cfg)
                    regional_prompt.apply(g,{'regional_characters':True,'characters':chars,'text':text},{**cfg,'parallel_layers':mode=='after'},passes)
                    if mode=='before':
                        if '203' in g:
                            g['5']['inputs']['negative']=g['203']['inputs']['negative']
                            for item in g.values():
                                if item.get('class_type')=='ConditioningCombine' and item['inputs']['conditioning_1']==['203',0]:item['inputs']['conditioning_1']=['2',0]
                            for node_id in ['200','201','202','203']:g.pop(node_id)
                        g['2']['inputs']['text']=text;g['3']['inputs']['text']=negative
                        legacy_passes(g,passes,cfg,seed,negative)
                    else:lora_binding.apply_passes(g,passes,[],cfg,seed,negative,add_triggers=False)
                    if mode=='after' and g['2'].get('_meta',{}).get('parallel_layers'):
                        for n in range(count):
                            g[str(950+n)]={'class_type':'SaveImage','inputs':{'images':[str(327+n*40),0],'filename_prefix':'comic-studio/verify-role-'+str(n)}}
                    key=f'{count}-people-{seed}-{mode}';e.save(out/(key+'-workflow.json'),g)
                    start=time.monotonic();job=comfyui.api('/prompt',{'prompt':g});e.need(not job.get('node_errors'),str(job.get('node_errors')))
                    prompt_id=job['prompt_id'];record=None
                    while time.monotonic()-start<300:
                        record=comfyui.api('/history/'+prompt_id).get(prompt_id)
                        if record:break
                        time.sleep(.5)
                    e.save(out/(key+'-history.json'),record)
                    e.need(record and record.get('status',{}).get('status_str')!='error','实际出图失败：'+key)
                    descriptor=record['outputs']['7']['images'][0]
                    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
                    with opener.open(cfg['endpoint']+'/view?'+urllib.parse.urlencode(descriptor),timeout=30) as response:(out/(key+'.png')).write_bytes(response.read())
                    records.append({'key':key,'expected_count':count,'seed':seed,'mode':mode,'characters':chars,'seconds':round(time.monotonic()-start,2),'file':key+'.png'})
                    e.save(out/'renders.json',records);print(key,'完成',flush=True)
        sheet=Image.new('RGB',(1280,((len(records)+1)//2)*472),'white');draw=ImageDraw.Draw(sheet)
        for i,r in enumerate(records):
            x=(i%2)*640;y=(i//2)*472
            im=Image.open(out/r['file']);im.thumbnail((630,430));sheet.paste(im,(x+(640-im.width)//2,y+34));draw.text((x+12,y+8),r['key'],fill='black')
        sheet.save(out/'comparison.jpg');print(out,flush=True)
        e.save(ROOT/'data/test-results/multi-character-gpu/latest.json',{'path':str(out),'renders':len(records),'checkpoint':checkpoint})
    finally:manager.stop()


if __name__=='__main__':main()
