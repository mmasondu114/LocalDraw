"""Simplify K02 to a single facial expression before the phone selfie."""
import copy
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from comic_workflow import engine as e,prompt_exchange
from comic_workflow.repository import Repository
from redraw_amusement_page_one import action,get,MODEL,PID

TARGET='P006-K02'
OUT=ROOT/'data/authored-stories/amusement-day-20261018-p006-k02-simple'


def save(name,value):
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')


def main():
    receipt=OUT/'generation-started.json'
    if receipt.exists():print(receipt.read_text(encoding='utf-8'));return
    assert not [j for j in get('/api/status')['jobs'] if j['status'] in
                {'running','queued','paused','pausing','cancelling'}], 'Another job is active'
    repo=Repository();original=repo.get(PID)
    previous=prompt_exchange.current_import(repo,PID)['bundle']
    old={r['panel_id']:r for r in previous['panels']}
    if not (OUT/'before.json').exists():save('before.json',{'page':original['pages']['P006'],'bundle':previous})
    page=copy.deepcopy(original['pages']['P006'])
    panel=next(p for p in page['panels'] if p['id']==TARGET)
    moment='只画能天使的头肩特写。能天使轻轻偏头、明亮微笑，招呼画外的博士看镜头。手机仍由能天使拿着，但手机和双手都在画外；背景只有虚化的摩天轮灯光。'
    panel.update(purpose='长期项目：'+moment,shot='能天使头肩表情特写',angle='平视略侧面',
        composition='单人头肩特写，头顶光环完整入镜；'+moment,main_instant=moment,
        background='蓝调傍晚，摩天轮暖色灯光虚化成柔和光点，没有其他人物入镜',props=[],
        transition_in='从双人自拍准备切到能天使的表情',transition_out='切入下一格手机拍下的双人合影',
        drawing_description=moment+' 保留红褐短发、橙眼、人耳、头顶白色光环、白色外套与黑色肩部。对白后期排版，不画进图片。')
    panel['characters']=[dict(character_id='C001',position='画面中央，头肩入镜',gaze='朝前方手机镜头方向',
        action='轻轻偏头，微笑招呼博士看镜头',expression='明亮、轻松的笑容')]
    panel['state_after']['O012.state']='准备手机自拍，尚未保存照片'
    page['lettering']=[t for t in page['lettering'] if t['panel_id']!=TARGET]
    page['lettering'].append(dict(id=TARGET+'-T01',panel_id=TARGET,kind='dialogue',speaker_id='C001',
        text='看镜头！',bubble_position='画面下侧留白，避开脸部和光环',order=1))
    page['lettering'].sort(key=lambda t:(t['panel_id'],t['order']))
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
                'Solo head-and-shoulders portrait of Exusiai, slight head tilt and a bright relaxed smile, '
                'looking toward the camera. Short fluffy reddish-brown hair, orange eyes, normal human ears, '
                'one floating white halo entirely inside the top of the frame. White jacket collar and '
                'black shoulders. Frame ends at upper chest, both hands outside the crop. Soft blurred '
                'Ferris-wheel lights at blue hour, warm light on her face, clean anime illustration.')
            row['negative']=('hands in frame, arms in foreground, smartphone, screen, buttons, camera device, '
                'extra person, hooded person, penguin, animal ears, mask, cropped halo, text, speech bubbles, watermark')
    assert {r['panel_id'] for r in document['panels'] if
        (r['positive'],r['negative'])!=(old[r['panel_id']]['positive'],old[r['panel_id']]['negative'])}<={TARGET}
    save('storyboard-prompts.json',document)
    preview=action({'action':'preview_prompt_import','project':PID,'document':document})
    assert preview['valid'] and not preview['missing'] and not preview['warnings'],preview
    bundle=action({'action':'save_prompt_import','project':PID,'document':document,'preview_hash':preview['preview_hash'],'confirm':True})
    request=dict(action='draw_imported',project=PID,bundle=bundle['id'],targets=[TARGET],candidate_count=8,checkpoint=MODEL,steps=35)
    save('prepared.json',{'request':request,'old_images_preserved':True,'dialogue':'看镜头！'})
    try:result=action(request)
    except Exception:
        matches=[]
        for path in (ROOT/'data/jobs').glob('*.json'):
            j=json.loads(path.read_text(encoding='utf-8'));r=j.get('request',{})
            if j.get('project')==PID and r.get('bundle')==bundle['id'] and r.get('targets')==[TARGET]:matches.append(j)
        if len(matches)!=1:raise
        result={'job':matches[0]['id'],'recovered':True}
    assert result.get('job'),result
    save('generation-started.json',{'request':request,'submission':result,'follow_after_start':False})
    print(json.dumps(result,ensure_ascii=False))
    j=get('/api/job?id='+result['job'])
    print(json.dumps({k:j.get(k) for k in ('status','message','progress','error')},ensure_ascii=False))


if __name__=='__main__':main()
