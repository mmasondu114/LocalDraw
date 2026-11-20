"""Verify model-folder switching against the running studio without submitting jobs."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    out = ROOT / 'data/test-results/lora-folders-live'
    out.mkdir(parents=True, exist_ok=True)
    cases, errors = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='chrome', headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('**/api/action', lambda route: route.fulfill(json={'ok': True}))
        page.route('**/api/workspace?*', lambda route: route.fulfill(json={'views': {}}))
        page.goto('http://127.0.0.1:8765', wait_until='networkidle')
        for entry in ['prompt', 'story']:
            page.evaluate('''async entry => {
                document.querySelector('#folder-qa')?.remove();
                const root=document.createElement('section');root.id='folder-qa';
                root.dataset.renderToken='qa';document.body.prepend(root);pid=null;
                localStorage.removeItem('comic-drawing-settings-new');
                if(entry==='prompt')await renderPromptStudio(root,null);
                else await renderLoraSettings(root,null,null);
            }''', entry)
            root = page.locator('#folder-qa')
            for model, folder, count in [('Anima-3.8B-v1.1.safetensors', 'anime', 117),
                                         ('NoobAI-XL-v1.1.safetensors', 'sdxl', 423),
                                         ('Anima-3.8B-v1.1.safetensors', 'anime', 117)]:
                root.get_by_role('combobox', name='绘图模型', exact=True).select_option(model)
                library = root.locator('.lora-library')
                assert library.get_attribute('data-lora-directory') == folder
                record = library.evaluate('''box=>({
                    total:box.querySelector('.lora-tools small').textContent,
                    folders:[...box.querySelector('[aria-label="LoRA 文件夹"]').options].map(o=>o.value),
                    visible:[...box.querySelectorAll('.lora-compact-row:not([hidden])')].map(r=>r.querySelector('details small').textContent),
                    enabled:[...box.querySelectorAll('.lora-compact-row')].filter(r=>!r.querySelector('input[type=checkbox]').disabled).map(r=>r.querySelector('details small').textContent)
                })''')
                assert record['total'].startswith(str(count)+' 个'), record
                assert all(f == '*' or f.startswith(folder+'/') or f == folder for f in record['folders']), record
                assert record['visible'] and all(f.startswith(folder+'/') for f in record['visible']), record
                assert record['enabled'] and all(f.startswith(folder+'/') for f in record['enabled']), record
                first = library.locator('.lora-compact-row:not([hidden]) input[type=checkbox]:enabled').first
                if first.count():
                    first.check()
                search = library.get_by_role('searchbox', name='搜索 LoRA')
                search.fill('Ganyu')
                visible = library.locator('.lora-compact-row:not([hidden])')
                assert visible.count() > 0, (entry, folder, 'English alias lost')
                search.fill('')
                cases.append({'entry': entry, 'checkpoint': model, 'folder': folder, 'count': count})
                library.screenshot(path=str(out / (entry+'-'+folder+'.png')))
        for model, folder, count in [('Anima-3.8B-v1.1.safetensors','anime',117),('NoobAI-XL-v1.1.safetensors','sdxl',423)]:
            response = page.request.get('http://127.0.0.1:8765/api/drawing-options', params={'checkpoint':model}).json()
            assert len(response['comfy']['loras']) == count
            assert all(f.startswith(folder+'/') for f in response['comfy']['loras'])
            assert len(response['lora_inspection']) == count
        assert not errors, errors
        browser.close()
    report = {'status':'passed','cases':cases,'browser_errors':errors,'actions_intercepted':True,'production_data_written':False}
    (out / 'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('Model folder switching passed: prompt + story + scoped API')


if __name__ == '__main__':
    main()
