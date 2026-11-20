"""Measure real long-input completion and retrieval, with an owned Strata process and RAM guard."""
import argparse,ctypes,json,pathlib,re,secrets,subprocess,sys,threading,time,urllib.request,urllib.error
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from comic_workflow.strata_model import StrataManager
OUT=ROOT/'data/strata-setup';BASE='http://127.0.0.1:8094';opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
SYSTEM='Use only the supplied archive. Return the three requested passcodes exactly, without inventing or shortening them.'
def request(path,body=None,timeout=30):
 req=urllib.request.Request(path,data=json.dumps(body).encode('utf-8') if body is not None else None,headers={'Content-Type':'application/json'})
 return opener.open(req,timeout=timeout)
def get(path,body=None,timeout=30):
 with request(path,body,timeout) as r:return json.load(r)
def save(path,data):
 temp=path.with_suffix('.tmp');temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(path)
class Memory(ctypes.Structure):
 _fields_=[('length',ctypes.c_ulong),('load',ctypes.c_ulong)]+[(x,ctypes.c_ulonglong) for x in ['total','available','page_total','page_available','virtual_total','virtual_available','extended']]
def memory():
 m=Memory();m.length=ctypes.sizeof(m);ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m));return m.total/2**30,m.available/2**30
class Monitor:
 def __init__(self,manager):self.manager=manager;self.done=threading.Event();self.samples=[];self.reason=None;self.stage='loading';self.thread=threading.Thread(target=self.loop,daemon=True)
 def start(self):self.thread.start()
 def loop(self):
  lows=0
  while not self.done.is_set():
   total,free=memory();row={'time':time.time(),'stage':self.stage,'ram_used_gib':total-free,'ram_available_gib':free}
   try:
    text=subprocess.check_output(['nvidia-smi','--id=0','--query-gpu=memory.used','--format=csv,noheader,nounits'],creationflags=subprocess.CREATE_NO_WINDOW,timeout=4,text=True);row['vram_used_mib']=int(text.strip())
   except Exception:pass
   self.samples.append(row);lows=lows+1 if free<5 else 0
   if free<2 or lows>=3:
    self.reason='Available system RAM remained below 5 GiB';self.manager.stop();return
   self.done.wait(2)
 def stop(self):self.done.set();self.thread.join(timeout=8)
 def metrics(self,stage):
  ss=[x for x in self.samples if x['stage']==stage]
  return {'peak_system_ram_used_gib':max((s['ram_used_gib'] for s in ss),default=None),'min_system_ram_available_gib':min((s['ram_available_gib'] for s in ss),default=None),'peak_vram_used_mib':max((s.get('vram_used_mib',0) for s in ss),default=None)}
def idle():
 state=get('http://127.0.0.1:8765/api/status',timeout=5)
 active=[j for j in state.get('jobs',[]) if j.get('status') in ['running','queued','paused','pausing','cancelling']]
 if active:raise RuntimeError('Studio has active work; postpone this test.')
 try:q=get('http://127.0.0.1:8188/queue',timeout=3)
 except urllib.error.URLError:q={}
 if q.get('queue_running') or q.get('queue_pending'):raise RuntimeError('Drawing queue has active work; postpone this test.')
def body(model,text):return {'model':model,'system':SYSTEM,'messages':[{'role':'user','content':text}],'thinking':{'type':'disabled'}}
def count(model,text):return int(get(BASE+'/v1/messages/count_tokens',body(model,text),timeout=180)['input_tokens'])
def archive(n,codes,nonce):
 lines=[f'Record {i:07d}: area {(i*17)%997:03d}; item {(i*13)%619:03d}; status recorded; the lamp and map were stored in the archive.\n' for i in range(n)]
 lines.insert(0,'EARLY_PASSCODE = '+codes['early']+'\n')
 lines.insert(len(lines)//2,'MIDDLE_PASSCODE = '+codes['middle']+'\n')
 lines.append('LATE_PASSCODE = '+codes['late']+'\n')
 return 'Independent archive '+nonce+'\n'+''.join(lines)+'\nReturn a JSON object with early, middle, late containing the corresponding exact passcodes, and summary containing one ordinary English sentence about this archive. No other text.'
def sized_prompt(model,capacity):
 codes={k:secrets.token_hex(8) for k in ['early','middle','late']};nonce=secrets.token_hex(8);target=capacity-1024
 lo=0;hi=max(100,target//10);best=None
 while lo<=hi:
  n=(lo+hi)//2;text=archive(n,codes,nonce);tokens=count(model,text)
  if tokens<=target:best=(text,tokens,codes);lo=n+1
  else:hi=n-1
 if best is None or best[1]<target-100:raise RuntimeError('Could not construct the intended measured input length.')
 return best

def completion(model,text,limit=256):
 payload={**body(model,text),'max_tokens':limit,'temperature':0,'stream':True}
 started=time.monotonic();first=None;chunks=[];usage={};stop=None;events=0
 with request(BASE+'/v1/messages',payload,timeout=1800) as response:
  for raw in response:
   if not raw.startswith(b'data:'):continue
   value=raw[5:].strip()
   if value==b'[DONE]':break
   obj=json.loads(value);events+=1;kind=obj.get('type')
   if kind=='error':raise RuntimeError(json.dumps(obj,ensure_ascii=False))
   if kind=='message_start':usage.update(obj.get('message',{}).get('usage',{}))
   if kind=='content_block_delta':
    delta=obj.get('delta',{});part=delta.get('text','')
    if part:
     if first is None:first=time.monotonic()-started
     chunks.append(part)
   if kind=='message_delta':usage.update(obj.get('usage',{}));stop=obj.get('delta',{}).get('stop_reason')
 total=time.monotonic()-started;output=''.join(chunks)
 return {'output':output,'usage':usage,'stop_reason':stop,'first_text_seconds':first,'total_seconds':total,'stream_events':events,'decode_tokens_per_second':usage.get('output_tokens',0)/max(.001,total-(first or total))}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--capacities',default='32768,131072,262144');ap.add_argument('--label',default='baseline');ap.add_argument('--profile');args=ap.parse_args()
 cfg=json.loads((OUT/'abliterated-iq4-prepare.json').read_text())['prompt_model_proposal']
 if args.profile:cfg['strata_profile']=args.profile
 manager=StrataManager(cfg);monitor=Monitor(manager)
 result={'label':args.label,'started':time.strftime('%Y-%m-%d %H:%M:%S'),'state':'starting','profile':cfg['strata_profile'],'model':cfg['model_name'],'tests':[]};dest=OUT/f'abliterated-iq4-context-{args.label}.json'
 save(dest,result);idle();monitor.start()
 try:
  manager.start(lambda message:print(message,flush=True));health=get(BASE+'/health');result['health']=health
  assert health['loaded'] and health['model']==cfg['model_name'] and health['max_context']==262144
  monitor.stage='short';sample=completion(cfg['model_name'],'Translate to concise English image tags: 一名成年旅人在雨夜的车站举着蓝色雨伞，背景是暖色灯光。',128)
  if not sample['output'].strip():raise RuntimeError('Short prompt produced no answer.')
  result['short_prompt']=sample;result['state']='testing';save(dest,result)
  for capacity in [int(v) for v in args.capacities.split(',')]:
   idle();stage=str(capacity);monitor.stage=stage
   text,tokens,codes=sized_prompt(cfg['model_name'],capacity)
   row={'capacity':capacity,'counted_input_tokens':tokens,'reserved_output_tokens':256,'expected_codes':codes,'state':'running','started':time.strftime('%Y-%m-%d %H:%M:%S')};result['tests'].append(row);save(dest,result);print('Testing measured input tokens',tokens,flush=True)
   response=completion(cfg['model_name'],text);row.update(response);row.update(monitor.metrics(stage))
   usage=response['usage'];actual=sum(usage.get(k,0) for k in ['input_tokens','cache_creation_input_tokens','cache_read_input_tokens']) if 'input_tokens' in usage else None
   row['actual_input_tokens']=actual;row['no_truncation']=actual is not None and abs(actual-tokens)<=16
   row['prefill_tokens_per_second_estimate']=tokens/response['first_text_seconds'] if response['first_text_seconds'] else None
   try:
    parsed=json.loads(re.search(r'\{.*\}',response['output'],re.S).group());row['retrieval_pass']=all(parsed.get(k)==v for k,v in codes.items());row['continuation_pass']=isinstance(parsed.get('summary'),str) and len(parsed['summary'])>10
   except Exception:row['retrieval_pass']=False;row['continuation_pass']=False
   row['state']='completed';row['memory_guard']=monitor.reason;save(dest,result)
   if monitor.reason:raise RuntimeError(monitor.reason)
   if actual is None or actual+response['usage'].get('output_tokens',0)>262144:raise RuntimeError('Missing token accounting or context budget violation.')
  result['state']='completed'
 except BaseException as e:
  result['state']='failed';result['error']=str(e);result['memory_guard']=monitor.reason
  if result['tests'] and result['tests'][-1]['state']=='running':result['tests'][-1].update(state='failed',error=str(e),**monitor.metrics(monitor.stage))
  raise
 finally:
  monitor.stop();manager.stop();result['finished']=time.strftime('%Y-%m-%d %H:%M:%S');result['resources_released']=True;result['load_memory']=monitor.metrics('loading');save(dest,result);save(OUT/f'abliterated-iq4-memory-{args.label}.json',monitor.samples)
if __name__=='__main__':main()
