import json,time
from pathlib import Path
from playwright.sync_api import sync_playwright
out=Path('data/test-results/prompt-studio-refresh')
with sync_playwright() as p:
 browser=p.chromium.launch(channel='chrome',headless=True)
 page=browser.new_page(viewport={'width':1366,'height':768});errors=[]
 page.on('pageerror',lambda e:errors.append(str(e)))
 page.add_init_script("localStorage.setItem('studio-last-view','new')")
 page.goto('http://127.0.0.1:8765');page.locator('#prompt-entry').click()
 page.locator('.prompt-top-actions').wait_for();page.wait_for_function('!restoringWorkspace')
 before=page.request.get('http://127.0.0.1:8765/api/bootstrap').json()
 page.locator('[data-edit-key="prompt-positive"]').fill('A quiet mountain lake, pine forest, warm morning sunlight, illustration')
 page.get_by_role('combobox',name='绘图模型',exact=True).select_option('krea2_turbo_fp8_scaled.safetensors')
 page.get_by_role('combobox',name='画面比例',exact=True).select_option('1:1')
 page.get_by_role('button',name='开始画图',exact=True).click()
 try:
  page.locator('.gallery-image img').wait_for(timeout=240000)
  page.wait_for_function('document.querySelector(".gallery-image img")?.naturalWidth > 0')
  page.screenshot(path=str(out/'live-result.png'))
  current=page.evaluate('pid');assert current.startswith('tmp-')
  after=page.request.get('http://127.0.0.1:8765/api/bootstrap').json()
  assert len(before['projects'])==len(after['projects'])
  assert page.evaluate('Object.values(localStorage).some(v => v.includes("A quiet mountain lake"))') is False
  assert not errors,errors
  page.reload();page.wait_for_load_state('networkidle',timeout=10000)
  deadline=time.monotonic()+15
  while time.monotonic()<deadline:
   response=page.request.get('http://127.0.0.1:8765/api/project?id='+current)
   if response.status!=200:break
   time.sleep(.5)
  assert response.status!=200,'session retained after reload'
  print(json.dumps({'end_to_end':'passed','project_count':len(after['projects']),'temporary_session_released':True,'errors':errors}))
 finally:
  browser.close()
