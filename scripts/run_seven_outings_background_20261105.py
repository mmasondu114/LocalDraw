"""Detached local generation with ten-minute checkpoints; user-only selection."""
import copy, json, os, time, subprocess, traceback, urllib.request
from pathlib import Path
from datetime import datetime, timezone, timedelta
from html import escape
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'output/seven-outings-longform-20261105'
BASE='http://127.0.0.1:8190'
INTERVAL=600
STYLE=('masterpiece, best quality, detailed 2D anime comic illustration, clean fine linework, refined cel shading, '
 'expressive recognizable character design, elegant color harmony, layered detailed environment, natural anatomy, '
 'one coherent moment in one scene. STRICT FIRST PERSON POV from the eyes of Traveler Aether. The camera IS the traveler. '
 'Any foreground hands belonging to the viewer wear BLACK FINGERLESS GLOVES WITH GOLD WRIST CUFFS, consistently. '
 'Never show the traveler face, head, back or whole body. Subjective eye-level camera unless scene specifies otherwise. '
 'Single comic panel, no borders or lettering, reserve breathing room around faces. ')
NEG=('worst quality, low quality, blurry, bad anatomy, extra arms, extra fingers, fused fingers, malformed hands, '
 'duplicate main character, twins, cloned face, multiple views, split screen, collage, panel borders, '
 'text, letters, speech bubble, watermark, signature, logo, photorealistic, 3d render, nude, swimsuit, '
 'visible protagonist face, visible protagonist head, external view of traveler, third person camera, over-the-shoulder view')

def now():return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds')
def save(path,obj):
 tmp=path.with_suffix(path.suffix+'.tmp')
 tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(path)
def api(path,body=None):
 req=urllib.request.Request(BASE+path,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(req,timeout=60) as r:return json.load(r)
def event(kind,**kw):
 v=dict(time=now(),event=kind,**kw)
 with (OUT/'monitor-events.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(v,ensure_ascii=False)+'\n')
 print(json.dumps(v,ensure_ascii=False),flush=True)

def prepare():
 spec=json.loads((OUT/'storyboard.json').read_text(encoding='utf-8'))
 (OUT/'workflows').mkdir(exist_ok=True);(OUT/'thumbnails').mkdir(exist_ok=True)
 if (OUT/'generation-manifest.json').exists():
  raise RuntimeError('Existing generation manifest: inspect prior run before resuming; no duplicate submissions made.')
 base=json.loads((ROOT/'workflows/delta-mix-v6-api.json').read_text(encoding='utf-8'))
 jobs=[]
 for p in spec['storyboard']:
  for c in range(1,9):
   ident=f'{p["id"]}-C{c:02}'
   g=copy.deepcopy(base)
   identity=p['identity']
   if p['page']%4==0 and p['panel']==4:
    identity='Still life from my own eyes. No full character and no face visible; only the described hands and objects. '
   g['6']['inputs']['text']=STYLE+'\n'+identity+'\n'+p['scene_prompt']
   g['13']['inputs']['text']=NEG
   if p['chapter']==7:g['13']['inputs']['text']+=', 2girls, two women, second woman, second face, twins, multiple queens'
   g['5']['inputs'].update(width=1216,height=832,batch_size=1)
   seed=261105000000+p['page']*1000+p['panel']*100+c
   g['3']['inputs'].update(seed=seed,steps=35,cfg=4.5,sampler_name='dpmpp_2m',scheduler='sgm_uniform')
   g['29']['inputs']['filename_prefix']=f'candidates/{p["id"]}/C{c:02}'
   save(OUT/'workflows'/f'{ident}.json',g)
   jobs.append(dict(id=ident,panel=p['id'],candidate=c,seed=seed,status='planned',workflow=f'workflows/{ident}.json'))
 m=dict(title=spec['title'],created=now(),updated=now(),state='prepared',worker_pid=os.getpid(),check_interval_seconds=INTERVAL,
        model='animaDeltaMix38BQwen0_deltaMixV60.safetensors',width=1216,height=832,steps=35,cfg=4.5,
        sampler='dpmpp_2m',scheduler='sgm_uniform',loras=[],total=896,selected={},selection_policy='USER_ONLY',jobs=jobs)
 save(OUT/'generation-manifest.json',m)
 return spec,m

def render(spec,m):
 count={s:sum(j['status']==s for j in m['jobs']) for s in ['complete','failed','queued','planned','submitting','submission_error','missing']}
 s=dict(title=m['title'],state=m['state'],updated=now(),total=896,counts=count,check_interval_seconds=600,
        worker_pid=os.getpid(),comfy_pid=m.get('comfy_pid'),last_error=m.get('last_error'),selection_policy='USER_ONLY')
 save(OUT/'status.json',s)
 cards=[]
 for p in spec['storyboard']:
  candidates=[]
  for j in (j for j in m['jobs'] if j['panel']==p['id']):
   content=(f'<a href="{escape(j["image"])}" target="_blank"><img loading="lazy" src="{escape(j["thumbnail"])}" alt="{j["id"]}"></a>'
            if j.get('image') else f'<div class="pending">{escape(j["status"])}</div>')
   disabled='' if j.get('image') else ' disabled'
   candidates.append(f'<div class="candidate" data-id="{j["id"]}">{content}<button data-panel="{p["id"]}" data-choice="{j["id"]}"{disabled}>选择候选 {j["candidate"]}</button></div>')
  cards.append(f'<section id="{p["id"]}"><h2>{p["id"]} · {escape(p["character"])} · {escape(p["page_title"])}</h2><p>{escape(p["visual"])}</p><p class="dialogue">{escape(p["dialogue"]).replace(chr(10),"<br>")}</p><p>{escape(p["narration"])}</p><div class="grid">'+''.join(candidates)+'</div></section>')
 nav=''.join(f'<a href="#P{i*4+1:03}-K01">{escape(c["name"])}</a>' for i,c in enumerate(spec['chapters']))
 script=r'''<script>
const key='seven-outings-20261105-user-choices';let chosen={};try{chosen=JSON.parse(localStorage.getItem(key)||'{}')}catch(e){}
function paint(){document.querySelectorAll('.candidate').forEach(e=>e.classList.toggle('selected',Object.values(chosen).includes(e.dataset.id)));document.getElementById('selection').textContent='你已选择 '+Object.keys(chosen).length+' / 112 格';}
document.querySelectorAll('button[data-choice]').forEach(b=>b.onclick=()=>{if(chosen[b.dataset.panel]===b.dataset.choice)delete chosen[b.dataset.panel];else chosen[b.dataset.panel]=b.dataset.choice;try{localStorage.setItem(key,JSON.stringify(chosen))}catch(e){alert('浏览器未能保存选择，请立即导出选图清单。')}paint();});
document.getElementById('export').onclick=()=>{const blob=new Blob([JSON.stringify({project:'seven-outings-longform-20261105',selected_by:'user',exported_at:new Date().toISOString(),choices:chosen},null,2)],{type:'application/json'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='我的选图清单.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),2000);};paint();
</script>'''
 css='''<style>body{margin:0;background:#171e27;color:#eef1f5;font:16px/1.7 system-ui,"Microsoft YaHei",sans-serif}header,section{max-width:1440px;margin:24px auto;padding:24px}header{background:#25303d;border-radius:12px}section{border-top:1px solid #455263}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:16px}.candidate{background:#283441;padding:6px;border:3px solid transparent;border-radius:9px}.selected{border-color:#ffc76d;background:#5d492f}img{display:block;width:100%;aspect-ratio:1216/832;object-fit:contain}.pending{aspect-ratio:1216/832;display:grid;place-items:center;color:#8392a3}button{background:#d9aa64;color:#161a1f;border:0;padding:10px 16px;cursor:pointer;border-radius:6px;margin:8px 0}button:disabled{opacity:.3;cursor:default}.candidate button{width:100%}a{color:#ffd69a}nav{display:flex;gap:20px;flex-wrap:wrap}.dialogue{border-left:3px solid #d9aa64;padding-left:15px}h2{font-size:22px}@media(max-width:850px){.grid{grid-template-columns:repeat(2,1fr)}header,section{padding:15px}}@media(max-width:420px){.grid{grid-template-columns:1fr}}</style>'''
 h=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>七日留白 · 896 张候选选图册</title>{css}<header><h1>七日留白 · 选图册</h1><p>28 页 / 112 格 / 每格 8 张候选。这里没有自动选图。</p><p>生成状态：{escape(m['state'])} · 已完成 {count['complete']} / 896 · 失败 {count['failed']+count['submission_error']+count['missing']}<br>最近检查：{s['updated']}（中国标准时间）· 后台每 10 分钟检查一次，刷新本页可查看最新记录。</p><p>点击图片打开原图；每格选择一张，可以随时改选。选择保存在当前浏览器；请导出选图清单备份，并把清单交给我继续排版。</p><p id="selection"></p><button id="export">导出我的选图清单</button> <a href="完整剧本与分镜.html">阅读完整剧本</a><nav>{nav}</nav></header>'''+''.join(cards)+script+'</html>'
 temp=OUT/'候选选图册.tmp.html';temp.write_text(h,encoding='utf-8');temp.replace(OUT/'候选选图册.html')
 return count

def checkpoint(spec,m,proc):
 if proc.poll() is not None:raise RuntimeError(f'Owned ComfyUI stopped unexpectedly, code {proc.returncode}')
 q=api('/queue');history=api('/history?max_items=10000')
 active={x[1] for x in q.get('queue_running',[])+q.get('queue_pending',[])}
 for j in m['jobs']:
  if j['status']!='queued':continue
  h=history.get(j['prompt_id'])
  if not h:
   if j['prompt_id'] not in active:
    # Queue and history are separate snapshots; re-read this ID before calling it missing.
    h=api('/history/'+j['prompt_id']).get(j['prompt_id'])
    if not h:j['status']='missing';j['error']='Prompt absent from queue and history'
   if not h:continue
  if h['status']['status_str']!='success':
   j['status']='failed';j['error']=h['status'];continue
  try:
   entry=h['outputs']['29']['images'][0]
   path=(OUT/entry['subfolder']/entry['filename']).resolve()
   if not path.is_relative_to(OUT.resolve()):raise RuntimeError('Unexpected output location')
   with Image.open(path) as im:
    im.load()
    if im.size!=(1216,832):raise RuntimeError(f'Unexpected size {im.size}')
    thumb=OUT/'thumbnails'/f'{j["id"]}.jpg'
    im.convert('RGB').resize((456,312)).save(thumb,quality=87)
   j.update(status='complete',image=path.relative_to(OUT).as_posix(),thumbnail=thumb.relative_to(OUT).as_posix(),completed_checked=now())
  except Exception as exc:j.update(status='failed',error=str(exc))
 unfinished=any(j['status']=='queued' for j in m['jobs'])
 if not unfinished:m['state']='completed' if all(j['status']=='complete' for j in m['jobs']) else 'finished_with_errors'
 m['updated']=now();save(OUT/'generation-manifest.json',m)
 counts=render(spec,m)
 event('checkpoint',state=m['state'],**counts,queue_running=len(q.get('queue_running',[])),queue_pending=len(q.get('queue_pending',[])))
 return unfinished

def main():
 OUT.mkdir(parents=True,exist_ok=True)
 lock=OUT/'generation.lock'
 fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY);os.write(fd,str(os.getpid()).encode());os.close(fd)
 proc=None;log=None;spec=None;m=None
 try:
  try:api('/system_stats')
  except OSError:pass
  else:raise RuntimeError('Port 8190 already in use; no existing service was modified.')
  spec,m=prepare();render(spec,m)
  free=int(subprocess.check_output(['nvidia-smi','--query-gpu=memory.free','--format=csv,noheader,nounits'],text=True).strip().splitlines()[0])
  if free<16000:raise RuntimeError(f'GPU memory not available: {free} MiB free')
  log=(OUT/'comfy-generation.log').open('a',encoding='utf-8')
  cmd=[str(ROOT/'runtime/kreamoss-venv/Scripts/python.exe'),str(ROOT/'runtime/ComfyUI-KreaMoss/main.py'),'--listen','127.0.0.1','--port','8190','--reserve-vram','3','--disable-all-custom-nodes','--whitelist-custom-nodes','ComfyUI-Anima-Delta-Q35-LoRA','local_draw_delta_characters','--disable-api-nodes','--disable-auto-launch','--extra-model-paths-config',str(ROOT/'config/delta-mix-model-paths.yaml'),'--output-directory',str(OUT)]
  proc=subprocess.Popen(cmd,cwd=ROOT/'runtime/ComfyUI-KreaMoss',stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
  m['comfy_pid']=proc.pid;m['state']='starting';save(OUT/'generation-manifest.json',m)
  for _ in range(120):
   if proc.poll() is not None:raise RuntimeError('Owned ComfyUI failed during startup')
   try:api('/system_stats');break
   except OSError:time.sleep(1)
  else:raise TimeoutError('ComfyUI startup timed out')
  q=api('/queue')
  if q['queue_running'] or q['queue_pending']:raise RuntimeError('Unexpected preexisting queue')
  m['state']='submitting';event('service_started',worker_pid=os.getpid(),comfy_pid=proc.pid,total=896)
  for index,j in enumerate(m['jobs']):
   j['status']='submitting';save(OUT/'generation-manifest.json',m)
   g=json.loads((OUT/j['workflow']).read_text(encoding='utf-8'))
   try:
    sub=api('/prompt',{'prompt':g,'client_id':'seven-outings-longform-20261105'})
    j.update(status='queued',prompt_id=sub['prompt_id'],submitted=now())
   except Exception as exc:
    j.update(status='submission_error',error=str(exc));event('submission_error',id=j['id'],error=str(exc))
   save(OUT/'generation-manifest.json',m)
   if (index+1)%64==0:render(spec,m);event('submitted_progress',count=index+1,total=896)
  m['state']='running';save(OUT/'generation-manifest.json',m)
  more=checkpoint(spec,m,proc)
  event('background_ready',next_check_seconds=600,selection_policy='USER_ONLY')
  while more:
   # Local worker owns monitoring, so the chat can end while generation proceeds.
   time.sleep(INTERVAL)
   more=checkpoint(spec,m,proc)
  event('generation_finished',state=m['state'],complete=sum(j['status']=='complete' for j in m['jobs']),total=896)
 except Exception as exc:
  event('fatal_error',error=str(exc),traceback=traceback.format_exc())
  if m:
   m.update(state='stopped_with_error',last_error=str(exc),updated=now());save(OUT/'generation-manifest.json',m)
   if spec:render(spec,m)
  raise
 finally:
  if proc and proc.poll() is None:
   proc.terminate()
   try:proc.wait(timeout=30)
   except subprocess.TimeoutExpired:proc.kill();proc.wait()
  if log:log.close()
  lock.unlink(missing_ok=True)
if __name__=='__main__':main()
