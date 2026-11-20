"""Exercise the compiled Windows EXE against temporary projects, with no inference."""
import ctypes as ct
from ctypes import wintypes as wt
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from comic_workflow import engine as e, manual_drawing, problem_panels
from comic_workflow.repository import Repository
from comic_workflow.server import Application, make_handler

user = ct.windll.user32
user.SetProcessDpiAwarenessContext(ct.c_void_p(-4))
callback = ct.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
user.SendMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user.SendMessageW.restype = wt.LPARAM


def handles(parent=None):
    found = []
    visit = callback(lambda hwnd, _: found.append(hwnd) is None)
    if parent:
        user.EnumChildWindows(parent, visit, 0)
    else:
        user.EnumWindows(visit, 0)
    return found


def caption(hwnd):
    buf = ct.create_unicode_buffer(2048)
    user.SendMessageW(hwnd, 13, len(buf), ct.addressof(buf))
    return buf.value


def kind(hwnd):
    buf = ct.create_unicode_buffer(128)
    user.GetClassNameW(hwnd, buf, len(buf))
    return buf.value.lower()


class ThreadUI(ct.Structure):
    _fields_ = [('size', wt.DWORD), ('flags', wt.DWORD),
                *[(k, wt.HWND) for k in ('active', 'focus', 'capture', 'menu', 'move', 'caret')], ('rect', wt.RECT)]


def focused(hwnd):
    state = ThreadUI(size=ct.sizeof(ThreadUI))
    return user.GetGUIThreadInfo(user.GetWindowThreadProcessId(hwnd, None), ct.byref(state)) and state.focus == hwnd


def bounds(hwnd):
    rect = wt.RECT()
    assert user.GetWindowRect(hwnd, ct.byref(rect))
    return rect


def wait(check, label):
    stop = time.monotonic() + 15
    while time.monotonic() < stop:
        val = check()
        if val:
            return val
        time.sleep(.025)
    raise AssertionError('Timed out: ' + label)


def control(parent, label):
    return wait(lambda: next((h for h in handles(parent) if caption(h) == label), None), label)


def click(hwnd):
    user.PostMessageW(hwnd, 0xF5, 0, 0)


def key(hwnd, vk):
    user.PostMessageW(hwnd, 0x100, vk, 0)
    user.PostMessageW(hwnd, 0x101, vk, 0)


def put(hwnd, text):
    buf = ct.create_unicode_buffer(text)
    user.SendMessageW(hwnd, 0xB1, 0, -1)  # EM_SETSEL
    user.SendMessageW(hwnd, 0xC2, 1, ct.addressof(buf))  # EM_REPLACESEL also sends EN_CHANGE.


def run(executable=ROOT/'LocalDraw.exe'):
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        repo = Repository(root / 'data/projects')
        sample = repo.get(repo.create(None, True))
        with patch('comic_workflow.jobs.DATA', root):
            app = Application(repo)
        app.jobs.on_finish = None
        original = app.action
        drawn = []
        def action(req):
            pid = req.get('project')
            if req['action'] == 'story_draft':
                payload = dict(story=req['text'], brief={**sample['brief'], 'panel_mode':'flexible'},
                               canon=sample['canon'], outline={**sample['outline'], 'selected_option':'direct'},
                               assumptions=[], changes=[])
                return {'job':app.jobs.submit('故事', lambda *_:dict(payload=payload, original=req['text'], mode='structure'))}
            if req['action'] == 'storyboard_all':
                def board(update, jid):
                    e.save(repo.folder(pid) / 'storyboard-drafts' / jid / 'bundle.json',
                           dict(id=jid, base_hash=req['base_hash'], status='complete', plan=sample['plan'],
                                pages={k:dict(payload=v,metadata={}) for k,v in sample['pages'].items()}))
                    return {'bundle':jid}
                return {'job':app.jobs.submit('分镜', board, pid)}
            if req['action'] == 'draw':
                manual_drawing.prepare_request(repo,pid,req)
                drawn.append(req)
                row = req['prompt_rows'][0]
                images = []
                for n in range(1,9):
                    file = 'fixture/' + str(n) + '.png'
                    path = repo.folder(pid) / 'images' / file
                    path.parent.mkdir(parents=True,exist_ok=True)
                    Image.new('RGB',(90,120),(n*19,33,55)).save(path)
                    images.append(dict(id='image'+str(n), file=file))
                e.save(repo.folder(pid)/'images/index.json',dict(selections={},batches=[dict(
                    id='batch',panel_id=row['panel_id'],panel_hash=row['panel_hash'],images=images)]))
                return {'job':app.jobs.submit('绘图', lambda *_: {},pid)}
            if req['action'] == 'understand_and_redraw':
                assert [r['problem'] for r in problem_panels.export(repo,pid)['panels']] == ['画面只留一只杯子']
                drawn.append(req)
                return {'job':app.jobs.submit('重画', lambda *_: {}, pid)}
            assert req['action'] in {'create_story','accept_storyboard_bundle','select_image','save_problem','preview_prompt_import','save_prompt_import'}, req['action']
            return original(req)
        app.action = action
        server = ThreadingHTTPServer(('127.0.0.1',0),make_handler(app))
        threading.Thread(target=server.serve_forever,daemon=True).start()
        e.save(root/'config/settings.json',dict(port=server.server_port))
        exe = shutil.copy2(executable, root/'LocalDraw.exe')
        shutil.copy2(str(executable)+'.config', root/'LocalDraw.exe.config')
        started = time.perf_counter()
        process = subprocess.Popen([exe], env={**os.environ,'COMIC_STUDIO_ROOT':str(root)})
        def window(title):
            def find():
                for hwnd in handles():
                    pid = wt.DWORD()
                    user.GetWindowThreadProcessId(hwnd,ct.byref(pid))
                    if pid.value == process.pid and caption(hwnd) == title:
                        return hwnd
            return wait(find,title)
        try:
            main = window('本地漫画工作室 · Local Draw')
            dpi = user.GetDpiForWindow(main)
            story = wait(lambda:next((h for h in handles(main) if '.edit.' in kind(h)),None),'native story input')
            wait(lambda:user.IsWindowVisible(story) and user.IsWindowEnabled(story) and focused(story),'visible focused story input')
            put(story,'I see a cup on the counter.')
            assert caption(story)=='I see a cup on the counter.'
            elapsed = time.perf_counter()-started
            assert user.IsZoomed(main), 'the workspace must start maximized'
            click(control(main,'生成 POV 分镜'))
            click(control(main,'确认并导出分镜'))
            save = wait(lambda:next((h for h in handles() if user.GetWindow(h,4)==main and kind(h)=='#32770'),None),'Save dialog')
            file = root/'export.json'
            edit = wait(lambda:next((h for h in handles(save) if kind(h)=='edit' and user.IsWindowVisible(h)),None),'file name')
            put(edit,str(file)); click(user.GetDlgItem(save,1))
            wait(file.exists,'exported storyboard')
            doc = e.read(file); pid = doc['project_id']
            assert len(doc['panels']) == 6
            draw = control(main,'画这一格')
            prompts = [h for h in handles(main) if '.edit.' in kind(h) and 'first-person POV' in caption(h)]
            assert len(prompts) == 1
            first = {**doc['panels'][0], 'positive':'POV, imported cup', 'negative':'watermark'}
            imported = root/'prompts.json'
            e.save(imported, {**doc, 'panels':[first]})
            click(control(main,'导入提示词'))
            op = wait(lambda:next((h for h in handles() if user.GetWindow(h,4)==main and kind(h)=='#32770'),None),'Open file dialog')
            name = wait(lambda:next((h for h in handles(op) if kind(h)=='edit' and user.IsWindowVisible(h)),None),'file name')
            put(name,str(imported)); click(user.GetDlgItem(op,1))
            confirm = window('导入提示词'); click(user.GetDlgItem(confirm,1))
            put(control(main,'POV, imported cup'), 'first-person POV, one cup')
            click(control(main,'画这一格')); control(main,'重画这一格')
            assert drawn[0]['prompt_rows'][0]['positive']=='first-person POV, one cup'
            canvas = wt.RECT()
            assert user.GetClientRect(user.GetParent(control(main,'下一页 →')),ct.byref(canvas))
            assert canvas.bottom >= 120, 'navigation or toolbars collapsed the image area'
            key(control(main,'下一页 →'),ord('8'))
            wait(lambda: 'P001-K01' in e.read(repo.folder(pid)/'images/index.json')['selections'],'candidate saved')
            assert e.read(repo.folder(pid)/'images/index.json')['selections']['P001-K01']['image']['id']=='image8'
            wide = any(caption(h)=='分镜目录' and user.IsWindowVisible(h) for h in handles(main))
            positive = control(main,'first-person POV, one cup')
            if wide:
                def four_columns():
                    nav, outline, canvas, prompt = map(bounds, (control(main,'创作首页'),control(main,'分镜目录'),
                        user.GetParent(control(main,'下一页 →')),positive))
                    return nav.right <= outline.left < canvas.left and canvas.right <= prompt.left and canvas.bottom-canvas.top > 400
                assert four_columns(), 'the four ultrawide columns overlapped'
                click(control(main,'查看分镜与对白'))
                dialogue = window('P001-K01 · 分镜与对白')
                user.PostMessageW(dialogue,16,0,0)
                wait(lambda:not user.IsWindow(dialogue),'close dialogue preview')
                user.ShowWindow(main,9)
                user.MoveWindow(main,20,20,1100,800,True)
                wait(lambda:not user.IsWindowVisible(control(main,'分镜目录')),'compact review')
                assert bounds(positive).top >= bounds(control(main,'下一页 →')).bottom
                assert caption(positive)=='first-person POV, one cup'
                user.ShowWindow(main,3)
                wait(four_columns,'restored ultrawide review')
                outline=next(h for h in handles(main) if '.listbox.' in kind(h) and user.IsWindowVisible(h))
                second=wt.RECT()
                assert user.SendMessageW(outline,0x198,1,ct.addressof(second))!=-1 and second.bottom>second.top  # LB_GETITEMRECT
                point=(second.top+3)<<16 | 5
                user.PostMessageW(outline,0x201,1,point); user.PostMessageW(outline,0x202,0,point)
                control(main,'2  /  6     ·     '+doc['panels'][1]['panel_id'])
                key(control(main,'下一页 →'),0x25); control(main,'✓ 8')
            click(control(main,'界面显示'))
            settings=window('界面显示'); click(control(settings,'大字号'))
            wait(lambda:(root/'data/native-editor/display.txt').exists(),'saved text size')
            control(main,'first-person POV, one cup'); control(main,'✓ 8')
            click(control(main,'创作首页')); click(control(main,'返回刚才的工作台'))
            control(main,'✓ 8')
            click(control(main,'创作首页'))
            saved=wait(lambda:next((h for h in handles(main) if '.button.' in kind(h)
                       and user.IsWindowVisible(h) and caption(h)==sample['brief']['title']),None),'saved comic on home')
            click(saved); control(main,'✓ 8')
            click(control(main,'打开作品')); picker=window('打开漫画作品')
            search=wait(lambda:next((h for h in handles(picker) if '.edit.' in kind(h)),None),'search by title')
            put(search,sample['brief']['title'])
            go=control(picker,'继续'); wait(lambda:user.IsWindowEnabled(go),'matching saved work'); click(go)
            wait(lambda:not user.IsWindow(picker),'reopened project'); control(main,'✓ 8')
            key(control(main,'下一页 →'),0x27); control(main,'画这一格')
            key(control(main,'下一页 →'),0x25); control(main,'重画这一格')
            key(control(main,'下一页 →'),0x28)
            dialog = window('P001-K01 · 问题说明')
            note = wait(lambda:next((h for h in handles(dialog) if '.edit.' in kind(h)),None),'problem editor')
            wait(lambda:focused(note),'problem focus')
            put(note,'画面只留一只杯子'); key(note,13)
            wait(lambda:problem_panels.state(repo,pid)['items'][0]['note']=='画面只留一只杯子','note saved')
            for n in range(2,7):
                control(main, str(n)+'  /  6     ·     '+doc['panels'][n-1]['panel_id'])
                key(control(main,'下一页 →' if n<6 else '问题汇总 →'),13)
            click(control(main,'按问题说明全部重画'))
            control(main,'重画这一格')
            assert drawn[-1]['action']=='understand_and_redraw'
            user.PostMessageW(main,16,0,0); assert process.wait(timeout=5)==0
            print('PASS: %dx%d at %d DPI, wide=%s; story input in %.3f s; layout, display size, resume, export, draw, review shortcuts and batch redraw.' %
                  (user.GetSystemMetrics(0),user.GetSystemMetrics(1),dpi,wide,elapsed))
        finally:
            if process.poll() is None:
                process.terminate(); process.wait(5)
            server.shutdown(); server.server_close(); app.night_queue.close()


if __name__=='__main__':
    run(Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'LocalDraw.exe')
