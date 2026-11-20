"""Exercise the real drawing dialog with pointer input; action capture starts no NN."""
import json
import copy
from pathlib import Path
import shutil
import sys
import threading
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from comic_workflow import engine as e, guided_hand, comfyui
from comic_workflow.repository import Repository
from comic_workflow.server import Application,make_handler


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    source=ROOT/'data/test-results/native-studio-batch/20261007-234949-400872'
    out=ROOT/'data/test-results/guided-hand-browser'/e.stamp();out.mkdir(parents=True)
    shutil.copytree(source/'projects',out/'projects')
    shutil.copytree(source/'characters',out/'characters')
    with patch('comic_workflow.jobs.DATA',out):app=Application(Repository(out/'projects'))
    pid=next(p.name for p in app.repo.root.iterdir() if p.is_dir())
    project=app.repo.get(pid)
    captured=[]
    original_state=comfyui.drawing_state
    review_reason='浏览器验收占位：手腕连接仍需核对'
    def state_with_review(repo,project_id):
        state=copy.deepcopy(original_state(repo,project_id))
        state['batches'][0]['images'][0]['review']={'verdict':'reject','reason':review_reason}
        return state
    def capture_action(request):
        if request['action']=='local_repair':
            captured.append(request);e.save(out/f'request-{len(captured)}.json',request)
            return {'job':'ui-contract-only'}
        raise ValueError('Browser QA never starts inference')
    app.action=capture_action
    app.model.status=lambda:{'ready':False,'owned':False,'generating':False,'file_size_ok':True}
    server=ThreadingHTTPServer(('127.0.0.1',8794),make_handler(app))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with patch('comic_workflow.comfyui.discover',return_value={'ready':False,'checkpoints':['waiIllustriousSDXL_v170.safetensors'],'loras':[],'configured':'waiIllustriousSDXL_v170.safetensors'}),\
             patch('comic_workflow.server.inspect_local',return_value={}):
            with patch.object(comfyui,'drawing_state',side_effect=state_with_review):
                run_browser(project,pid,out,captured,review_reason)
        print(out.relative_to(ROOT).as_posix(),flush=True)
    finally:
        server.shutdown();server.server_close()


def run_browser(project,pid,out,captured,review_reason):
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='chrome',headless=True)
        page=browser.new_page(viewport={'width':1800,'height':1300})
        errors=[];page.on('pageerror',lambda ex:errors.append(str(ex)))
        page.goto('http://127.0.0.1:8794')
        # Use the same project/open-view functions as the sidebar.
        page.evaluate('(id)=>openProject(id)',pid)
        page.screenshot(path=str(out/'drawing-before-dialog.png'),full_page=True)
        repair=page.get_by_role('button',name='框选错误区域修复',exact=True).first
        repair.wait_for(timeout=15000)
        assert page.get_by_text('AI 核对：未通过（供参考）',exact=True).is_visible()
        page.get_by_text('生成详情',exact=True).click()
        assert page.get_by_text(review_reason,exact=True).is_visible()
        page.wait_for_function('()=>document.querySelector(".gallery-image img")?.naturalWidth>0')
        repair.click()
        dialog=page.locator('dialog[open]');main=dialog.locator('canvas').first
        bounds=main.bounding_box()
        rect=[1006/1824,575/1248,1074/1824,650/1248]
        def position(point,box):return (box['x']+point[0]*box['width'],box['y']+point[1]*box['height'])
        page.mouse.move(*position(rect[:2],bounds));page.mouse.down();page.mouse.move(*position(rect[2:],bounds),steps=8);page.mouse.up()
        dialog.get_by_label('修复放松的手形（手背朝外）').check()
        assert dialog.get_by_label('修复角色').input_value()==''
        assert dialog.get_by_label('角色的左手或右手').input_value()==''
        assert dialog.get_by_label('手套颜色').input_value()==''
        owner=next(c['id'] for c in project['canon']['characters'] if c['name']=='甘雨')
        dialog.get_by_label('修复角色').select_option(owner)
        dialog.get_by_label('角色的左手或右手').select_option('right')
        dialog.get_by_label('手套颜色').select_option('black')
        hand=dialog.locator('canvas').nth(1);hand.scroll_into_view_if_needed()
        pad=max(rect[2]-rect[0],rect[3]-rect[1])*.35
        crop=[max(0,rect[0]-pad),max(0,rect[1]-pad),min(1,rect[2]+pad),min(1,rect[3]+pad)]
        for x,y in [(1018/1824,593/1248),(1038/1824,615/1248)]:
            box=hand.bounding_box();page.mouse.click(*position([(x-crop[0])/(crop[2]-crop[0]),(y-crop[1])/(crop[3]-crop[1])],box))
        assert '中指根部' in dialog.inner_text()
        page.wait_for_function("()=>[...document.querySelectorAll('dialog canvas')].some(c=>c.dataset.guideFingers==='5')")
        dialog.get_by_label('只修描出的手部（保护袖口）').check()
        outline=[(1014/1824,580/1248),(1069/1824,581/1248),(1069/1824,646/1248),(1015/1824,646/1248)]
        for x,y in outline:
            hand.scroll_into_view_if_needed();box=hand.bounding_box()
            page.mouse.click(*position([(x-crop[0])/(crop[2]-crop[0]),(y-crop[1])/(crop[3]-crop[1])],box))
        page.screenshot(path=str(out/'guided-hand-dialog.png'),full_page=True)
        dialog.get_by_role('button',name='生成局部修复候选',exact=True).click()
        page.wait_for_timeout(300)
        assert len(captured)==1,captured
        request=captured[0]
        guided_hand.validate_request(request['hand_guidance'],request['rect'],{owner})
        assert request['hand_guidance']['glove']=='black'
        assert len(request['hand_guidance']['outline'])==4
        for actual,expected in zip(request['hand_guidance']['outline'],outline):
            assert all(abs(a-b)<.003 for a,b in zip(actual,expected))
        for actual,expected in zip(request['hand_guidance']['wrist'],[1018/1824,593/1248]):assert abs(actual-expected)<.003
        for actual,expected in zip(request['hand_guidance']['palm'],[1038/1824,615/1248]):assert abs(actual-expected)<.003
        repair.click()
        dialog=page.locator('dialog[open]');main=dialog.locator('canvas').first
        bounds=main.bounding_box()
        page.mouse.move(*position(rect[:2],bounds));page.mouse.down();page.mouse.move(*position(rect[2:],bounds),steps=8);page.mouse.up()
        partner=next(c['id'] for c in project['canon']['characters'] if c['name']=='刻晴')
        dialog.get_by_label('修复角色').select_option(partner)
        dialog.locator('textarea').fill('修复刻晴的白色花边袖口，保留手形')
        dialog.get_by_label('沿边缘描出修复区域（先拖红框，再点击描边）').check()
        main.scroll_into_view_if_needed();bounds=main.bounding_box()
        for point in ([rect[0]+.002,rect[1]+.002],[rect[2]-.002,rect[1]+.002],[(rect[0]+rect[2])/2,rect[3]-.002]):
            page.mouse.click(*position(point,bounds))
        page.screenshot(path=str(out/'owned-cuff-dialog.png'),full_page=True)
        dialog.get_by_role('button',name='生成局部修复候选',exact=True).click()
        page.wait_for_timeout(300)
        assert len(captured)==2,captured
        assert captured[1]['owner']==partner
        assert 'hand_guidance' not in captured[1]
        assert captured[1]['instruction']=='修复刻晴的白色花边袖口，保留手形'
        assert len(captured[1]['outline'])==3
        old_reference=guided_hand.preview_reference()
        page.route('**/api/hand-guide',lambda route:route.fulfill(status=200,content_type='application/json',body=json.dumps(old_reference)))
        repair.click()
        dialog=page.locator('dialog[open]');main=dialog.locator('canvas').first
        bounds=main.bounding_box()
        page.mouse.move(*position(rect[:2],bounds));page.mouse.down();page.mouse.move(*position(rect[2:],bounds),steps=8);page.mouse.up()
        dialog.get_by_label('修复角色').select_option(partner)
        dialog.locator('textarea').fill('修复刻晴的袖口')
        page.wait_for_timeout(300)
        dialog.get_by_role('button',name='生成局部修复候选',exact=True).click()
        page.get_by_text('当前工作室需重新打开，才能使用角色修复与描边功能',exact=True).wait_for(timeout=3000)
        assert len(captured)==2,'Old backend must never silently ignore selected owner or outline'
        assert not errors,errors
        e.save(out/'result.json',{'status':'passed','real_dialog_and_pointer_input':True,'request':request,
            'browser_errors':errors,'neural_inference_started':False,
            'rejected_review_visible':True,'review_reason_visible':True,'review_result_fixture_only':True,
            'hand_outline_pointer_input_verified':True,'middle_finger_root_label_verified':True,
            'five_finger_reference_preview_verified':True,
            'generic_repair_owner_request':captured[1],
            'generic_repair_owner_selection_verified':True,
            'old_backend_rejects_owner_and_outline_before_submission':True})
        browser.close()


if __name__=='__main__':
    main()
