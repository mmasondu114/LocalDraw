"""Review Japanese anime style and one-pass multi-person LoRA conditioning."""
import argparse
import json
from pathlib import Path
import sys
import time
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from comic_workflow import comfyui, engine as e, lora_binding, regional_prompt, regional_lora, reference_images
from comic_workflow.prompt_adapter import compile_prompt

MODELS = {'animagine':'animagine-xl-4.0-opt.safetensors',
          'illustrious':'Illustrious-XL-v2.0.safetensors',
          'noob':'NoobAI-XL-v1.1.safetensors',
          'wai':'waiIllustriousSDXL_v170.safetensors'}
STYLE = 'anime screencap, Japanese anime, delicate thin lineart, soft cel shading, natural colors, gentle lighting, detailed anime background'
NEGATIVE = ('low quality, worst quality, bad anatomy, bad hands, extra limbs, extra digits, '
            'extra people, duplicate person, western comics, american comic style, thick outlines, '
            'harsh shading, cutout, collage, split screen, triptych, character sheet, text, watermark')


def standing_pose(roles, cfg):
    return regional_lora.standing_pose(roles,cfg)


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--models', nargs='+', choices=list(MODELS), default=['animagine','noob','wai'])
    parser.add_argument('--counts', nargs='+', type=int, default=[3])
    parser.add_argument('--seeds', nargs='+', type=int, default=[71007])
    parser.add_argument('--pose',action='store_true')
    parser.add_argument('--styler',action='store_true')
    parser.add_argument('--style',default='Modern Digital Anime')
    parser.add_argument('--references',action='store_true')
    parser.add_argument('--fp8',action='store_true')
    parser.add_argument('--common-cast',action='store_true')
    parser.add_argument('--no-style-lora',action='store_true')
    parser.add_argument('--lora-weight',type=float,default=.7)
    parser.add_argument('--global-strength',type=float,default=.3)
    parser.add_argument('--regional-strength',type=float,default=.75)
    parser.add_argument('--reference-weight',type=float,default=.65)
    parser.add_argument('--hooked-negative',action='store_true')
    parser.add_argument('--local-areas-global-pose',action='store_true')
    parser.add_argument('--pose-margin',type=float,default=.07)
    parser.add_argument('--reference-outfits',action='store_true')
    parser.add_argument('--mona-official-art',action='store_true')
    args = parser.parse_args()
    out = ROOT/'data/test-results/anime-cohesion'/e.stamp(); out.mkdir(parents=True)
    cfg = {**comfyui.config(), 'width':1216,'height':832,'steps':25,'cfg':3.5,
           'sampler_name':'dpmpp_2m_sde','scheduler':'karras','person_masks':False,'regional_lora_hooks':True}
    if args.styler: cfg['regional_style']={'anime':args.style}
    cfg['regional_common_cast']=args.common_cast
    cfg['regional_global_strength']=args.global_strength
    cfg['regional_strength']=args.regional_strength
    cfg['regional_pose_margin']=args.pose_margin
    selected = {r['character']['name']:r['selected'] for r in e.read(ROOT/'data/lora-curation/selection.json')['characters'] if r['selected']}
    if args.mona_official_art:
        selected['莫娜']=next(c for c in e.read(ROOT/'data/lora-curation/research/official-art-comparison-candidates.json') if c['character']=='莫娜')
    definitions = [
        ('甘雨','g4n7u, 1girl, blue hair, long hair, horns, purple eyes, neck bell, black gloves, bare shoulders, detached sleeves, white sleeves, black leotard, black pantyhose, gold trim, chinese knot'),
        ('刻晴',selected['刻晴']['triggers'][0]+', 1girl'),
        ('琴',selected['琴']['triggers'][1]+', 1girl'),
        ('莫娜','zzMona, 1girl, blue eyes, purple hair, twintails, very long hair, large blue witch hat, star ornaments, purple leotard, black pantyhose, purple gloves, blue cape, red cape lining')]
    if args.reference_outfits:
        definitions[0]=('甘雨',selected['甘雨']['triggers'][0].split(',')[0]+', 1girl, blue hair, long hair, red and black horns, purple eyes, neck bell, bare shoulders, detached white sleeves, black leotard, black pantyhose, white and gold pelvic curtain, chinese knot')
        definitions[3]=('莫娜',selected['莫娜']['triggers'][0].split(',')[0]+', 1girl, dark purple hair, twintails, turquoise eyes, large blue witch hat, gold star ornaments, star earrings, blue leotard, gold trim, covered navel, black pantyhose, purple detached sleeves, black gloves, blue capelet, black fur collar, red cape lining, black choker, neck bow')
    records=[]; manager=comfyui.ComfyManager()
    try:
        manager.start(runtime_options={'unet_storage_dtype':'fp8_e4m3fn'} if args.fp8 else None)
        e.save(out/'gpu-system.json',comfyui.api('/system_stats'))
        native=comfyui.discover()['lora_files']
        for model in args.models:
            if records:
                manager.stop()
                manager.start(runtime_options={'unet_storage_dtype':'fp8_e4m3fn'} if args.fp8 else None)
            # The NoobAI author recommends its own sampler/CFG settings.
            cfg.update(steps=28 if model=='noob' else 25,cfg=5.5 if model=='noob' else 3.5,
                       sampler_name='euler_ancestral' if model=='noob' else 'dpmpp_2m_sde',
                       scheduler='normal' if model=='noob' else 'karras')
            checkpoint=MODELS[model]
            styles=[]
            if model in ('noob','wai','illustrious') and not args.no_style_lora:
                file='原神/动画截图风格_Eufoniuzs_Anime_Screencap_illustrious_3-000010.safetensors'
                styles=[{'file':native[file],'weight':.35,'clip_weight':.35}]
            for count in args.counts:
                roles=[]; passes=[]
                for i,(name,prompt) in enumerate(definitions[:count]):
                    region=[i/count,0,(i+1)/count,1]
                    roles.append({'name':name,'prompt':prompt,'region':region})
                    passes.append({'name':name,'positive':prompt,'region':region,'loras':[
                        {'file':native[selected[name]['relative_path']],'weight':args.lora_weight,'clip_weight':args.lora_weight}]})
                text=f'{count}girls, standing side by side, full body, eye level, outdoors, park, lawn, trees, clear sky, daylight, '+STYLE
                positive, negative, _ = compile_prompt(text, NEGATIVE, checkpoint)
                for seed in args.seeds:
                    g=comfyui.graph(checkpoint,positive,negative,seed,cfg,styles)
                    if count>=2:
                        regional_prompt.apply(g,{'text':positive,'regional_characters':True,'characters':roles},cfg,passes)
                    lora_binding.apply_passes(g,passes,styles,cfg,seed,negative,add_triggers=False)
                    if args.local_areas_global_pose:
                        # Native Area Composition samples every area in the same
                        # latent. This is not a crop/paste image operation.
                        for i,role in enumerate(roles):
                            base=300+i*40;x1,y1,x2,y2=role['region']
                            g[str(base+12)]={'class_type':'ConditioningSetAreaPercentage','inputs':{
                                'conditioning':[str(base+10),0], 'width':x2-x1,
                                'height':y2-y1,'x':x1,'y':y1,'strength':args.regional_strength}}
                            g[str(base+16)]['inputs']['conditioning']=[str(base+12),0]
                    if args.hooked_negative:
                        # ComfyUI's official masking example binds both members
                        # of each conditioning pair to that region's LoRA hooks.
                        negative_pipeline=None
                        for i,role in enumerate(roles):
                            base=300+i*40
                            g[str(base+11)]={'class_type':'CLIPTextEncode','inputs':{
                                'clip':[str(base+8),0],'text':negative}}
                            x1,y1,x2,y2=role['region']
                            inputs={'conditioning':[str(base+11),0], 'width':x2-x1,
                                    'height':y2-y1,'x':x1,'y':y1,'strength':args.regional_strength}
                            if negative_pipeline: inputs['pipeline_in']=negative_pipeline
                            g[str(base+17)]={'class_type':'ConditioningPipelineSetArea','inputs':inputs}
                            if args.local_areas_global_pose:
                                g[str(base+13)]={'class_type':'ConditioningSetAreaPercentage','inputs':{
                                    'conditioning':[str(base+11),0], 'width':x2-x1,
                                    'height':y2-y1,'x':x1,'y':y1,'strength':args.regional_strength}}
                                g[str(base+17)]['inputs']['conditioning']=[str(base+13),0]
                            negative_pipeline=[str(base+17),0]
                        g['296']={'class_type':'ConditioningPipelineCombine','inputs':{
                            'pipeline':negative_pipeline,'global_positive':['3',0],
                            'global_negative':['3',0],'global_strength':args.global_strength,'fast_mode':False}}
                        g['5']['inputs']['negative']=['296',0]
                    if args.pose: regional_lora.apply_pose(g,standing_pose(roles,cfg),cfg)
                    if args.pose and args.local_areas_global_pose:
                        # A full-canvas pose belongs to the full-canvas branch.
                        # Native ControlNet resizes its hint to each input latent;
                        # sending four poses to a narrow area packs them together.
                        g['280']['inputs'].pop('areas',None)
                        g['282']['inputs'].update(positive=['2',0],negative=['3',0])
                        g['297']['inputs'].update(global_positive=['282',0],global_negative=['282',1])
                        g['5']['inputs'].update(positive=['297',0],negative=['297',1])
                        if args.hooked_negative:
                            g['296']['inputs'].update(global_positive=['282',1],global_negative=['282',1])
                            g['5']['inputs']['negative']=['296',0]
                    if args.pose: g['283']={'class_type':'SaveImage','inputs':{'images':['280',0],'filename_prefix':'pose-review'}}
                    if args.references:
                        refs=[{'image':'qa-official-references/'+name+'-clip.png','name':role['name'],
                               'region':role['region'],'weight':args.reference_weight,'end_at':.8}
                              for name,role in zip(('Ganyu','Keqing','Jean','Mona'),roles)]
                        reference_images.apply_references(g,refs,cfg)
                    assert sum(n['class_type']=='KSampler' for n in g.values())==1
                    assert not any(n['class_type'] in ('ImageCrop','ImageCompositeMasked','LocalDrawPersonLayer') for n in g.values())
                    key=f'{model}-{count}-people-{seed}'
                    e.save(out/(key+'-workflow.json'),g)
                    started=time.monotonic(); job=comfyui.api('/prompt',{'prompt':g})
                    e.need(not job.get('node_errors'),str(job.get('node_errors')))
                    history=None
                    while time.monotonic()-started<900:
                        history=comfyui.api('/history/'+job['prompt_id']).get(job['prompt_id'])
                        if history:break
                        time.sleep(.5)
                    e.save(out/(key+'-history.json'),history)
                    e.need(history and history.get('status',{}).get('status_str')=='success','实图生成失败：'+key)
                    descriptor=history['outputs']['7']['images'][0]
                    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
                    with opener.open(cfg['endpoint']+'/view?'+urllib.parse.urlencode(descriptor),timeout=30) as response:
                        (out/(key+'.png')).write_bytes(response.read())
                    records.append({'key':key,'expected_count':count,'seed':seed,'checkpoint':checkpoint,
                                    'style_lora_weight':.35 if styles else 0,'characters':[r['name'] for r in roles],
                                    'seconds':round(time.monotonic()-started,2),'file':key+'.png'})
                    e.save(out/'renders.json',records)
                    e.save(ROOT/'data/test-results/anime-cohesion/latest.json',{'path':str(out),'renders':len(records),'acceptance':'pending_visual_review'})
                    print(key,'完成',flush=True)
    finally:
        manager.stop()


if __name__=='__main__':
    main()
