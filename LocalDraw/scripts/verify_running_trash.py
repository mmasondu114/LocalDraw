"""Exercise deleting a live work using an isolated fixture and real browser UI."""
import sys,threading,tempfile,time
from pathlib import Path
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from playwright.sync_api import sync_playwright
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from comic_workflow.server import Application,make_handler
from comic_workflow.repository import Repository
from comic_workflow import engine as e
with tempfile.TemporaryDirectory() as tmp:
 root=Path(tmp)
 with patch('comic_workflow.jobs.DATA',root):app=Application(Repository(root/'projects'))
 pid=app.repo.create(None,True);folder=app.repo.folder(pid)
 started,proceed,written,finish=(threading.Event() for _ in range(4))
 def worker(update,jid):
  e.save(folder/'images/index.json',{'batches':[{'status':'running','id':'fixture'}]})
  started.set()
  assert proceed.wait(20)
  e.save(app.repo.folder(pid)/'runs/progress.json',{'completed':1})
  update({'message':'已完成第一张，任务继续运行','progress':{'completed':1,'total':2,'unit':'张'}})
  written.set();assert finish.wait(20)
  return {'completed':2}
 jid=app.jobs.submit('运行中的测试作品',worker,pid)
 http=ThreadingHTTPServer(('127.0.0.1',0),make_handler(app));threading.Thread(target=http.serve_forever,daemon=True).start()
 try:
  assert started.wait(2)
  with sync_playwright() as p:
   browser=p.chromium.launch(channel='chrome',headless=True);page=browser.new_page(viewport={'width':1366,'height':900})
   errors=[];page.on('pageerror',lambda x:errors.append(str(x)))
   page.add_init_script("localStorage.setItem('studio-last-view','new')")
   page.goto(f'http://127.0.0.1:{http.server_port}')
   page.locator('#manage-projects').click();page.get_by_role('button',name='全选当前结果',exact=True).click()
   page.get_by_role('button',name='删除所选',exact=True).click();page.get_by_role('button',name='确认删除所选作品',exact=True).click()
   page.locator("#dialog").wait_for(state="hidden")
   assert app.repo.listing()==[] and app.jobs.get(jid)['status']=='running'
   proceed.set();assert written.wait(3)
   page.locator('#project-trash').click()
   job=page.locator('#dialog-body [data-job-id="'+jid+'"]')
   assert job.locator('.task-state').inner_text()=='正在进行'
   assert '已完成第一张' in job.inner_text()
   page.get_by_role('button',name='刷新任务状态',exact=True).click()
   assert page.get_by_role('button',name='恢复作品',exact=True).is_visible()
   page.get_by_role('button',name='恢复作品',exact=True).click()
   page.locator("#dialog").wait_for(state="hidden")
   assert app.repo.listing()[0]['id']==pid
   assert app.jobs.get(jid)['status']=='running'
   assert e.read(folder/'runs/progress.json')['completed']==1
   assert not errors,errors
   browser.close();print('PASS: delete running work, preserve progress, inspect trash, restore while running')
 finally:
  proceed.set();finish.set()
  for _ in range(200):
   if jid not in app.jobs.controls:break
   time.sleep(.01)
  http.shutdown();http.server_close();app.repo._temporary.cleanup()
