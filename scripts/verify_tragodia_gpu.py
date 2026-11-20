"""Load the verified Tragodia weight on CUDA and compare against its official art."""
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from comic_workflow import comfyui, engine as e, regional_lora


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    row=next(r for r in e.read(ROOT/'data/lora-curation/selection.json')['characters'] if r['character']['name']=='酒神')
    selected=row['selected'];e.need(selected is not None,'酒神尚未完成权重校验')
    out=ROOT/'data/test-results/tragodia-gpu'/e.stamp();out.mkdir(parents=True)
    prompt=(r'tragodia_\(/arknights/\), 1boy, solo, male focus, adult man, full body, standing, '
            'grey hair, hair over one eye, yellow eyes, black cat ears, golden thorn crown, '
            'white long coat, purple coat lining, black vest, dark pants, black boots, '
            'black gloves, gold ornaments, blue gemstone choker, Japanese anime, anime illustration, '
            'delicate thin lineart, natural colors, detailed clothing, outdoors, stone courtyard, daylight, '
            'masterpiece, best quality')
    negative='worst quality, low quality, extra people, duplicate person, bad anatomy, extra limbs, bad hands, cropped head, out of frame, western comics, thick outlines, text, watermark'
    e.save(out/'provenance.json',{'character':row['character'],'weight':selected,'training_trigger':selected['triggers'][0],
        'render_trigger':r'tragodia_\(/arknights/\)','official_references':'data/lora-curation/research/tragodia-official-art/provenance.json',
        'gpu_only':True,'acceptance':'pending_visual_review'})
    manager=comfyui.ComfyManager();records=[]
    try:
        manager.start()
        e.save(out/'gpu-system.json',comfyui.api('/system_stats'))
        native=comfyui.discover()['lora_files'][selected['relative_path']]
        for model,checkpoint in [('noob','NoobAI-XL-v1.1.safetensors'),('wai','waiIllustriousSDXL_v170.safetensors')]:
            if records:manager.stop();manager.start()
            cfg={**comfyui.config(),'width':832,'height':1216,'steps':28,'cfg':5.5,'sampler_name':'euler_ancestral','scheduler':'normal'}
            for seed in (71007,71008):
                g=comfyui.graph(checkpoint,prompt,negative,seed,cfg,[{'file':native,'weight':.7,'clip_weight':.7}])
                role={'name':'酒神','region':[0,0,1,1]}
                regional_lora.apply_pose(g,regional_lora.standing_pose([role],{**cfg,'regional_pose_margin':.16}),cfg)
                g['280']['inputs'].pop('areas',None)
                key=f'{model}-{seed}';e.save(out/(key+'-workflow.json'),g)
                started=time.monotonic();job=comfyui.api('/prompt',{'prompt':g})
                e.need(not job.get('node_errors'),str(job.get('node_errors')));history=None
                while time.monotonic()-started<900:
                    history=comfyui.api('/history/'+job['prompt_id']).get(job['prompt_id'])
                    if history:break
                    time.sleep(.5)
                e.save(out/(key+'-history.json'),history)
                e.need(history and history.get('status',{}).get('status_str')=='success','酒神 GPU 加载或出图失败：'+key)
                descriptor=history['outputs']['7']['images'][0]
                opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(cfg['endpoint']+'/view?'+urllib.parse.urlencode(descriptor),timeout=30) as response:
                    (out/(key+'.png')).write_bytes(response.read())
                records.append({'key':key,'seconds':round(time.monotonic()-started,2),'acceptance':'pending_visual_review'})
                e.save(out/'renders.json',records)
                e.save(ROOT/'data/test-results/tragodia-gpu/latest.json',{'path':str(out),'acceptance':'pending_visual_review'})
                print('酒神 '+key+' 已通过 GPU 加载并生成',flush=True)
    finally:manager.stop()


if __name__=='__main__':main()
