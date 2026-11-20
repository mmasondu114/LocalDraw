"""Check the actual studio picker after adopting the audited Lessing payload."""
import json
from pathlib import Path
import sys
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    out = ROOT / 'data/test-results/lora-search-live'
    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='chrome', headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        page.route('**/api/action', lambda route: route.fulfill(json={'ok': True}))
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto('http://127.0.0.1:8765', wait_until='networkidle')
        page.locator('#prompt-entry').click()
        page.get_by_role('heading', name='直接输入提示词画图', exact=True).wait_for()
        search = page.get_by_role('searchbox', name='搜索 LoRA', exact=True)
        search.wait_for()
        page.wait_for_timeout(2500)
        search.fill('')
        page.wait_for_timeout(250)
        inventory = int(page.locator('.lora-library .lora-tools small').first.inner_text().split()[0])
        queries = []
        for query, expected in [('止颂', 1), ('zhi song', 1), ('zhisong', 1), ('Lessing', 1),
                                ('000009', 0), ('arknight_all_v2', 0), ('nochekaiser', 0)]:
            search.fill(query)
            page.wait_for_timeout(250)
            total = page.locator('.lora-library .lora-tools small').first.inner_text()
            count = int(total.split()[0])
            assert count == expected, (query, count, expected)
            queries.append({'query': query, 'total_matches': count})
        search.fill('止颂')
        page.wait_for_timeout(250)
        row = page.locator('.lora-compact-row:visible').first
        full_path = row.locator('details small').first.text_content().replace('\\', '/')
        assert full_path == '明日方舟/止颂_Lessing-000009.safetensors', full_path
        page.screenshot(path=str(out / 'lessing-current-library.png'), full_page=True)
        assert not errors, errors
        result = {'status': 'passed', 'inventory': inventory, 'queries': queries,
                  'browser_errors': errors, 'pagination_aware': True,
                  'entry': 'actual_prompt_drawing_studio_after_initial_load',
                  'full_file_path_retained': '明日方舟/止颂_Lessing-000009.safetensors'}
        (out / 'lessing-current-library.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        browser.close()
        print('止颂中文、拼音、英文搜索和完整文件路径在真实工作室中通过验证', flush=True)


if __name__ == '__main__':
    main()
