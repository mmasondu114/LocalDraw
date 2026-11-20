"""Exercise the BV SDXL attention/LoRA backend with recorded source changes."""
import argparse
import json
import shutil
import re
from pathlib import Path
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from comic_workflow import comfyui,engine as e,regional_lora,reference_images,lora_binding,regional_prompt

PIN='dc7af1bec7461b70ae1e100f3c02e5cff2169869'


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser()
    parser.add_argument('--models',nargs='+',choices=['noob','illustrious','wai'],default=['noob'])
    parser.add_argument('--counts',nargs='+',type=int,default=[4])
    parser.add_argument('--seeds',nargs='+',type=int,default=[71007,71008])
    parser.add_argument('--end',type=float,default=1.0)
    parser.add_argument('--weight',type=float,default=.7)
    parser.add_argument('--pose',action='store_true')
    parser.add_argument('--training-prompts',action='store_true')
    parser.add_argument('--negative-mode',choices=['auto','zero_out'],default='auto')
    parser.add_argument('--screencap-weight',type=float,default=0)
    parser.add_argument('--pose-margin',type=float,default=.07)
    parser.add_argument('--reference-outfits',action='store_true')
    parser.add_argument('--official-art-candidates',action='store_true')
    parser.add_argument('--candidate-characters',nargs='+',choices=['甘雨','莫娜'])
    parser.add_argument('--pose-strength',type=float)
    parser.add_argument('--pose-end',type=float)
    parser.add_argument('--extra-negative',default='')
    parser.add_argument('--references',action='store_true')
    parser.add_argument('--reference-weight',type=float,default=.3)
    parser.add_argument('--reference-start',type=float,default=0.0)
    parser.add_argument('--studio-pipeline',action='store_true')
    parser.add_argument('--width',type=int,default=1216)
    parser.add_argument('--height',type=int,default=832)
    parser.add_argument('--mask-feather',type=float,default=.015)
    parser.add_argument('--regional-count-only',action='store_true')
    args=parser.parse_args()
    plugin=ROOT/'runtime/custom_nodes/bv_nodepack'
    revision=subprocess.check_output(['git','-C',str(plugin),'rev-parse','HEAD'],text=True).strip()
    e.need(revision==PIN,'BV 节点版本与验收试验不符')
    out=ROOT/'data/test-results/bv-regional'/e.stamp();out.mkdir(parents=True)
    local_patch=subprocess.check_output(['git','-C',str(plugin),'diff','--','py/util/regional/sdxl_attention.py','py/nodes/bv_regional.py'],text=True)
    e.save(out/'source-provenance.json',{'repository':'https://github.com/BlackVortexAI/bv_nodepack',
        'revision':revision,'local_patch':local_patch,'arguments':vars(args),
        'patch_basis':'Author clip_with_hooks helper and existing Krea2 scoped text encoding.'})
    if args.references:
        ip_plugin=ROOT/'runtime/custom_nodes/ComfyUI_IPAdapter_plus'
        e.save(out/'reference-source-provenance.json',{
            'repository':'https://github.com/cubiq/ComfyUI_IPAdapter_plus',
            'revision':subprocess.check_output(['git','-C',str(ip_plugin),'rev-parse','HEAD'],text=True).strip(),
            'local_patch':subprocess.check_output(['git','-C',str(ip_plugin),'diff','--','CrossAttentionPatch.py'],text=True),
            'bridge_source':(ROOT/'scripts/comfy_gpu_hooks_node.py').read_text(encoding='utf-8'),
            'basis':'Keep author IPAdapter additive callbacks; use BV as text base attention. Map middle block to current ComfyUI index 0.',
            'official_references':e.read(ROOT/'data/lora-curation/research/qa-official-references.json')})
        frozen=out/'official-references';frozen.mkdir()
        for reference in e.read(ROOT/'data/lora-curation/research/qa-official-references.json'):
            for key in ('file','clip_file'):
                source=ROOT/'data/comfy-input'/reference[key]
                shutil.copyfile(source,frozen/source.name)
    selected={r['character']['name']:r['selected'] for r in e.read(ROOT/'data/lora-curation/selection.json')['characters'] if r['selected']}
    if args.official_art_candidates:
        for candidate in e.read(ROOT/'data/lora-curation/research/official-art-comparison-candidates.json'):
            if args.candidate_characters and candidate['character'] not in args.candidate_characters:
                continue
            e.need(candidate['evidence'].get('full_file_verified') and candidate['evidence'].get('structurally_compatible'),
                   '官方立绘候选尚未完成权重校验')
            selected[candidate['character']]=candidate
    definitions=[('甘雨','g4n7u, 1girl, blue hair, long hair, red and black horns, purple eyes, neck bell, detached white sleeves, black leotard, black pantyhose, white and gold tabard, chinese knot'),
                 ('刻晴',selected['刻晴']['triggers'][0].split(',')[0]+', 1girl, purple hair, twintails, purple eyes, purple dress, white trim, gold ornaments, black pantyhose'),
                 ('琴','lgcjng, 1girl, blonde hair, ponytail, blue eyes, black shirt, white vest, white pants, blue cape, black gloves, gold ornaments'),
                 ('莫娜','zzMona, 1girl, purple hair, twintails, turquoise eyes, large blue witch hat, star ornaments, purple leotard, black pantyhose, blue cape, red cape lining')]
    if args.training_prompts:
        # Use the selected authors' outfit-specific training tokens. The Mona
        # eye color follows the official reference rather than its public typo.
        definitions[1]=('刻晴',selected['刻晴']['triggers'][0]+', 1girl')
        definitions[2]=('琴',selected['琴']['triggers'][1]+', 1girl')
        definitions[3]=('莫娜',selected['莫娜']['triggers'][1].replace('grey eyes','turquoise eyes')+', large blue witch hat, star ornaments, purple leotard, black pantyhose, blue cape, red cape lining, 1girl')
    if args.reference_outfits:
        # The supplied official Mona art depicts the fur-collared blue outfit;
        # its eyes are blue-green. Do not mix its outfit with the older purple one.
        ganyu_trigger=selected['甘雨']['triggers'][0].split(',')[0]
        mona_trigger=selected['莫娜']['triggers'][0].split(',')[0]
        definitions[0]=('甘雨',ganyu_trigger+', 1girl, blue hair, long hair, red and black horns, purple eyes, neck bell, bare shoulders, detached white sleeves, black leotard, black pantyhose, white and gold pelvic curtain, chinese knot')
        definitions[3]=('莫娜',mona_trigger+', 1girl, dark purple hair, twintails, turquoise eyes, large blue witch hat, gold star ornaments, star earrings, blue leotard, gold trim, covered navel, black pantyhose, purple detached sleeves, black gloves, blue capelet, black fur collar, red cape lining, black choker, neck bow')
    negative='worst quality, low quality, bad anatomy, bad hands, extra limbs, extra people, duplicate person, collage, split screen, thick outlines, western comics, text, watermark'
    if args.extra_negative:
        negative+=', '+args.extra_negative
    manager=comfyui.ComfyManager();records=[]
    try:
        manager.start(runtime_options={'bv_regional_trial':True})
        e.save(out/'gpu-system.json',comfyui.api('/system_stats'))
        native_loras=comfyui.discover()['lora_files']
        for model in args.models:
            if records:
                manager.stop();manager.start(runtime_options={'bv_regional_trial':True})
            cfg={**comfyui.config(),'width':args.width,'height':args.height,'steps':28 if model in ('noob','wai') else 25,
                 'cfg':5.5 if model in ('noob','wai') else 3.5,'sampler_name':'euler_ancestral' if model in ('noob','wai') else 'dpmpp_2m_sde',
                 'scheduler':'normal' if model in ('noob','wai') else 'karras'}
            checkpoint={'noob':'NoobAI-XL-v1.1.safetensors','illustrious':'Illustrious-XL-v2.0.safetensors',
                        'wai':'waiIllustriousSDXL_v170.safetensors'}[model]
            for count in args.counts:
                doc_id=str(uuid.uuid4())
                scene=f'{count}girls, full body, standing side by side, outdoors, park, daylight, Japanese anime, soft cel shading, thin lineart, masterpiece, best quality'
                doc={'schema':'bv.regional','version':2,'document_id':doc_id,'title':'日漫多人角色实图验收',
                     'canvas':{'width':args.width,'height':args.height},'negative_mode':args.negative_mode,'overlap':{'mode':'joint'},
                     'prompts':{'global':{'positive_source':scene,'negative_source':negative},
                                'background':{'positive_source':'','negative_source':''}},'regions':[]}
                loras={'version':3,'entries':[],'steps':[]};roles=[]
                if args.screencap_weight:
                    scene+=', anime screencap, delicate thin lineart, gentle lighting, natural colors, detailed anime background'
                    doc['prompts']['global']['positive_source']=scene
                    loras['entries'].append({'id':str(uuid.uuid4()),'source':{'kind':'native',
                        'lora_name':'原神/动画截图风格_Eufoniuzs_Anime_Screencap_illustrious_3-000010.safetensors',
                        'model_strength':args.screencap_weight,'clip_strength':args.screencap_weight},
                        'targets':[{'scope':'global'}]})
                for i,(name,text) in enumerate(definitions[:count]):
                    identifier=str(uuid.uuid4());region=[i/count,0,(i+1)/count,1]
                    doc['regions'].append({'id':identifier,'name':name,'enabled':True,'usage':'generation','strength':1.0,'priority':i,
                        'prompts':{'positive_source':text,'negative_source':negative},'mask':{'feather':args.mask_feather},
                        'geometry':[{'id':str(uuid.uuid4()),'type':'rect','operation':'add','x':i/count,'y':0,'width':1/count,'height':1}]})
                    loras['entries'].append({'id':str(uuid.uuid4()),'source':{'kind':'native','lora_name':selected[name]['relative_path'],
                        'model_strength':args.weight,'clip_strength':args.weight},'targets':[{'scope':'region','document_id':doc_id,'region_id':identifier}]})
                    roles.append({'name':name,'region':region})
                for seed in args.seeds:
                    g=comfyui.graph(checkpoint,scene,negative,seed,cfg)
                    g['270']={'class_type':'BV Regional Prompt','inputs':{'regional_json':json.dumps(doc,ensure_ascii=False),
                                                                         'lora_v3_config_json':json.dumps(loras,ensure_ascii=False)}}
                    g['271']={'class_type':'BV Regional SDXL Attention','inputs':{'model':['1',0],'clip':['1',1],'regional':['270',0],
                        'attention_strength':1.0,'start_percent':0.0,'end_percent':args.end}}
                    g['290']={'class_type':'LocalDrawGpuHooks','inputs':{'model':['271',0]}}
                    g['5']['inputs'].update(model=['290',0],positive=['271',1],negative=['271',2])
                    if args.studio_pipeline:
                        style=[]
                        if args.screencap_weight:
                            style=[{'file':'原神/动画截图风格_Eufoniuzs_Anime_Screencap_illustrious_3-000010.safetensors',
                                    'weight':args.screencap_weight,'clip_weight':args.screencap_weight}]
                        g=comfyui.graph(checkpoint,scene,negative,seed,cfg,style)
                        studio_roles=[{**role,'prompt':definitions[index][1]} for index,role in enumerate(roles)]
                        passes=[{'name':role['name'],'positive':role['prompt'],'region':role['region'],'loras':[
                            {'file':selected[role['name']]['relative_path'],'weight':args.weight,'clip_weight':args.weight}]}
                            for role in studio_roles]
                        studio_cfg={**cfg,'regional_lora_backend':'bv_sdxl','regional_lora_hooks':True,
                                    'regional_auto_pose':False,'regional_mask_feather':args.mask_feather}
                        regional_prompt.apply(g,{'regional_characters':True,'text':scene,'characters':studio_roles},studio_cfg,passes)
                        lora_binding.apply_passes(g,passes,style,studio_cfg,seed,negative,False,studio_roles)
                        if args.regional_count_only:
                            diagnostic_doc=json.loads(g['270']['inputs']['regional_json'])
                            shared=diagnostic_doc['prompts']['global']['positive_source']
                            diagnostic_doc['prompts']['global']['positive_source']=re.sub(
                                r'\b(?:solo|[1-9]\s*(?:people|girls?|boys?|others?)|(?:two|three|four|five|six)\s+(?:people|girls|boys))\b','',shared,flags=re.I)
                            g['270']['inputs']['regional_json']=json.dumps(diagnostic_doc,ensure_ascii=False)
                    if args.pose:
                        pose_cfg={**cfg,'regional_pose_margin':args.pose_margin}
                        regional_lora.apply_pose(g,regional_lora.standing_pose(roles,pose_cfg),cfg)
                        g['280']['inputs'].pop('areas',None)
                        if args.pose_strength is not None:
                            g['282']['inputs']['strength']=args.pose_strength
                        if args.pose_end is not None:
                            g['282']['inputs']['end_percent']=args.pose_end
                        g['289']={'class_type':'PreviewImage','inputs':{'images':['280',0]}}
                    if args.references:
                        refs=[{'image':'qa-official-references/'+ref+'-clip.png',
                               'name':role['name'],'region':role['region'],
                               'weight':args.reference_weight,'start_at':args.reference_start,'end_at':.8}
                              for ref,role in zip(('Ganyu','Keqing','Jean','Mona'),roles)]
                        reference_images.apply_references(g,refs,cfg)
                    assert sum(n['class_type']=='KSampler' for n in g.values())==1
                    assert not any(n['class_type'] in ('ImageCrop','ImageCompositeMasked','LocalDrawPersonLayer') for n in g.values())
                    for node in g.values():
                        if node['class_type'] in ('LoraLoader','CreateHookLora'):
                            name=node['inputs']['lora_name']
                            node['inputs']['lora_name']=native_loras[comfyui.lora_file_key(name)]
                    key=f'{model}-{count}-people-{seed}';e.save(out/(key+'-workflow.json'),g)
                    started=time.monotonic();job=comfyui.api('/prompt',{'prompt':g});e.need(not job.get('node_errors'),str(job.get('node_errors')))
                    history=None
                    while time.monotonic()-started<900:
                        history=comfyui.api('/history/'+job['prompt_id']).get(job['prompt_id'])
                        if history:break
                        time.sleep(.5)
                    e.save(out/(key+'-history.json'),history)
                    e.need(history and history.get('status',{}).get('status_str')=='success','BV GPU 实图执行失败：'+key)
                    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
                    descriptor=history['outputs']['7']['images'][0]
                    with opener.open(cfg['endpoint']+'/view?'+urllib.parse.urlencode(descriptor),timeout=30) as response:
                        (out/(key+'.png')).write_bytes(response.read())
                    records.append({'key':key,'expected_people':count,'characters':[r['name'] for r in roles],
                                    'seconds':round(time.monotonic()-started,2),'acceptance':'pending_visual_review'})
                    e.save(out/'renders.json',records);e.save(ROOT/'data/test-results/bv-regional/latest.json',{'path':str(out),'acceptance':'pending_visual_review'})
                    print(key+' 已生成',flush=True)
    finally:manager.stop()


if __name__=='__main__':main()
