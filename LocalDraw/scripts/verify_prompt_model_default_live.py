"""Read-only browser check: configured defaults and explicit saved choices.

All action requests are intercepted; synthetic cards do not enter the library.
"""
import json
from pathlib import Path
import sys
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    out = ROOT / 'data/test-results/prompt-model-default-live'
    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='chrome', headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        page.route('**/api/action', lambda route: route.fulfill(json={'ok': True}))
        page.route('**/api/workspace?*', lambda route: route.fulfill(json={'views': {}}))
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto('http://127.0.0.1:8765', wait_until='networkidle')
        records = []
        configured = json.loads((ROOT / 'config/comfyui.json').read_text(encoding='utf-8'))['checkpoint']
        for saved, expected in [(None, configured),
                                ('NoobAI-XL-v1.1.safetensors', 'NoobAI-XL-v1.1.safetensors'),
                                ('missing-model.safetensors', configured)]:
            page.evaluate('''async saved => {
                document.querySelector('#model-default-qa')?.remove();
                const root = document.createElement('section'); root.id='model-default-qa';
                root.dataset.renderToken='qa'; document.body.append(root);
                pid = null;
                const project = saved ? {quick_sample:true,prompt_input:{checkpoint:saved,characters:[]}} : null;
                await window.renderPromptStudio(root, project);
            }''', saved)
            root = page.locator('#model-default-qa')
            selected = root.get_by_role('combobox', name='绘图模型', exact=True).input_value()
            assert selected == expected, (saved, selected, expected)
            hand = root.locator('[data-edit-key="prompt-hand-detail"]')
            assert not hand.is_checked()
            if selected == 'Anima-3.8B-v1.1.safetensors':
                assert hand.is_disabled()
                assert root.locator('[data-edit-key="prompt-regional-characters"]').is_disabled()
            records.append({'saved_checkpoint': saved, 'selected_checkpoint': selected, 'hand_detail_checked': False})
        assert not errors, errors
        root.screenshot(path=str(out / 'configured-default.png'))
        result = {'status': 'passed', 'cases': records, 'browser_errors': errors,
                  'server_actions_intercepted': True, 'production_data_written': False,
                  'scope': 'Actual studio assets and API inventory; synthetic unsaved prompt forms verify new defaults, explicit saved choice, unavailable saved-choice fallback.'}
        (out / 'report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        browser.close()
        print('真实工作室界面默认模型、已保存模型和失效模型回退选择通过验证', flush=True)


if __name__ == '__main__':
    main()
