"""GPU smoke test for the three newly connected models using disposable prompts."""
import json,time,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from comic_workflow import comfyui,prompt_models
out=Path('data/test-results/prompt-studio-refresh')
manager=comfyui.ComfyManager();report=[]
try:
 manager.start()
 info=comfyui.api('/object_info')
 assert 'DeltaMixQ35LoraLoader' in info
 for name in list(prompt_models.MODELS)[1:]:
  cfg={**prompt_models.defaults(name),'width':512,'height':512,'steps':2,'literal_prompt':True}
  workflow=comfyui.graph(name,'A quiet mountain lake, pine forest, morning sunlight, illustration','text, watermark',137,cfg)
  workflow['7']={'class_type':'PreviewImage','inputs':{'images':['6',0]}}
  missing={n['class_type'] for n in workflow.values()}-set(info);assert not missing,missing
  t=time.monotonic();res=comfyui.api('/prompt',{'prompt':workflow,'client_id':'studio-new-model-smoke'})
  jid=res['prompt_id'];print('submitted',name,flush=True)
  deadline=time.monotonic()+600
  while time.monotonic()<deadline:
   history=comfyui.api('/history/'+jid).get(jid)
   if history:break
   time.sleep(1)
  else:raise RuntimeError('timeout '+name)
  status=history.get('status',{}).get('status_str')
  images=[im for o in history.get('outputs',{}).values() for im in o.get('images',[])]
  report.append({'model':name,'status':status,'images':len(images),'seconds':round(time.monotonic()-t,1)})
  if status=='error':
   report[-1]['error']=history['status'];print(json.dumps(report[-1],ensure_ascii=False),flush=True)
  else:print('completed',name,report[-1]['seconds'],flush=True)
  comfyui.cleanup_prompt_output(jid,history)
  comfyui.api('/free',{'unload_models':True,'free_memory':True})
finally:
 out.mkdir(parents=True,exist_ok=True);(out/'gpu-smoke.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
 manager.stop()
assert len(report)==3 and all(x['status']=='success' and x['images'] for x in report),report
