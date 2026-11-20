"""Remove the peg in P003-K03 and redraw that panel only."""
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from comic_workflow import engine as e, prompt_exchange
from comic_workflow.repository import Repository
from redraw_amusement_page_one import action, get, MODEL, PID

OUT = ROOT / 'data/authored-stories/amusement-day-20261018-p003-k03'
TARGET = 'P003-K03'


def save(name, value):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT/name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def main():
    receipt = OUT/'generation-started.json'
    if receipt.exists():
        print(receipt.read_text(encoding='utf-8'))
        return
    assert not [j for j in get('/api/status')['jobs'] if j['status'] in
                {'running','queued','paused','pausing','cancelling'}], 'Another job is active'
    repo = Repository()
    original = repo.get(PID)
    previous = prompt_exchange.current_import(repo, PID)['bundle']
    old_rows = {r['panel_id']:r for r in previous['panels']}
    if not (OUT/'before.json').exists():
        save('before.json', {'pages':original['pages'],'bundle':previous})
    pages = copy.deepcopy(original['pages'])
    panel = next(p for p in pages['P003']['panels'] if p['id'] == TARGET)
    moment = '黄色圆环平放在套圈摊前排平整台面上，环住手掌大小的黑白小企鹅玩偶底部；小企鹅有橙色小嘴。台面上没有木桩、小柱子或支架，后排大玩偶仅为虚化背景，没有人物或手入镜。'
    panel.update(purpose='最小的奖品：'+moment, main_instant=moment,
        composition='圆环与小企鹅特写；'+moment,
        drawing_description=moment+' 光线：午前明亮自然光，棚布遮阴。')
    for prop in panel['props']:
        if prop['prop_id']=='O006':
            prop.update(position='套圈摊前排台面，小企鹅底部周围',state='圆环平放，圈住小企鹅玩偶')
        elif prop['prop_id']=='O007':
            prop.update(position='前排台面黄色圆环内',state='坐在台面上的小企鹅玩偶，刚被圈中')
    # Keep confirmed page boundary states: this request changes one visible shot.
    for page in pages.values():
        if page != original['pages'][page['id']]:
            current=repo.get(PID)
            candidate=action({'action':'propose','project':PID,'stage':'page','target':page['id'],
                              'payload':page,'base_hash':e.digest(current)})
            action({'action':'accept','project':PID,'candidate':candidate['id']})
    current=repo.get(PID)
    e.validate_project(current,complete=True)
    document=prompt_exchange.export_storyboard(repo,PID)
    for row in document['panels']:
        old=old_rows[row['panel_id']]
        row.update(positive=old['positive'],negative=old['negative'])
        if row['panel_id']==TARGET:
            row['positive']=(
                'Low oblique close-up of a palm-sized black-and-white penguin plush with a small orange beak, '
                'sitting directly on a flat carnival prize platform. One yellow plastic hoop lies flat on '
                'the platform around the base of the little penguin. The hoop encloses the plush itself. '
                'Clear unobstructed tabletop around them. A much larger plush is softly blurred far behind. '
                'Object-only anime illustration, warm late-morning light under a colorful awning.')
            row['negative']=('wooden peg, pole, post, pillar, cylinder, stake, dowel, support rod, pedestal, '
                'people, hands, faces, several hoops, text, speech bubbles, watermark')
    changes=[r['panel_id'] for r in document['panels'] if
             (r['positive'],r['negative'])!=(old_rows[r['panel_id']]['positive'],old_rows[r['panel_id']]['negative'])]
    assert set(changes)<={TARGET}
    save('storyboard-prompts.json',document)
    preview=action({'action':'preview_prompt_import','project':PID,'document':document})
    assert preview['valid'] and not preview['missing'],preview
    assert not preview['warnings'],preview['warnings']
    bundle=action({'action':'save_prompt_import','project':PID,'document':document,
                   'preview_hash':preview['preview_hash'],'confirm':True})
    request=dict(action='draw_imported',project=PID,bundle=bundle['id'],targets=[TARGET],
                 candidate_count=16,checkpoint=MODEL,steps=35)
    save('prepared.json',{'request':request,'changed_visual_panel':TARGET,'old_images_preserved':True})
    try:
        result=action(request)
    except Exception:
        matches=[]
        for path in (ROOT/'data/jobs').glob('*.json'):
            job=json.loads(path.read_text(encoding='utf-8'));req=job.get('request',{})
            if job.get('project')==PID and req.get('bundle')==bundle['id'] and req.get('targets')==[TARGET]:
                matches.append(job)
        if len(matches)!=1:raise
        result={'job':matches[0]['id'],'recovered':True}
    assert result.get('job'),result
    save('generation-started.json',{'request':request,'submission':result,'follow_after_start':False})
    print(json.dumps(result,ensure_ascii=False))
    job=get('/api/job?id='+result['job'])
    print(json.dumps({k:job.get(k) for k in ('status','message','progress','error')},ensure_ascii=False))


if __name__=='__main__':main()
