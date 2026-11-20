"""Inspect a newly found public author-permitted LoRA using its normal page."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import urllib.parse
import urllib.request
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
MODEL_ID = '1818053850728661834'


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--download', action='store_true')
    args = parser.parse_args()
    report = {'model_id': MODEL_ID, 'page': 'https://pixai.art/en/model/' + MODEL_ID,
              'anonymous_browser': True, 'authorization_bypassed': False, 'download_completed': False,
              'models': [], 'versions': []}
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='chrome', headless=True)
        page = browser.new_page()

        def capture(response):
            if 'graphql' not in response.url or 'json' not in response.headers.get('content-type', ''):
                return
            try:
                data = response.json().get('data', {}) or {}
                model = data.get('generationModel')
                if isinstance(model, dict) and model.get('id') == MODEL_ID:
                    report['models'].append({key: model.get(key) for key in ('id', 'title', 'type', 'category', 'isDownloadable', 'loraBaseModelTypes', 'permittedUse', 'userPermission', 'refCount', 'refUserCount', 'reviewSummary')})
                edges = (data.get('generationModelVersions') or {}).get('edges', [])
                for edge in edges:
                    node = edge.get('node', {})
                    if node.get('modelId') == MODEL_ID:
                        version = {key: node.get(key) for key in ('id', 'name', 'fileUploadId', 'loraBaseModelType', 'loraBaseModelId', 'status')}
                        version['extra'] = {key: node.get('extra', {}).get(key) for key in ('triggerWords', 'permissions', 'isEarlyAccess')}
                        version['download_url_provided'] = bool(node.get('downloadUrl'))
                        report['versions'].append(version)
            except (ValueError, AttributeError):
                pass

        page.on('response', capture)
        page.goto(report['page'], wait_until='domcontentloaded', timeout=45000)
        page.wait_for_timeout(8000)
        # The primary record may arrive in the normal page's server-rendered
        # loader data, before browser GraphQL response listeners can see it.
        html = page.content()
        decoded_records = []
        for payload in re.findall(r'window\.__reactRouterContext\.streamController\.enqueue\(("(?:[^"\\]|\\.)*")\)', html):
            try:
                values = json.loads(json.loads(payload))
                if not isinstance(values, list):
                    continue

                def decode(index, seen=None):
                    if not isinstance(index, int) or index < 0 or index >= len(values):
                        return None
                    seen = seen or set()
                    if index in seen:
                        return None
                    value = values[index]
                    chain = seen | {index}
                    if isinstance(value, dict):
                        return {str(values[int(k[1:])]): decode(v, chain) for k, v in value.items() if k.startswith('_') and k[1:].isdigit()}
                    if isinstance(value, list):
                        return [decode(v, chain) for v in value]
                    return value

                for i, value in enumerate(values):
                    if isinstance(value, dict):
                        keys = {str(values[int(k[1:])]) for k in value if k.startswith('_') and k[1:].isdigit()}
                        if 'downloadUrl' in keys or ('isDownloadable' in keys and 'title' in keys):
                            decoded_records.append(decode(i))
            except (ValueError, TypeError, IndexError):
                continue
        report['server_rendered_metadata'] = []
        for record in decoded_records:
            if record.get('id') == MODEL_ID or record.get('modelId') == MODEL_ID:
                sanitized = {k: record.get(k) for k in ('id', 'modelId', 'title', 'name', 'isDownloadable', 'fileUploadId', 'loraBaseModelType', 'type', 'status')}
                sanitized['download_url_provided'] = bool(record.get('downloadUrl'))
                sanitized['extra'] = {k: (record.get('extra') or {}).get(k) for k in ('triggerWords', 'permissions')}
                sanitized['record_keys'] = list(record)
                sanitized['download_url_kind'] = type(record.get('downloadUrl')).__name__
                file_record = record.get('fileUploadRecord') or {}
                if isinstance(file_record, dict):
                    sanitized['file_record_keys'] = list(file_record)
                    sanitized['file_record'] = {k: file_record.get(k) for k in ('name', 'filename', 'fileName', 'originalName', 'size', 'sha256')}
                report['server_rendered_metadata'].append(sanitized)
        report['visible_page_text'] = page.locator('body').inner_text()[:12000]
        report['buttons'] = page.get_by_role('button').all_text_contents()
        report['button_metadata'] = page.get_by_role('button').evaluate_all("items => items.slice(0, 18).map(b => ({text: b.innerText, title: b.title, label: b.getAttribute('aria-label'), svg: b.querySelector('svg')?.getAttribute('class')}))")
        if args.download and page.get_by_role('button', name='More actions', exact=True).count():
            page.get_by_role('button', name='More actions', exact=True).click()
            page.wait_for_timeout(500)
            report['normal_download_menu'] = page.get_by_role('menuitem').all_text_contents()
            menu_download = page.get_by_role('menuitem').filter(has_text=re.compile('Download', re.IGNORECASE))
            if menu_download.count():
                try:
                    with page.expect_download(timeout=12000) as download_info:
                        menu_download.first.click()
                    incoming = download_info.value
                    original = incoming.suggested_filename
                    if original.endswith('.safetensors') and Path(original).name == original:
                        folder = ROOT / 'data/lora-curation/probes/pixai-vigil'
                        folder.mkdir(parents=True, exist_ok=True)
                        path = folder / original
                        incoming.save_as(path)
                        with path.open('rb') as stream:
                            sha = hashlib.file_digest(stream, 'sha256').hexdigest()
                        report['download_completed'] = True
                        report['downloaded_weight'] = {'path': path.relative_to(ROOT).as_posix(), 'original_name': original, 'sha256': sha, 'bytes': path.stat().st_size, 'catalog_checksum_available': False, 'normal_browser_download': True}
                except Exception as ex:
                    report['normal_download_control_result'] = {'exception_type': type(ex).__name__, 'visible_dialogs': page.get_by_role('dialog').all_text_contents()}
            page.keyboard.press('Escape')
        # Follow only an ordinary visible download control. A sign-in request
        # is a limitation, not a reason to bypass the site's access control.
        buttons = page.get_by_role('button').filter(has_text='Download')
        if buttons.count():
            buttons.first.click(timeout=10000)
            page.wait_for_timeout(3000)
            report['after_download_control_text'] = page.locator('body').inner_text()[-6000:]
        report['download_url_available_to_anonymous_browser'] = any(v['download_url_provided'] for v in report['versions'] + report['server_rendered_metadata'])
        if args.download and not report['download_completed']:
            permitted = any(m.get('id') == MODEL_ID and m.get('isDownloadable') is True for m in decoded_records)
            version = next((m for m in decoded_records if m.get('modelId') == MODEL_ID and m.get('downloadUrl')), None)
            if permitted and version:
                url = version['downloadUrl']
                report['download_attempt'] = {'version_id': version['id'], 'url_kind': type(url).__name__}
                if isinstance(url, str) and urllib.parse.urlsplit(url).scheme == 'https':
                    parsed = urllib.parse.urlsplit(url)
                    report['download_attempt']['public_host'] = parsed.hostname
                    report['download_attempt']['query_parameter_names'] = list(urllib.parse.parse_qs(parsed.query))
                    try:
                        request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': report['page']})
                        with urllib.request.urlopen(request, timeout=60) as response:
                            record = version.get('fileUploadRecord') or {}
                            name = next((record.get(k) for k in ('fileName', 'filename', 'originalName', 'name') if isinstance(record, dict) and record.get(k)), None)
                            disposition = response.headers.get('Content-Disposition', '')
                            filename_match = re.search(r"filename\*=UTF-8''([^;]+)|filename=\"?([^\";]+)", disposition, re.IGNORECASE)
                            if filename_match:
                                name = urllib.parse.unquote(filename_match.group(1) or filename_match.group(2))
                            # Keep author/download original filename exactly. A
                            # missing filename needs metadata, not a made-up name.
                            if not name or not str(name).endswith('.safetensors') or Path(str(name)).name != str(name):
                                report['download_attempt']['missing_original_filename'] = True
                            else:
                                folder = ROOT / 'data/lora-curation/probes/pixai-vigil'
                                folder.mkdir(parents=True, exist_ok=True)
                                path = folder / str(name)
                                temporary = path.with_suffix('.safetensors.partial')
                                expected = int(response.headers.get('Content-Length', 0))
                                h = hashlib.sha256()
                                size = 0
                                with temporary.open('wb') as output:
                                    while chunk := response.read(8*1024*1024):
                                        output.write(chunk)
                                        h.update(chunk)
                                        size += len(chunk)
                                if expected and size != expected:
                                    report['download_attempt']['incomplete_bytes'] = size
                                else:
                                    temporary.replace(path)
                                    report['download_completed'] = True
                                    report['downloaded_weight'] = {'path': path.relative_to(ROOT).as_posix(), 'original_name': str(name), 'sha256': h.hexdigest(), 'bytes': size, 'version_id': version['id'], 'catalog_checksum_available': False}
                    except Exception as ex:
                        report['download_attempt']['error_type'] = type(ex).__name__
                        report['download_attempt']['http_status'] = getattr(ex, 'code', None)
                        if isinstance(ex, urllib.error.HTTPError):
                            report['download_attempt']['response_content_type'] = ex.headers.get('Content-Type')
                            body = ex.read(3000).decode('utf-8', errors='replace')
                            code = re.search(r'<Code>([^<]+)</Code>', body)
                            if code:
                                report['download_attempt']['storage_error_code'] = code.group(1)
        browser.close()
    out = ROOT / 'data/lora-curation/research/pixai-vigil-public-download.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'page': report['page'], 'matching_model_metadata': len(report['models']),
                      'matching_versions': len(report['versions']), 'download_url_available': report['download_url_available_to_anonymous_browser'],
                      'server_rendered_records': report['server_rendered_metadata'],
                      'download_completed': report['download_completed'], 'download_attempt': report.get('download_attempt'),
                      'downloaded_weight': report.get('downloaded_weight'), 'report': str(out)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
