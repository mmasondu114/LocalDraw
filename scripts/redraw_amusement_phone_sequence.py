"""Revise the last-page phone sequence atomically and render only K02-K04."""
import copy
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from comic_workflow import engine as e,prompt_exchange
from comic_workflow.repository import Repository
from redraw_amusement_page_one import action,get,MODEL,PID

OUT=ROOT/'data/authored-stories/amusement-day-20261018-phone-sequence'
TARGETS=['P006-K02','P006-K03','P006-K04']


def save(name,value):
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')


def role(cid,position,act):
    return dict(character_id=cid,position=position,gaze='按本格手机取景与动作',action=act,expression='自然轻松，局部镜头不额外显示人脸')


def prop(pid,position,state):
    return dict(prop_id=pid,position=position,state=state)


def main():
    receipt=OUT/'generation-started.json'
    if receipt.exists():print(receipt.read_text(encoding='utf-8'));return
    assert not [j for j in get('/api/status')['jobs'] if j['status'] in
                {'running','queued','paused','pausing','cancelling'}], 'Another job is active'
    repo=Repository();original=repo.get(PID)
    previous=prompt_exchange.current_import(repo,PID)['bundle']
    old={r['panel_id']:r for r in previous['panels']}
    if not (OUT/'before.json').exists():save('before.json',{'project':original,'bundle':previous})
    q=copy.deepcopy(original);page=q['pages']['P006']
    for p in q['canon']['props']:
        if p['id']=='O011':p['description']='能天使的一部深色窄边框智能手机，使用前置摄像头自拍，并在相册中查看照片'
        if p['id']=='O012':p['description']='保存在能天使手机相册里的双人电子合影，背景为亮灯的摩天轮，无纸质打印件'
    scene=next(s for s in q['canon']['scenes'] if s['id']=='S006')
    scene.update(name='摩天轮前与出口相连的亮灯步道',landmarks=['亮灯的摩天轮','出口步道暖色灯串'])
    common='蓝调傍晚的摩天轮前步道，暖色灯光，无合影机、摄影师或其他游客'
    descriptions={
        'P006-K02':('手机快门与手部特写','斜俯视',
            '能天使戴露指手套的右手握住同一部手机，右拇指轻触屏幕下方的圆形快门，启动短倒计时。画面只取手机下半部、右手、白袖口和红腕带；上方取景预览裁出画外，不画人脸。'),
        'P006-K03':('手机前置镜头里的双人合影','前置自拍视角略高于眼睛',
            '画面就是手机拍到的合影：能天使在左侧微笑，右手仍在画外举手机，左手比V字；博士在右侧，右手戴黑手套比V字，左手掌心向上捧着唯一的小企鹅，玩偶在胸前。两人身后是亮灯的摩天轮。博士保持炭灰色兜帽与长外套、宽松不贴脸的深色面罩。'),
        'P006-K04':('手机相册回看特写','近距离斜俯视',
            '能天使把同一部手机放低，右手握着手机给博士查看相册。屏幕显示刚拍好的双人合影：红发能天使、深色兜帽博士和身后的摩天轮。画面只见手机、能天使露指手套和白袖口；右侧边缘是博士左手掌心托着的小企鹅及炭灰衣袖。真实人脸在画外，不出现打印照片、合影机或胸袋中的企鹅。')}
    for panel in page['panels']:
        if panel['id'] not in TARGETS:continue
        shot,angle,moment=descriptions[panel['id']]
        panel.update(purpose='长期项目：'+moment,shot=shot,angle=angle,composition=shot+'；'+moment,
            main_instant=moment,background=common,drawing_description=moment+' 光线：蓝调傍晚与柔和暖灯，手机屏幕亮度自然。')
        state=panel['state_after']
        state.update({'C001.location':'摩天轮前步道左侧','C002.location':'摩天轮前步道右侧',
            'O007.holder':'C002左手','O007.state':'左手掌心向上捧着小企鹅',
            'O003.holder':'C002外套口袋'})
        if panel['id']=='P006-K02':
            panel['characters']=[role('C001','手机下方的右手局部','右手握手机、右拇指点屏幕快门启动倒计时')]
            panel['props']=[prop('O011','C001右手','前置拍照模式，点击屏幕快门启动短倒计时')]
            state.update({'O012.holder':'尚未拍摄','O012.state':'手机倒计时已启动，尚未保存照片'})
        elif panel['id']=='P006-K03':
            panel['characters']=[role('C001','画面左侧','左手比V，右手在画外持手机，面对前置镜头微笑'),
                role('C002','画面右侧','右手比V，左手掌心向上捧企鹅，面罩宽松不贴脸')]
            panel['props']=[prop('O007','C002左手掌心，胸前','唯一的小企鹅由博士左手捧着')]
            state.update({'O012.holder':'C001手机相册','O012.state':'已保存的摩天轮双人电子合影'})
        else:
            panel['characters']=[role('C001','画面左侧手部局部','右手握手机展示相册照片'),
                role('C002','画面右侧手部局部','左手继续捧企鹅，靠近手机看照片')]
            panel['props']=[prop('O011','C001右手','相册显示刚拍好的合影'),
                prop('O012','O011手机屏幕内','一张电子合影，无纸质照片'),
                prop('O007','C002左手掌心，画面右侧边缘','左手继续托着小企鹅')]
            state.update({'O012.holder':'C001手机相册','O012.state':'已保存的摩天轮双人电子合影'})
    # The closing long shot is not redrawn; only its hidden continuity state is
    # reconciled so export cannot resurrect a printed photo after phone capture.
    closing=page['panels'][-1]
    closing['state_after'].update({'O012.holder':'C001手机相册','O012.state':'已保存的摩天轮双人电子合影',
        'O007.holder':'C002左手','O007.state':'左手捧着小企鹅离园','O003.holder':'C002外套口袋'})
    page['state_out']=copy.deepcopy(closing['state_after'])
    assert q['pages']['P006']['panels'][0]==original['pages']['P006']['panels'][0]
    assert all(q['pages'][k]==v for k,v in original['pages'].items() if k!='P006')
    e.validate_project(q,complete=True)
    if q!=original:
        repo.commit(PID,q,e.digest(original),{'source':'explicit_user_revision',
            'description':'P006-K02/K03/K04改为手机快门、前置自拍、手机回看；企鹅由博士左手捧着；同步末页电子照片道具状态',
            'render_targets':TARGETS})
    document=prompt_exchange.export_storyboard(repo,PID)
    positives={
        'P006-K02':('Extreme close-up of ONE smartphone held in Exusiai right hand, a dark fingerless glove '
            'with exposed thumb, white cuff and small red wrist band. The same right thumb gently taps the '
            'simple circular shutter icon at the bottom of the touchscreen to start a selfie countdown. '
            'Only the lower half of the phone and the single holding hand are framed; the upper live preview '
            'is outside the crop. Minimal camera controls without text, blurred blue-hour carnival lights, '
            'anime object-and-hand detail, no faces or bodies visible.'),
        'P006-K03':('The actual smartphone front-camera selfie fills the entire image, slightly elevated '
            'wide-angle waist-up framing of exactly two companions, illuminated Ferris wheel centered behind '
            'them at blue hour. LEFT Exusiai smiles, short reddish-brown hair, orange eyes, normal human ears, '
            'white halo above her head and pale geometric wing fragments, white jacket with black shoulders. '
            'Her LEFT hand makes a V sign beside her face; her right arm extends toward the camera holding '
            'the phone outside the image. RIGHT the Doctor wears an entirely matte charcoal-black hooded long '
            'coat with matching dark sleeves and subtle teal trim. A roomy opaque angular dark face covering '
            'stands away from the face under the hood, plain surfaces without nose, lip or chin contours. '
            'Doctor RIGHT black-gloved hand makes a V sign. Doctor LEFT open palm supports ONE tiny black-and-white '
            'penguin plush with orange beak at chest height, left elbow bent. Empty dark chest pocket, '
            'clear separate arms, warm Ferris-wheel lights, cheerful anime souvenir selfie.'),
        'P006-K04':('Close-up looking down at ONE smartphone held in Exusiai right hand with a dark fingerless '
            'glove and white jacket cuff. The phone screen displays ONE saved selfie: a smiling red-haired '
            'woman on the left and a fully charcoal-hooded masked companion on the right, illuminated Ferris '
            'wheel behind them. The displayed picture is confined within the screen. At the right foreground '
            'edge, the Doctor LEFT black-gloved open palm supports a tiny black-and-white penguin plush with '
            'orange beak, forearm emerging from a dark charcoal sleeve. Only these two real hands and props '
            'are in the main scene; both actual heads and bodies are outside the crop. Dark phone bezel, '
            'simple photo viewer with no readable lettering, soft evening bokeh, anime illustration.')}
    negatives={
        'P006-K02':'faces, heads, full body, extra hands, extra phone, photo booth, kiosk, physical large button, printed photograph, text, watermark',
        'P006-K03':'photo booth, phone border, phone screen in frame, photographer, extra people, duplicate character, extra arms, merged hands, duplicate penguin, penguin in chest pocket, penguin in right hand, white coat on right person, white hood on right person, skin-tight mask, sculpted nose on mask, visible lips, animal ears, text, watermark',
        'P006-K04':'printed photograph, paper photo, photo strip, photo booth, printer, second smartphone, extra hands, extra plush outside screen, full-size faces outside screen, full-size people outside screen, white sleeve on right hand, text, watermark'}
    for row in document['panels']:
        row.update(positive=old[row['panel_id']]['positive'],negative=old[row['panel_id']]['negative'])
        if row['panel_id'] in TARGETS:row.update(positive=positives[row['panel_id']],negative=negatives[row['panel_id']])
    changes={r['panel_id'] for r in document['panels'] if
        (r['positive'],r['negative'])!=(old[r['panel_id']]['positive'],old[r['panel_id']]['negative'])}
    assert changes<=set(TARGETS)
    save('storyboard-prompts.json',document)
    preview=action({'action':'preview_prompt_import','project':PID,'document':document})
    assert preview['valid'] and not preview['missing'],preview
    save('preflight.json',{'warnings':preview['warnings'],'render_targets':TARGETS})
    bundle=action({'action':'save_prompt_import','project':PID,'document':document,'preview_hash':preview['preview_hash'],'confirm':True})
    request=dict(action='draw_imported',project=PID,bundle=bundle['id'],targets=TARGETS,candidate_count=8,checkpoint=MODEL,steps=35)
    save('prepared.json',{'request':request,'total_images':24,'old_images_preserved':True})
    try:result=action(request)
    except Exception:
        matches=[]
        for path in (ROOT/'data/jobs').glob('*.json'):
            j=json.loads(path.read_text(encoding='utf-8'));r=j.get('request',{})
            if j.get('project')==PID and r.get('bundle')==bundle['id'] and r.get('targets')==TARGETS:matches.append(j)
        if len(matches)!=1:raise
        result={'job':matches[0]['id'],'recovered':True}
    assert result.get('job'),result
    save('generation-started.json',{'request':request,'submission':result,'follow_after_start':False})
    print(json.dumps(result,ensure_ascii=False))
    j=get('/api/job?id='+result['job'])
    print(json.dumps({k:j.get(k) for k in ('status','message','progress','error')},ensure_ascii=False))


if __name__=='__main__':main()
