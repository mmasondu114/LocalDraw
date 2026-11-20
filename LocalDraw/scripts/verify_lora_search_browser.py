"""Real Chrome UI checks against an isolated fixture; never loads a model."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
out=ROOT/'data/test-results/lora-search-browser';out.mkdir(parents=True,exist_ok=True)
server=subprocess.Popen([sys.executable,str(ROOT/'scripts/test_prompt_ui.py'),'--port','8789','--data-root',str(out),'--lora-search'],cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),env={**os.environ,'PYTHONUTF8':'1'})
try:
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='chrome',headless=True)
        page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];page.on('pageerror',lambda ex:errors.append(str(ex)))
        for _ in range(40):
            try:page.goto('http://127.0.0.1:8789');break
            except Exception:time.sleep(.25)
        page.locator('#prompt-entry').click()
        search=page.get_by_role('searchbox',name='搜索 LoRA',exact=True)
        search.wait_for();results=[]
        for query,expected in [('雷电',1),('leidianjiangjun',1),('lei dian',1),('Raiden',1),('BAAL',1),('mlynar',1),('random',0),('pony',0),('artist123',0),('opaque',0),('原神',0)]:
            search.fill(query);page.wait_for_timeout(60)
            count=page.locator('.lora-compact-row:visible').count();assert count==expected,(query,count,expected);results.append({'query':query,'matches':count})
        search.fill('雷电');row=page.locator('.lora-compact-row:visible');row.locator('input[type=checkbox]').first.check()
        file=page.evaluate("window.loraDisplayName('原神/雷电将军_random_pony_artist123.safetensors')")
        assert file=='雷电将军'
        page.screenshot(path=str(out/'search.png'),full_page=True)
        assert not errors,errors
        (out/'result.json').write_text(json.dumps({'status':'passed','queries':results,'browser_errors':errors},ensure_ascii=False,indent=2),encoding='utf-8')
        browser.close();print('中文、拼音、英文检索及原始文件名排除验证通过',flush=True)
finally:
    server.terminate();server.wait(timeout=10)
