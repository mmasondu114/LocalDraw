"""Remove the visible throwing ring from one panel, preserving every other panel."""
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from comic_workflow import engine as e, prompt_exchange
from comic_workflow.repository import Repository
from redraw_amusement_page_one import action, get, MODEL, PID

OUT = ROOT / 'data/authored-stories/amusement-day-20261018-p003-k02'
TARGET = 'P003-K02'


def save(name, value):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def main():
    receipt = OUT / 'generation-started.json'
    if receipt.exists():
        print(receipt.read_text(encoding='utf-8'))
        return
    assert not [j for j in get('/api/status')['jobs']
                if j['status'] in {'running','queued','paused','pausing','cancelling'}], 'Another job is active'
    repo = Repository()
    original = repo.get(PID)
    old_bundle = prompt_exchange.current_import(repo, PID)['bundle']
    old_rows = {r['panel_id']:r for r in old_bundle['panels']}
    page = copy.deepcopy(original['pages']['P003'])
    panel = next(p for p in page['panels'] if p['id'] == TARGET)
    moment = '能天使刚完成投掷，空着的右手顺势向画外右侧伸出，手指自然放松；左手垂下，表情专注而轻松。圆环已飞到画外，本格不画手持或空中的圆环。'
    panel.update(purpose='最小的奖品：'+moment, shot='能天使投掷后中景',
        composition='能天使侧面中景；'+moment, main_instant=moment, props=[],
        drawing_description=moment+' 光线：午前明亮自然光，棚布遮阴。保持人耳与头顶白色光环，光环不属于投掷道具。')
    panel['characters'][0]['action'] = '完成投掷后的空手跟随动作，右手自然前送，左手垂下'
    panel['state_after']['O006.holder'] = '画外右侧空中'
    panel['state_after']['O006.state'] = '已离开手部并飞出取景范围，向目标落下'
    assert all(a == b for a,b in zip(original['pages']['P003']['panels'],page['panels']) if a['id'] != TARGET)
    if page != original['pages']['P003']:
        if not (OUT/'before.json').exists():
            save('before.json', {'page':original['pages']['P003'], 'bundle':old_bundle})
        candidate = action({'action':'propose','project':PID,'stage':'page','target':'P003',
                            'payload':page,'base_hash':e.digest(original)})
        action({'action':'accept','project':PID,'candidate':candidate['id']})
    current = repo.get(PID)
    e.validate_project(current, complete=True)
    assert all(current['pages'][k] == v for k,v in original['pages'].items() if k != 'P003')
    document = prompt_exchange.export_storyboard(repo, PID)
    for row in document['panels']:
        old = old_rows[row['panel_id']]
        row.update(positive=old['positive'],negative=old['negative'])
        if row['panel_id'] == TARGET:
            row['positive'] = (
                'Solo medium side view of Exusiai (Arknights) immediately after a gentle forward toss. '
                'Her empty right hand extends toward the right edge in a relaxed follow-through, '
                'fingers naturally open; left arm hangs at her side, both hands empty. '
                'Short fluffy reddish-brown hair, orange eyes, normal human ears, '
                'one white halo floating ABOVE HER HEAD and pale angular wing fragments behind her shoulders. '
                'White jacket with black shoulders, black skirt over opaque black tights, fingerless gloves. '
                'Focused cheerful expression, colorful carnival game stall softly blurred behind, '
                'warm late-morning daylight, anime illustration. Exactly one person.')
            # Do not ban rings indiscriminately: Exusiai's halo must remain.
            row['negative'] = (
                'held object, object between fingers, yellow hoop, yellow ring in hand, '
                'flying hoop, floating object near hand, finger jewelry, throwing weapon, '
                'extra people, duplicate character, extra fingers, animal ears, firearms, '
                'masked face, text, speech bubbles, watermark')
    changes = [r['panel_id'] for r in document['panels']
               if (r['positive'],r['negative']) != (old_rows[r['panel_id']]['positive'],old_rows[r['panel_id']]['negative'])]
    assert set(changes) <= {TARGET}
    save('storyboard-prompts.json', document)
    preview = action({'action':'preview_prompt_import','project':PID,'document':document})
    assert preview['valid'] and not preview['missing'], preview
    assert not preview['warnings'], preview['warnings']
    bundle = action({'action':'save_prompt_import','project':PID,'document':document,
                     'preview_hash':preview['preview_hash'],'confirm':True})
    request = dict(action='draw_imported',project=PID,bundle=bundle['id'],targets=[TARGET],
                   candidate_count=16,checkpoint=MODEL,steps=35)
    save('prepared.json', {'request':request,'changed_panel':TARGET,'old_candidates_preserved':True})
    try:
        result = action(request)
    except Exception:
        matches=[]
        for file in (ROOT/'data/jobs').glob('*.json'):
            job=json.loads(file.read_text(encoding='utf-8'));req=job.get('request',{})
            if job.get('project') == PID and req.get('bundle') == bundle['id'] and req.get('targets') == [TARGET]:
                matches.append(job)
        if len(matches)!=1:
            raise
        result={'job':matches[0]['id'],'recovered':True}
    assert result.get('job'), result
    save('generation-started.json',{'request':request,'submission':result,'follow_after_start':False})
    print(json.dumps(result,ensure_ascii=False))
    job=get('/api/job?id='+result['job'])
    print(json.dumps({k:job.get(k) for k in ('status','message','progress','error')},ensure_ascii=False))


if __name__ == '__main__':
    main()
