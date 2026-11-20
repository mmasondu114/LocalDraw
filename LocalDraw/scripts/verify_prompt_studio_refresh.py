import json
from pathlib import Path
from playwright.sync_api import sync_playwright
out=Path('data/test-results/prompt-studio-refresh');out.mkdir(parents=True,exist_ok=True)
with sync_playwright() as p:
 browser=p.chromium.launch(channel='chrome',headless=True)
 page=browser.new_page(viewport={'width':1366,'height':768})
 errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
 page.add_init_script("localStorage.setItem('studio-last-view','new')")
 page.goto('http://127.0.0.1:8765');page.locator('#prompt-entry').click()
 page.locator('.prompt-top-actions').wait_for()
 page.wait_for_function('!restoringWorkspace')
 clear=page.get_by_role('button',name='一键清除角色、LoRA 和正负提示词',exact=True)
 for width,height in [(1366,768),(1920,1080),(1024,768),(390,844)]:
  page.set_viewport_size({'width':width,'height':height})
  page.wait_for_timeout(300)
  box=clear.bounding_box(); assert box['y']>=0 and box['y']+box['height']<height,(width,box)
  if width==1920:page.screenshot(path=str(out/'desktop.png'))
 page.set_viewport_size({'width':1366,'height':768})
 positive=page.locator('[data-edit-key="prompt-positive"]');negative=page.locator('[data-edit-key="prompt-negative"]')
 positive.fill('A blue mountain');negative.fill('text')
 selected=page.locator('[data-edit-key^="prompt-character-use-"]').first
 selected.check()
 assert page.locator('[data-edit-key^="prompt-character-use-"]:checked').count()==1
 clear.click()
 assert positive.input_value()==negative.input_value()==''
 assert page.locator('[data-edit-key^="prompt-character-use-"]:checked').count()==0
 assert page.locator('[data-edit-key^="prompt-lora-use-"]:checked').count()==0
 model=page.get_by_role('combobox',name='绘图模型',exact=True)
 for name in ['anima-aesthetic-v1.1.safetensors','animaDeltaMix38BQwen0_deltaMixV60.safetensors','krea2_turbo_fp8_scaled.safetensors']:
  model.select_option(name)
  assert model.input_value()==name
  assert negative.is_disabled()==name.startswith('krea')
  directory=page.locator('[data-lora-directory]').get_attribute('data-lora-directory')
  assert directory==('krea2' if name.startswith('krea') else 'anime')
 model.select_option('anima-aesthetic-v1.1.safetensors')
 page.locator('#manage-projects').click()
 page.get_by_role('button',name='全选当前结果',exact=True).click()
 count=page.locator('.project-manager-row input:checked').count();assert count>1
 page.get_by_role('button',name='删除所选',exact=True).click()
 assert page.get_by_role('button',name='确认删除所选作品',exact=True).is_visible()
 page.screenshot(path=str(out/'management.png'))
 # Inspect only: do not delete any real user work.
 assert not errors,errors
 print(json.dumps({'errors':errors,'works':count,'viewports':'4 passed','clear':'passed','models':'3 passed'},ensure_ascii=False))
 browser.close()
