"""Inspect the newly located Ash author page through normal anonymous browsing."""
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PAGE = 'https://tensor.art/models/766242201899420392/AshR6sXArknights-AshR6sXarknights'


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    report = {'page': PAGE, 'character': '灰烬', 'author': 'Sakura Chiyono',
              'anonymous_browser': True, 'authorization_bypassed': False,
              'weight_downloaded': False, 'public_response_metadata': []}
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='chrome', headless=True)
        page = browser.new_page()

        def capture(response):
            if 'json' not in response.headers.get('content-type', ''):
                return
            parsed = urlsplit(response.url)
            if 'tensor' not in parsed.hostname:
                return
            try:
                data = response.json()
            except ValueError:
                return
            safe_keys = {'id', 'name', 'title', 'modelId', 'versionId', 'baseModel',
                         'baseModelType', 'sha256', 'fileName', 'filename', 'fileSize',
                         'size', 'allowDownload', 'downloadable', 'canDownload'}
            records = []

            def walk(value):
                if isinstance(value, dict):
                    fields = {key: val for key, val in value.items()
                              if key in safe_keys and isinstance(val, (str, int, float, bool))
                              and not (isinstance(val, str) and ('://' in val or len(val) > 400))}
                    if any(key in fields for key in ('sha256', 'fileName', 'filename',
                                                    'allowDownload', 'downloadable', 'canDownload')):
                        records.append(fields)
                    for item in value.values():
                        walk(item)
                elif isinstance(value, list):
                    for item in value:
                        walk(item)
            walk(data)
            if records:
                report['public_response_metadata'].append({'host': parsed.hostname,
                                                          'path': parsed.path, 'records': records[:30]})
        page.on('response', capture)
        try:
            response = page.goto(PAGE, wait_until='domcontentloaded', timeout=45000)
            report['page_status'] = response.status if response else None
            page.wait_for_timeout(6000)
            text = page.locator('body').inner_text()
            report['visible_page_text'] = text[:9000]
            report['page_title'] = page.title()
            report['buttons'] = page.get_by_role('button').all_text_contents()
            report['download_links'] = page.get_by_role('link').filter(
                has_text=re.compile(r'^\s*(Download|下载)\s*$', re.I)).all_text_contents()
            report['site_verification_required'] = bool(re.search(
                r'Verify you are human|Checking your browser|Just a moment|安全验证|验证您',
                report['page_title'] + ' ' + text, re.I))
            report['normal_download_button_present'] = bool(page.get_by_role('button').filter(
                has_text=re.compile(r'^\s*(Download|下载)\s*$', re.I)).count())
        except Exception as exc:
            report['error_type'] = type(exc).__name__
        browser.close()
    path = ROOT / 'data/lora-curation/research/tensor-ash-public-download.json'
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: report.get(key) for key in ('page_status', 'page_title',
        'site_verification_required', 'normal_download_button_present', 'weight_downloaded')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
