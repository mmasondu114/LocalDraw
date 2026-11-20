import sys,json,uuid,copy,re,shutil,hashlib,time,urllib.request,urllib.parse
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from comic_workflow import engine as e
from comic_workflow.repository import Repository
from comic_workflow.character_binding import identity_hash
OUT=ROOT/'output/seven-outings-longform-20261105'
BASE='http://127.0.0.1:8765'
def get(path):
 with urllib.request.urlopen(BASE+path,timeout=120) as r:return json.load(r)
def main():
 spec=e.read(OUT/'storyboard.json');manifest=e.read(OUT/'generation-manifest.json')
 assert manifest['state']=='completed' and len(manifest['jobs'])==896
 boot=get('/api/bootstrap')
 def act(action,**kw):
  req=urllib.request.Request(BASE+'/api/action',json.dumps(dict(action=action,**kw),ensure_ascii=False).encode(),{'Content-Type':'application/json','X-Studio-Token':boot['token']})
  try:
   with urllib.request.urlopen(req,timeout=120) as r:return json.load(r)
  except urllib.error.HTTPError as exc:raise RuntimeError(exc.read().decode()) from exc
 chars=[];names={}
 def character(name,appearance,origin='existing_work'):
  cid=f'C{len(chars):03}';names[name]=cid
  chars.append(dict(id=cid,name=name,origin=origin,source_notes=['原创出游同人；来自本次已完成的长篇剧本'],verified_facts=[],adaptation_choices=['本篇独立出游；第一人称旅行者视角'],unknowns=['不推断官方时间线'],appearance=appearance,voice='遵循逐格原稿对白',sample_lines=[],motivation='共同享受一次休闲出游',taboos=['不混淆人物身份','不把旅行者画成第三人称全身']))
 character('旅行者（空）','镜头就是旅行者；只允许局部手、膝、鞋。黑色露指手套与金色腕饰，禁止露脸或外部全身镜头。')
 names['我']=names['旅行者（空）']
 for c in spec['chapters']:character(c['name'],c['identity'])
 names['女皇']=names['冰之女皇']
 for p in spec['storyboard']:
  for line in p['dialogue'].splitlines():
   label=line.partition('：')[0];name=label.split('（')[0]
   if name not in names:character(name,'画外声音，不要求人物入镜。','original')
 state={'viewpoint':'旅行者第一人称','traveler_clothing':'黑色露指手套、金色腕饰；不露脸'}
 pages=[];scenes=[]
 for pn in range(1,29):
  rows=[r for r in spec['storyboard'] if r['page']==pn];pageid=f'P{pn:03}';sid=f'S{pn:03}'
  scenes.append(dict(id=sid,name=rows[0]['character']+'篇 · '+rows[0]['page_title'],layout='旅行者主观镜头；每格空间和取景见详细分镜',landmarks=[r['visual'] for r in rows],lighting='按原稿画面及英文提示词保持本页时段',axis_rule='摄像机始终位于旅行者双眼；允许低头、仰视与手部特写'))
  page=dict(id=pageid,number=pn,reading_direction='LTR',layout='两行两列四格，从左至右、由上至下',page_turn_note=rows[-1]['narration'] or rows[-1]['visual'],state_in=copy.deepcopy(state),panels=[],lettering=[],state_out=copy.deepcopy(state),layout_rows=[dict(weight=1,panels=[dict(panel_id=r['id'],weight=1) for r in rows[n:n+2]]) for n in (0,2)])
  for r in rows:
   actor=names[r['character']];still=(pn%4==0 and r['panel']==4)
   cast=[] if still else [dict(character_id=actor,position='镜头前或所指定局部',gaze='按原稿与旅行者互动或观察道具',action=r['visual'],expression='按当前情节与英文提示词')]
   p=dict(id=r['id'],page_id=pageid,number=r['panel'],purpose=r['chapter_title']+'：'+r['page_title'],layout='四格中的第'+str(r['panel'])+'格',aspect_ratio='19:13',shot=r['visual'].split('。')[0],angle='第一人称 POV；'+r['visual'].split('。')[0],composition=r['visual'],scene_id=sid,characters=cast,main_instant=r['visual'],background=r['scene_prompt'],props=[],transition_in='新页场景承接' if r['panel']==1 else '延续前格的动作、视线或道具',transition_out='翻页或章节收束' if r['panel']==4 else '承接下一格',state_after=copy.deepcopy(state),drawing_description=r['scene_prompt'])
   page['panels'].append(p)
   lines=[]
   for text in r['dialogue'].splitlines():
    label,sep,line=text.partition('：');name=label.split('（')[0]
    annotation=label[len(name):]
    lines.append(('dialogue',names[name],annotation+line if sep else text))
   if r['narration']:lines.append(('narration',None,r['narration']))
   for order,(kind,speaker,text) in enumerate(lines,1):page['lettering'].append(dict(id=f'{r["id"]}-T{order:02}',panel_id=r['id'],kind=kind,speaker_id=speaker,text=text,bubble_position='后期排字，避开人物面部、POV前景手与主要道具',order=order))
  pages.append(page)
 brief=dict(title=spec['title'],genre='原神同人 · POV休闲出游长篇',direction='七位角色分别与旅行者出游一次，每篇四页十六格；七件纪念物串起七次经历。全程旅行者第一人称。',page_count=28,panels_per_page=4,panel_mode='fixed',reading_direction='LTR',page_mode='single_page',first_story_page_side='right',must_include=[c['name']+'：'+c['title'] for c in spec['chapters']],avoid=['自动选图','第三人称旅行者全身','将冰之女皇同人造型视为官方造型'],character_material='水之篇由芙宁娜出演；冰之女皇为原创同人造型。每格已有八张DeltaMix v6候选，供用户选择。')
 document=dict(format='local-draw-story-v1',brief=brief,canon=dict(characters=chars,scenes=scenes,props=[],world_rules=['七篇独立假想出游，不推断官方时间线','旅行者黑色露指手套与金色腕饰保持一致'],initial_state=state),pages=pages)
 e.save(OUT/'studio-import-story.json',document)
 receipt_path=OUT/'studio-import-receipt.json'
 receipt=e.read(receipt_path) if receipt_path.exists() else dict(import_key=uuid.uuid4().hex)
 e.save(receipt_path,receipt)
 preview=act('preview_story_import',document=document)
 e.save(OUT/'studio-import-preview.json',preview)
 result=act('save_story_import',document=document,preview_hash=preview['preview_hash'],import_key=receipt['import_key'],confirm=True)
 pid=result['project'];receipt.update(project=pid,status='importing_images');e.save(receipt_path,receipt)
 print('Created studio project '+pid,flush=True)
 repo=Repository();project=repo.get(pid);folder=repo.folder(pid)
 prefs=dict(mode='none',checkpoint=manifest['model'],items=[],hand_detail=False,consistency_lora=False,reference_character_id='',aspect_ratio='storyboard',resolution='default')
 act('save_drawing_settings',project=pid,settings=prefs)
 prompts=get('/api/prompt-exchange?id='+pid)
 for row in prompts['panels']:
  g=e.read(OUT/'workflows'/f'{row["panel_id"]}-C01.json')
  row.update(positive=g['6']['inputs']['text'],negative=g['13']['inputs']['text'])
 pp=act('preview_prompt_import',project=pid,document=prompts)
 assert pp['valid'] and not pp['missing'],pp
 bundle=act('save_prompt_import',project=pid,document=prompts,preview_hash=pp['preview_hash'],confirm=True)
 receipt['prompt_bundle']=bundle['id'];e.save(receipt_path,receipt)
 # Only a new dedicated project is touched. Actual generated images and recipes are
 # copied, hashed, and published atomically into the studio's native image store.
 index_path=folder/'images/index.json'
 old=e.read(index_path) if index_path.exists() else {'batches':[],'selections':{}}
 batches=[];hashes=[]
 for page in project['pages'].values():
  for panel in page['panels']:
   key=panel['id'];bid='import-seven-'+key
   dest=folder/'images'/bid;dest.mkdir(parents=True,exist_ok=True)
   jobs=[j for j in manifest['jobs'] if j['panel']==key]
   assert len(jobs)==8 and all(j['status']=='complete' for j in jobs)
   images=[]
   for j in jobs:
    n=j['candidate'];source=OUT/j['image'];target=dest/f'{n}.png'
    source_hash=hashlib.sha256(source.read_bytes()).hexdigest()
    if not target.exists():shutil.copy2(source,target)
    assert hashlib.sha256(target.read_bytes()).hexdigest()==source_hash
    g=e.read(OUT/j['workflow']);e.save(dest/f'workflow-{n}.json',g)
    images.append(dict(id=f'{bid}-{n}',file=f'{bid}/{n}.png',seed=j['seed'],prompt_id=j['prompt_id'],view_index=n,positive=g['6']['inputs']['text'],negative=g['13']['inputs']['text'],review={'verdict':'unreviewed'},source_file=str(source),source_sha256=source_hash))
    hashes.append(dict(panel=key,candidate=n,sha256=source_hash,source=str(source),destination=str(target)))
   batches.append(dict(id=bid,panel_id=key,panel_hash=e.digest(panel),prompt_hash=e.digest(dict(positive=images[0]['positive'],negative=images[0]['negative'])),identity_hash=identity_hash(project,panel),checkpoint=manifest['model'],created=manifest['created'],status='done',images=images,source='导入本地 DeltaMix v6 已完成的原始候选；非本次重新生成',settings=dict(width=1216,height=832,steps=35,cfg=4.5,sampler='dpmpp_2m',scheduler='sgm_uniform'),count=8,candidate_indices=list(range(8)),mode='seeds',loras=[],character_lora_passes=[],review_required=False,references=[],consistency=None,render_options={'import_run':'seven-outings-20261105','external_prompt':True,'source_manifest':str(OUT/'generation-manifest.json')},art_preset=None,art_preset_hash=None,guide=None))
  print('Imported '+page['id'],flush=True)
 expected={b['id'] for b in batches}
 assert all(b['id'] in expected for b in old['batches']),'Unexpected unrelated images in destination'
 e.save(index_path,dict(selections=old['selections'],batches=batches))
 act('save_workspace',project=pid,state=dict(stage='drawing',target='',views={'drawing:':{'fields':{'literal-candidate-count-v2':'8'}}},updated=int(time.time()*1000)))
 api_index=get('/api/images?id='+pid)['images']
 assert len(api_index['batches'])==112 and sum(len(b['images']) for b in api_index['batches'])==896
 assert api_index['selections']==old['selections']
 # Fetch boundary images from every page through the exact frontend route.
 for b in batches[::4]+batches[-1:]:
  for n in (1,8):
   url=BASE+'/api/image?'+urllib.parse.urlencode(dict(id=pid,bundle=b['id'],file=f'{n}.png'))
   with urllib.request.urlopen(url,timeout=30) as response:data=response.read()
   assert hashlib.sha256(data).hexdigest()==b['images'][n-1]['source_sha256']
 e.save(OUT/'studio-import-file-verification.json',hashes)
 receipt.update(status='complete',pages=28,panels=112,images=896,selections=len(old['selections']),verified_files=len(hashes),api_images_verified=58,folder=str(folder),studio_url=BASE)
 e.save(receipt_path,receipt);print(json.dumps(receipt,ensure_ascii=False),flush=True)
if __name__=='__main__':main()
