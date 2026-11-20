"""Single-panel phone selfie, with the penguin supported by the Doctor's left hand."""
import copy
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from comic_workflow import engine as e, prompt_exchange
from comic_workflow.repository import Repository
from redraw_amusement_page_one import action,get,MODEL,PID

TARGET='P006-K01'
OUT=ROOT/'data/authored-stories/amusement-day-20261018-p006-k01'


def save(name,value):
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')


def main():
    receipt=OUT/'generation-started.json'
    if receipt.exists():
        print(receipt.read_text(encoding='utf-8'));return
    assert not [j for j in get('/api/status')['jobs'] if j['status'] in
                {'running','queued','paused','pausing','cancelling'}], 'Another job is active'
    repo=Repository();original=repo.get(PID)
    previous=prompt_exchange.current_import(repo,PID)['bundle']
    if not (OUT/'before.json').exists():save('before.json',{'page':original['pages']['P006'],'bundle':previous})
    old={r['panel_id']:r for r in previous['panels']}
    page=copy.deepcopy(original['pages']['P006'])
    panel=next(p for p in page['panels'] if p['id']==TARGET)
    moment='能天使在左侧举起一部手机，屏幕朝向两人，与右侧博士并肩自拍合影，亮灯的摩天轮在两人身后清楚入镜。博士用自己的左手掌心向上捧着一只手掌大小的小企鹅，左肘弯曲，玩偶在胸前；博士右手自然放松，胸袋里没有玩偶。'
    panel.update(purpose='长期项目：'+moment,shot='双人手机自拍中景',angle='略斜侧正面平视',
        composition='两人并肩，手机伸向前方，摩天轮在身后；'+moment,
        main_instant=moment,background='摩天轮前的开放步道，蓝调傍晚，完整亮灯的摩天轮轮廓与暖色灯串；无合影机和其他游客',
        drawing_description=moment+' 能天使白色外套配黑肩，博士整件兜帽长外套为炭灰色。博士宽松的深色面罩与脸留有空间，不显鼻嘴下巴线条。')
    for role in panel['characters']:
        if role['character_id']=='C001':
            role.update(position='画面左侧',gaze='看向手机屏幕',action='右手举一部手机向前伸出，屏幕朝向自己和博士，微笑自拍')
        else:
            role.update(position='画面右侧',gaze='朝向手机屏幕',action='左手掌心向上捧着唯一的小企鹅，左肘弯曲、玩偶位于胸前，右手放松')
    panel['props']=[{'prop_id':'O007','position':'C002左手掌心，胸前','state':'唯一的小企鹅由博士左手托住，胸袋为空'}]
    panel['state_after'].update({'O007.holder':'C002左手','O007.state':'左手掌心向上捧着小企鹅',
        'C001.location':'摩天轮前步道左侧','C002.location':'摩天轮前步道右侧'})
    assert all(a==b for a,b in zip(original['pages']['P006']['panels'],page['panels']) if a['id']!=TARGET)
    if page!=original['pages']['P006']:
        candidate=action({'action':'propose','project':PID,'stage':'page','target':'P006','payload':page,'base_hash':e.digest(original)})
        action({'action':'accept','project':PID,'candidate':candidate['id']})
    e.validate_project(repo.get(PID),complete=True)
    document=prompt_exchange.export_storyboard(repo,PID)
    for row in document['panels']:
        row.update(positive=old[row['panel_id']]['positive'],negative=old[row['panel_id']]['negative'])
        if row['panel_id']==TARGET:
            row['positive']=(
                'Medium slightly oblique frontal view of exactly two people standing shoulder to shoulder '
                'taking a smartphone selfie, an illuminated Ferris wheel clearly visible behind both of them at blue hour. '
                'LEFT: Exusiai smiles toward the phone screen, holding ONE smartphone in her extended right hand '
                'in front of the pair, screen facing them and back of phone visible to the viewer. '
                'Short reddish-brown hair, orange eyes, human ears, white halo above her head, pale geometric '
                'wing fragments, white jacket with black shoulders. '
                'RIGHT: the Doctor looks toward the same phone. The Doctor cradles ONE tiny black-and-white '
                'penguin plush with an orange beak on their OWN LEFT open palm at chest height, left elbow bent '
                'and left forearm supporting the plush from below. The Doctor right hand rests empty at their side. '
                'Doctor wears a matte charcoal-black long coat, matching dark hood and dark sleeves, black gloves, '
                'subtle teal trim, empty chest pocket. A roomy opaque dark face covering stands away from the face '
                'under the hood, broad plain angular surfaces hiding all facial anatomy without tracing nose, lips or chin. '
                'Clear separate arms, warm wheel lights against a blue evening sky, open walkway, anime illustration.')
            row['negative']=(
                'photo booth, photography kiosk, standalone camera, photographer, extra people, duplicate character, '
                'two phones, extra phone, duplicate penguin, penguin in chest pocket, penguin in right hand, '
                'toy held by Exusiai, floating plush, extra arms, merged hands, animal ears, firearms, '
                'white coat on right person, white hood on right person, skin-tight mask, nose-shaped mask, '
                'lips visible through mask, chin contour through mask, text, speech bubbles, watermark')
    changes=[r['panel_id'] for r in document['panels'] if
             (r['positive'],r['negative'])!=(old[r['panel_id']]['positive'],old[r['panel_id']]['negative'])]
    assert set(changes)<={TARGET}
    save('storyboard-prompts.json',document)
    preview=action({'action':'preview_prompt_import','project':PID,'document':document})
    assert preview['valid'] and not preview['missing'],preview
    assert not preview['warnings'],preview['warnings']
    bundle=action({'action':'save_prompt_import','project':PID,'document':document,'preview_hash':preview['preview_hash'],'confirm':True})
    request=dict(action='draw_imported',project=PID,bundle=bundle['id'],targets=[TARGET],candidate_count=8,checkpoint=MODEL,steps=35)
    save('prepared.json',{'request':request,'changed_visual_panel':TARGET,'old_images_preserved':True})
    try:result=action(request)
    except Exception:
        matches=[]
        for path in (ROOT/'data/jobs').glob('*.json'):
            job=json.loads(path.read_text(encoding='utf-8'));req=job.get('request',{})
            if job.get('project')==PID and req.get('bundle')==bundle['id'] and req.get('targets')==[TARGET]:matches.append(job)
        if len(matches)!=1:raise
        result={'job':matches[0]['id'],'recovered':True}
    assert result.get('job'),result
    save('generation-started.json',{'request':request,'submission':result,'follow_after_start':False})
    print(json.dumps(result,ensure_ascii=False))
    job=get('/api/job?id='+result['job'])
    print(json.dumps({k:job.get(k) for k in ('status','message','progress','error')},ensure_ascii=False))


if __name__=='__main__':main()
