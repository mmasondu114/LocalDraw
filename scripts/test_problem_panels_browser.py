"""Exercise issue review in a temporary studio; never run inference or edit user projects."""
import json
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import Mock, patch
from http.server import ThreadingHTTPServer
from PIL import Image
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from comic_workflow import engine as e, manual_drawing, prompt_exchange as exchange, problem_panels as issues
from comic_workflow.repository import Repository
from comic_workflow.server import Application, make_handler

def main():
    out=ROOT/'data/test-results/problem-panels';out.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);repo=Repository(root/'projects');pid=repo.create(None,True);project=repo.get(pid)
        doc=exchange.export_storyboard(repo,pid)
        for row in doc['panels']:row.update(positive='POV, two cups.',negative='watermark')
        preview=exchange.preview(repo,pid,doc)
        exchange.save_import(repo,pid,dict(document=doc,preview_hash=preview['preview_hash'],confirm=True))
        batches=[]
        for row in doc['panels'][:2]:
            panel=next(p for page,p in exchange.panels(project) if p['id']==row['panel_id']);key=panel['id']
            images=[]
            for n in range(1,10):
                path=repo.folder(pid)/f'images/{key}/{n}.png';path.parent.mkdir(parents=True,exist_ok=True)
                Image.new('RGB',(320,400),(80+n*40,120,150)).save(path)
                images.append(dict(id=f'{key}-{n}',file=f'{key}/{n}.png',view_index=n,review={'verdict':'unreviewed'}))
            batches.append(dict(id=key,panel_id=key,panel_hash=e.digest(panel),images=images,status='done'))
        e.save(repo.folder(pid)/'images/index.json',dict(batches=batches,selections={}))
        e.save(repo.folder(pid)/'workspace.json',dict(stage='drawing',views={},updated=1))
        with patch('comic_workflow.jobs.DATA',root):app=Application(repo)
        app.model=Mock();app.model.status.return_value=dict(ready=False,owned=False,generating=False,file_size_ok=True)
        app.model.clear_context.return_value=None
        captured=[];original=app.action
        finish_storyboard=threading.Event()
        def action(request):
            if request['action']=='story_draft':
                result={'original':request['text'],'mode':'structure','payload':{
                    'story':request['text'],'brief':{**project['brief'],'panel_mode':'flexible'},'canon':project['canon'],
                    'outline':{**project['outline'],'selected_option':'direct'},'assumptions':[],'changes':[]}}
                return {'job':app.jobs.submit('整理剧本草稿',lambda update,jid:result)}
            if request['action']=='storyboard_all':
                def storyboard(update,jid):
                    assert finish_storyboard.wait(15)
                    bundle={'id':jid,'base_hash':request['base_hash'],'status':'complete','plan':project['plan'],
                            'pages':{key:{'payload':page,'metadata':{}} for key,page in project['pages'].items()}}
                    e.save(repo.folder(request['project'])/'storyboard-drafts'/jid/'bundle.json',bundle)
                    return {'bundle':jid}
                return {'job':app.jobs.submit('生成整部分镜',storyboard,request['project'],request=request)}
            if request['action']=='draw':
                captured.append(manual_drawing.prepare_request(repo,pid,request));return {'captured':True}
            if request['action']=='redraw_problems':
                captured.append(issues.prepare_drawing(repo,pid));return {'captured':True}
            if request['action']=='understand_problems':
                document=issues.export(repo,pid)
                for row in document['panels']:row.update(understanding='本地测试：只保留一个杯子。',changes='删除多余杯子。',positive='POV, one cup.',negative='watermark')
                issues.import_result(repo,pid,document,'llm');return {'captured':True}
            return original(request)
        app.action=action
        server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(app));threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            with sync_playwright() as pw:
                browser=pw.chromium.launch(channel='chrome',headless=True)
                page=browser.new_page(viewport={'width':1440,'height':1050});errors=[]
                page.set_default_timeout(15000)
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{server.server_port}/');page.wait_for_function('typeof openProject==="function" && boot?.projects?.length')
                page.evaluate('(pid)=>openProject(pid)',pid);page.evaluate('window.leaveStudioHome()')
                reader=page.locator('.panel-reader')
                expect(reader).to_contain_text(project['pages']['P001']['panels'][0]['main_instant'])
                reader.focus()
                page.keyboard.press('8')
                expect(reader.locator('[data-slot="8"]')).to_have_attribute('aria-pressed','true')
                assert repo.get(pid)==project
                page.keyboard.press('ArrowRight');expect(reader.locator('h2')).to_contain_text('P001-K02')
                page.keyboard.press('ArrowLeft');expect(reader.locator('h2')).to_contain_text('P001-K01')
                positive=reader.get_by_label('正向提示词',exact=True)
                positive.fill('POV, one cup')
                positive.press('End');positive.press('1');positive.press('ArrowDown')
                expect(positive).to_have_value('POV, one cup1')
                expect(page.locator('dialog[open]')).to_have_count(0)
                reader.get_by_label('负向提示词',exact=True).fill('duplicate cup')
                reader.get_by_role('button',name='重画这一格',exact=True).click()
                expect(page.locator('#notice')).to_contain_text('已开始绘制')
                assert captured[-1]['candidate_count']==8 and captured[-1]['targets']==['P001-K01']
                saved=exchange.load_import(repo,pid,captured[-1]['bundle'])['panels'][0]
                assert (saved['positive'],saved['negative'])==('POV, one cup1','duplicate cup')
                with page.expect_download() as dl:reader.get_by_role('button',name='导出分镜',exact=True).click()
                prompt_file=json.loads(Path(dl.value.path()).read_text(encoding='utf-8'))
                assert any('POV' in rule for rule in prompt_file['prompt_writing_guidance'])
                imported=prompt_file['panels'][0];imported.update(positive='POV, a teapot.',negative='text')
                prompt_file['panels']=[imported]
                reader.locator('input[type=file]').set_input_files(dict(name='test.json',mimeType='application/json',buffer=json.dumps(prompt_file).encode()))
                page.get_by_role('button',name='确认使用 1 格提示词',exact=True).click()
                expect(positive).to_have_value('POV, a teapot.')
                for width in [375,1440]:
                    page.set_viewport_size({'width':width,'height':900})
                    left=reader.get_by_role('button',name='← 上一页').bounding_box()
                    center=reader.locator('.reader-figure').bounding_box()
                    right=reader.get_by_role('button',name='下一页 →').bounding_box()
                    assert left['x']+left['width']<=center['x'] and center['x']+center['width']<=right['x']+1
                    assert right['x']+right['width']<=width
                page.screenshot(path=str(out/'panel-reader.png'))
                reader.focus()
                for key in ['P001-K01','P001-K02']:
                    page.keyboard.press('ArrowDown')
                    note=page.get_by_label('这格有什么问题？',exact=True)
                    expect(note).to_be_focused()
                    note.fill(key+' 只画一个杯子');note.press('Enter')
                    expect(page.locator('dialog[open]')).to_have_count(0)
                expect(reader.locator('h2')).to_contain_text('P001-K03')
                for _ in range(len(doc['panels'])-2):
                    reader.focus();page.keyboard.press('Enter')
                cards=page.locator('.problem-card');expect(cards).to_have_count(2)
                expect(cards.first.locator('.panel-story-preview')).to_contain_text(project['pages']['P001']['panels'][0]['main_instant'])
                for line in project['pages']['P001']['lettering']:
                    if line['panel_id']=='P001-K01':expect(cards.first.locator('.panel-story-dialogue')).to_contain_text(line['text'])
                for n in range(2):cards.nth(n).get_by_label('这格有什么问题？').fill(f'第 {n+1} 格只要一个杯子')
                cards.nth(0).get_by_label('这一格重画几张').fill('2');cards.nth(1).get_by_label('这一格重画几张').fill('3')
                page.locator('.problem-methods > summary').click()
                with page.expect_download() as dl:page.get_by_role('button',name='导出问题 JSON 给 GPT',exact=True).click()
                exported=json.loads(Path(dl.value.path()).read_text(encoding='utf-8'))
                assert len(exported['panels'])==2
                for row in exported['panels']:row.update(understanding='只保留一个杯子，保持 POV。',changes='删去多余杯子。',positive='POV, exactly one cup.',negative='watermark')
                page.get_by_label('GPT 返回的 JSON',exact=True).fill(json.dumps(exported,ensure_ascii=False))
                page.get_by_role('button',name='导入粘贴的 JSON',exact=True).click()
                expect(page.get_by_text('GPT 理解结果',exact=True)).to_have_count(2)
                page.get_by_role('button',name='按问题说明全部重画',exact=True).click()
                page.wait_for_timeout(200)
                assert [len(v) for v in captured[-1]['candidate_slots_by_panel'].values()]==[2,3]
                page.evaluate('document.querySelectorAll("*").forEach(el=>{if(el.scrollTop)el.scrollTop=0;});window.scrollTo(0,0)')
                page.screenshot(path=str(out/'gpt-review.png'),full_page=True)
                page.locator('.problem-methods > summary').click()
                page.get_by_label('问题理解方式',exact=True).select_option('llm')
                with page.expect_response(lambda r:r.url.endswith('/api/action') and r.request.post_data_json.get('action')=='understand_problems'):
                    page.get_by_role('button',name='让 LLM 理解全部问题',exact=True).click()
                page.get_by_role('button',name='刷新结果',exact=True).click()
                expect(page.get_by_text('LLM 理解结果',exact=True)).to_have_count(2)
                card=page.locator('.problem-card').first
                gallery=card.locator('details').filter(has=page.locator('summary',has_text='查看与选择候选图')).first
                gallery.locator(':scope > summary').click()
                card.get_by_role('button',name='选用此图',exact=True).first.click()
                expect(gallery).not_to_have_attribute('open','')
                gallery.locator(':scope > summary').click()
                expect(page.locator('.problem-card').first.get_by_role('button',name='✓ 已选用',exact=True)).to_be_visible()
                page.locator('.problem-card').first.get_by_role('button',name='已满意，解除问题标记',exact=True).click()
                expect(page.locator('.problem-card')).to_have_count(1)
                page.reload();page.wait_for_function('boot?.projects?.length');page.evaluate('(pid)=>openProject(pid)',pid);page.evaluate('window.leaveStudioHome()')
                expect(page.locator('.problem-card')).to_have_count(1)
                assert issues.state(repo,pid)['items'][0]['candidate_count']==3
                page.get_by_role('button',name='＋ 新作品',exact=True).click()
                page.get_by_label('故事文本',exact=True).fill('I find a paper boat on the counter.')
                page.get_by_role('button',name='从故事生成分镜',exact=True).click()
                expect(page.locator('body')).to_have_attribute('data-stage','page',timeout=15000)
                expect(page.locator('#jobs.has-running')).to_contain_text('生成整部分镜')
                finish_storyboard.set()
                with page.expect_download() as dl:page.get_by_role('button',name='确认并导出分镜',exact=True).click()
                fresh=json.loads(Path(dl.value.path()).read_text(encoding='utf-8'))
                assert fresh['project_id']!=pid and len(fresh['panels'])==len(doc['panels'])
                expect(page.locator('.panel-reader')).to_be_visible()
                assert page.get_by_label('正向提示词',exact=True).input_value().startswith('first-person POV')
                assert not errors,errors
                browser.close()
                print('Browser passed: mark, persist, GPT export/import, LLM route, per-panel counts, select, resolve. No inference.')
        finally:server.shutdown();server.server_close()

if __name__=='__main__':main()
