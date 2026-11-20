"""Make the active comic five pages; retain all page-six material for recovery."""
import copy
import json
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from comic_workflow import engine as e,prompt_exchange,comfyui
from comic_workflow.repository import Repository
from redraw_amusement_page_one import action,get,PID

OUT=ROOT/'data/authored-stories/amusement-day-five-pages'


def save(name,value):
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')


def main():
    assert not [j for j in get('/api/status')['jobs'] if j.get('project')==PID and
                j['status'] in {'running','queued','paused','pausing','cancelling'}], 'Wait for cancellation to finish'
    repo=Repository();p=repo.get(PID)
    if 'P006' not in p['pages']:
        print(json.dumps({'already_five_pages':True}));return
    current=prompt_exchange.current_import(repo,PID)['bundle']
    workspace=get('/api/workspace?id='+PID)
    drawing=comfyui.drawing_state(repo,PID)
    archived_batches=[b for b in drawing['batches'] if b['panel_id'].startswith('P006-')]
    image_files=[repo.folder(PID)/'images'/image['file'] for b in archived_batches for image in b.get('images',[])]
    assert all(path.is_file() for path in image_files)
    if not (OUT/'six-page-backup.json').exists():
        save('six-page-backup.json',{'project':p,'prompt_bundle':current,'workspace':workspace})
        save('page-six-drawing-backup.json',{'batches':archived_batches,
            'selections':{k:v for k,v in drawing['selections'].items() if k.startswith('P006-')}})
    q=copy.deepcopy(p)
    q['pages'].pop('P006')
    q['brief']['page_count']=5
    q['brief']['direction']='/能天使 和 /博士 相约游乐园，从精确规划到享受没有截止时间的一天；在日落摩天轮上，以“建议下次继续”的回应收尾。'
    q['brief']['must_include']=[x for x in q['brief']['must_include'] if '合影' not in x]
    q['plan']['pages']=[page for page in q['plan']['pages'] if page['id']!='P006']
    q['outline']['beats']=q['outline']['beats'][:5]
    q['outline']['ending']='日落摩天轮上，博士用“建议下次继续”回应能天使；两人在夕阳中笑着结束这一天的故事。'
    q['outline']['must_include_mapping']=[s for s in q['outline']['must_include_mapping'] if not s.startswith('P006')]
    names={c['id']:c['name'] for c in q['canon']['characters']}
    script=['《'+q['brief']['title']+'》','五页日常短篇 · 能天使与博士',
            '关系设定：轻松温暖的同伴出游；博士保持中性称谓与兜帽遮面形象。']
    for plan in q['plan']['pages']:
        page=q['pages'][plan['id']]
        script+=['',f"第 {page['number']} 页：{plan['purpose']}",plan['beat']]
        for panel in page['panels']:
            script+=['',panel['id']+'｜'+panel['shot']+'，'+panel['angle']+'。',panel['main_instant']]
            lines=sorted([t for t in page['lettering'] if t['panel_id']==panel['id']],key=lambda t:t['order'])
            script.extend([names.get(t.get('speaker_id'),'旁白')+'：'+t['text'] for t in lines] or ['（无对白）'])
    q['script']='\n'.join(script)+'\n'
    q['script_source']['original']=q['script']
    q['script_source'].setdefault('changes',[]).append('按用户要求移除第六页，保留前五页，在摩天轮场景结束；第六页资料已备份。')
    assert all(q['pages'][key]==value for key,value in p['pages'].items() if key!='P006')
    e.validate_project(q,complete=True)
    saved=repo.commit(PID,q,e.digest(p),{'source':'explicit_user_revision',
        'description':'移除第六页，作品改为五页；原第六页分镜、选图与图片文件保留备份',
        'backup':str(OUT/'six-page-backup.json')})
    old={r['panel_id']:r for r in current['panels']}
    document=prompt_exchange.export_storyboard(repo,PID)
    for row in document['panels']:
        row.update(positive=old[row['panel_id']]['positive'],negative=old[row['panel_id']]['negative'])
    assert len(document['panels'])==25 and all(not r['panel_id'].startswith('P006') for r in document['panels'])
    preview=action({'action':'preview_prompt_import','project':PID,'document':document})
    assert preview['valid'] and not preview['missing'],preview
    bundle=action({'action':'save_prompt_import','project':PID,'document':document,'preview_hash':preview['preview_hash'],'confirm':True})
    workspace['stage']='drawing';workspace['target']='';workspace['updated']=int(time.time()*1000)
    for key,view in workspace.get('views',{}).items():
        fields=view.get('fields',{})
        if not view.get('dirty') and 'story-text' in fields:fields['story-text']=q['script']
    action({'action':'save_workspace','project':PID,'state':workspace})
    export=repo.export(PID)
    assert all(path.is_file() for path in image_files)
    (OUT/'剧本与分镜-五页版.txt').write_text(q['script'],encoding='utf-8')
    save('storyboard-prompts-five-pages.json',document)
    result={'project':PID,'version':saved['version'],'pages':len(saved['pages']),
        'panels':25,'archived_page_six_image_count':len(image_files),'images_deleted':0,
        'prompt_bundle':bundle['id'],'export':export,'cancelled_job':'66a2979ab3204765863717899f071734'}
    save('completed.json',result)
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
