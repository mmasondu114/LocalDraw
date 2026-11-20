"""Finite preparation for the user's Liyue English/560-image production request."""
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from comic_workflow import engine as e, drawing_settings
from comic_workflow.project_translation import translate
from comic_workflow.repository import Repository
from comic_workflow.anima_consistency import LORA

PID = 'story-afc684ad25a8'
BASE = 'http://127.0.0.1:8765'
REPO = Repository()
FOLDER = REPO.folder(PID) / 'production-english'
STATUS = FOLDER / 'status.json'
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
state = {'phase':'preparing','process_id':os.getpid(),'started':e.stamp()}


def get(path):
    with OPENER.open(BASE + path, timeout=20) as response:
        return json.load(response)


def act(action, **fields):
    token = get('/api/bootstrap')['token']
    req = urllib.request.Request(BASE+'/api/action',
        data=json.dumps({'action':action,**fields}).encode(),
        headers={'Content-Type':'application/json','X-Studio-Token':token,'Origin':BASE})
    with OPENER.open(req, timeout=60) as response:
        return json.load(response)


def save(**fields):
    state.update(fields, updated=e.stamp())
    e.save(STATUS,state)


def idle():
    active=[j for j in get('/api/bootstrap')['jobs'] if j['status'] in ('running','queued')]
    e.need(not active, 'Another studio job started; preserve preparation and avoid concurrent generation')


def start(update):
    idle()
    jid=act('start_model')['job']
    while True:
        job=get('/api/job?id='+jid)
        if job['status'] not in ('running','queued'):
            e.need(job['status']=='done',job.get('message','Model start failed'))
            return
        time.sleep(2)


def stop():
    idle()
    act('stop_model')


last_saved=0
def update(event):
    global last_saved
    if time.monotonic()-last_saved<2:
        return
    last_saved=time.monotonic()
    event=event if isinstance(event,dict) else {'message':event}
    save(phase='translation',progress=event.get('progress',state.get('progress')),
         message=event.get('message',''))


def main():
    idle()
    if STATUS.exists():
        previous=e.read(STATUS)
        e.need(previous.get('phase') in ('failed','preparing','translation'),
               'Preparation already handed off; inspect status instead of restarting')
    save()
    p=REPO.get(PID)
    app=SimpleNamespace(repo=REPO,prepare_language=start,model=SimpleNamespace(stop=stop))
    result=translate(app,PID,{'base_hash':e.digest(p)},update,'production-'+e.stamp())
    save(phase='translation_complete',translation=result)
    # The existing server supports explicit global LoRAs. This enables Edit v2
    # without restarting the studio or depending on newly changed server code.
    settings=drawing_settings.load(REPO,PID)
    settings.update(mode='characters',consistency_lora=True,
        checkpoint='animaDeltaMix38BQwen0_deltaMixV60.safetensors')
    item=next((i for i in settings['items'] if i['file']==LORA),None)
    if item is None:
        item={'file':LORA,'name':'Anima Edit v2','trigger_words':''}
        settings['items'].append(item)
    item.update(selected=True,confirmed=True,weight=0.8,character_id='',character='',
        characters=[],scope='story',incompatible=False)
    drawing_settings.save(REPO,PID,settings)
    p=REPO.get(PID)
    routing={panel['id']:drawing_settings.loras_for_panel(settings,p,panel)
        for page in p['pages'].values() for panel in page['panels']}
    e.need(all(any(x['file']==LORA for x in rows) for rows in routing.values()),
           'Consistency LoRA must be loaded for every panel')
    e.save(FOLDER/'lora-routing.json',routing)
    e.save(FOLDER/'english-project.json',p)
    from comic_workflow.prompt_exchange import export_storyboard
    from comic_workflow.local_prompts import panel_context
    document=export_storyboard(REPO,PID)
    sizes=[len(e.encoded(panel_context(document,i))) for i in range(len(document['panels']))]
    e.need(max(sizes)<=24000, 'A prompt context is too large; preserve translation and reduce duplicated card metadata')
    idle()
    job=act('fill_local_prompts',project=PID)
    save(phase='prompt_generation',job=job['job'],panels=len(routing),candidate_count=8,
         total_images=len(routing)*8,max_prompt_context=max(sizes))
    print(json.dumps(state,ensure_ascii=True),flush=True)


if __name__=='__main__':
    try:
        main()
    except Exception as error:
        save(phase='failed',error=str(error))
        raise
