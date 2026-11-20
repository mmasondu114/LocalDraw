"""Browser regression checks for startup; isolated data, no model execution.

Run with .venv/Scripts/python.exe scripts/test_startup_ui.py.
"""
import json
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import sync_playwright
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from comic_workflow import engine
from comic_workflow.repository import Repository
from comic_workflow.server import Application, make_handler


def no_inference(*args, **kwargs):
    raise AssertionError("Startup tests must never run a model")


with tempfile.TemporaryDirectory(prefix="studio-startup-") as folder:
    root = Path(folder)
    repo = Repository(root / "projects")
    project = repo.create(None, True)
    batches=[]
    for pg in repo.get(project)['pages'].values():
        for panel in pg['panels']:
            file='fixture/'+panel['id']+'.png'
            path=repo.folder(project)/'images'/file
            path.parent.mkdir(parents=True,exist_ok=True)
            Image.new('RGB',(16,16),'blue').save(path)
            batches.append({'id':panel['id'],'panel_id':panel['id'], 'panel_hash':engine.digest(panel),
                'status':'done','checkpoint':'fixture','render_options':{},
                'images':[{'id':panel['id']+'-test','file':file,'review':{'verdict':'unreviewed'}}]})
    engine.save(repo.folder(project)/'images/index.json',{'batches':batches,'selections':{}})
    workspace = {"stage": "page", "target": "P001", "updated": 1, "views": {}}
    engine.save(repo.folder(project) / "workspace.json", workspace)
    with patch("comic_workflow.jobs.DATA", root):
        app = Application(repo)
    app.prepare_language = app.model.start = app.comfy.start = no_inference
    app.jobs.on_pause = None
    app.model.status = lambda: {
        "ready": False, "owned": False, "generating": False, "file_size_ok": True,
    }
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page(viewport={"width": 1366, "height": 768})
            errors, requests, pending_boot, pending_status = [], [], [], []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("request", lambda request: requests.append(request.url.removeprefix(base)))
            page.route("**/api/bootstrap", lambda route: pending_boot.append(route))
            page.route("**/api/status", lambda route: pending_status.append(route))
            page.add_init_script("""window.startupFlashes = [];
                addEventListener('DOMContentLoaded', () => {
                  const check = () => {
                    if (!document.querySelector('.home-choices') &&
                        getComputedStyle(document.querySelector('.shell')).display !== 'none')
                      startupFlashes.push('editor visible before home');
                  };
                  new MutationObserver(check).observe(document.body,
                    {attributes:true, childList:true, subtree:true});
                  check();
                });""")
            page.goto(base)
            page.get_by_role("heading", name="今天，想画点什么？").wait_for()
            assert not page.locator(".shell").is_visible(), "Editor flashed while bootstrap was pending"
            assert len(pending_boot) == 1
            pending_boot.pop().continue_()
            page.unroute("**/api/bootstrap")
            page.locator(".home-choices").wait_for()
            assert requests.count("/api/bootstrap") == 1, requests
            assert not any(path.startswith(("/api/project?", "/api/workspace?", "/api/action", "/drawing.js")) for path in requests), requests
            assert page.evaluate("startupFlashes") == []
            assert engine.read(repo.folder(project) / "workspace.json") == workspace
            assert page.locator(".home-choice button").count() == 3
            assert not page.locator(".shell").is_visible()
            for route in list(pending_status):
                route.continue_()
            page.unroute("**/api/status")

            # Deferred restoration still opens the saved editor when requested.
            page.get_by_role("button", name="返回刚才的工作台", exact=True).click()
            page.locator("body[data-stage='page'] .shell").wait_for()
            page.wait_for_function("() => !restoringWorkspace")
            assert requests.count(f"/api/project?id={project}") == 1
            assert not any(path == "/drawing.js" for path in requests)

            # Prompt entry and returning home must not pull in comic font data.
            page.locator("#home-entry").click()
            page.get_by_role("button", name="开始画一张图", exact=True).click()
            positive = page.locator('[data-edit-key="prompt-positive"]')
            positive.wait_for()
            positive.fill("Startup regression draft")
            page.locator("#home-entry").click()
            page.get_by_role("button", name="返回刚才的工作台", exact=True).click()
            assert positive.input_value() == "Startup regression draft"
            assert not any(path == "/drawing.js" for path in requests)

            # Drawing tools are shared and retryable, not fetched on every visit.
            page.route("**/drawing.js", lambda route: route.abort())
            first = page.evaluate("Promise.allSettled([ensureDrawingTools(), ensureDrawingTools()]).then(r=>r.map(x=>x.status))")
            assert first == ["rejected", "rejected"]
            assert requests.count("/drawing.js") == 1
            page.unroute("**/drawing.js")
            page.evaluate("Promise.all([ensureDrawingTools(), ensureDrawingTools()])")
            page.evaluate("ensureDrawingTools()")
            assert requests.count("/drawing.js") == 2
            assert page.evaluate("typeof renderComfy === 'function' && typeof renderLoraSettings === 'function'")

            page.locator("#home-entry").click()
            page.get_by_role("button", name="开始一个新故事", exact=True).click()
            page.wait_for_function("() => document.body.dataset.stage === 'script' && !restoringWorkspace")
            assert page.locator(".shell").is_visible()
            assert requests.count("/drawing.js") == 2
            assert requests.count("/api/characters") == 1, requests
            assert requests.count("/api/drawing-options") == 1, requests
            assert requests.count("/api/bootstrap") == 1, requests
            page.locator("#home-entry").click()
            page.get_by_role("button", name="安排今晚的任务", exact=True).click()
            page.locator("#studio-queue-dialog[open]").wait_for()
            page.locator("#studio-queue-dialog").get_by_role("button", name="关闭", exact=True).click()
            out = ROOT / "data/test-results/startup-ui"
            out.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(out / "home.png"))

            # Exercise the real picker -> save -> resume HTTP path, holding the
            # save response to reproduce a fast click on Continue. No GPU work.
            worker_counts = []
            def paused_worker(application, pid, request, update, jid):
                from comic_workflow.task_control import checkpoint
                worker_counts.append(request['candidate_count'])
                application.jobs.control(jid, 'pause')
                checkpoint()
                return {}

            app.jobs.save('quantity-ui', {'id':'quantity-ui', 'project':project,
                'label':'数量回归测试', 'created':'9999', 'status':'paused',
                'request':{'action':'draw_imported', 'run_key':'quantity-ui',
                           'targets':['P001-K01','P001-K02'], 'candidate_count':8}})
            page.get_by_role("button", name="继续", exact=True).click()
            page.locator("#stages button").filter(has_text="导出分镜与绘图").click()
            page.get_by_role("button", name="高级绘图工具", exact=True).click()
            picker = page.locator('[data-edit-key="literal-candidate-count-v2"]')
            picker.wait_for()
            page.wait_for_function("() => !restoringWorkspace && window.activeStudioJob?.id==='quantity-ui'")
            assert picker.input_value() == '8'
            assert sum(path.startswith('/api/drawing?') for path in requests)==1
            assert not any(path.startswith(('/api/layout-presets?', '/api/review-statistics?')) for path in requests)
            assert page.locator('.panel-choice .image-option').count() == 1
            page.get_by_role('combobox', name='P001 · 选择画格', exact=True).select_option('P001-K02')
            candidate=page.locator('#choice-P001-K02 .image-option')
            candidate.wait_for()
            candidate.locator('.choose-image').click()
            page.wait_for_function("() => !document.querySelector('#choice-P001-K02').open")
            assert 'P001-K02' in engine.read(repo.folder(project)/'images/index.json')['selections']
            layout_box=page.locator('.drawing-page:not([hidden]) .drawing-layout')
            layout_box.locator(':scope > summary').click()
            with page.expect_response(lambda response: '/api/layout-presets?' in response.url) as layout_response:
                layout_box.locator('select').select_option('balanced')
            expected=layout_response.value.json()['balanced']
            page.wait_for_function("expected => JSON.stringify(JSON.parse(document.querySelector('.drawing-page:not([hidden]) [data-edit-key^=layout]').value))===JSON.stringify(expected)",arg=expected)
            picker.evaluate("el=>el.dataset.testMarker='keep-mounted'")
            saved_routes = []
            def hold_quantity(route):
                if route.request.post_data_json.get('action') == 'set_remaining_candidate_count':
                    saved_routes.append(route)
                else:
                    route.continue_()
            page.route('**/api/action', hold_quantity)
            drawing_reads = sum(path.startswith('/api/drawing?') for path in requests)
            with patch('comic_workflow.prompt_exchange.draw_imported', side_effect=paused_worker):
                picker.select_option('4')
                page.get_by_role('button', name='继续任务', exact=True).click()
                assert worker_counts == []
                assert app.jobs.get('quantity-ui')['status'] == 'paused'
                assert len(saved_routes) == 1
                saved_routes.pop().continue_()
                page.unroute('**/api/action')
                page.wait_for_function("() => window.activeStudioJob?.id!=='quantity-ui' && window.activeStudioJob?.status==='paused'")
                assert worker_counts == [4], worker_counts
                assert picker.input_value() == '4'
                assert picker.get_attribute('data-test-marker') == 'keep-mounted'
                assert sum(path.startswith('/api/drawing?') for path in requests) == drawing_reads
                active = next(job for job in app.jobs.listing() if job['status']=='paused')
                assert active['request']['candidate_count'] == 4
                app.jobs.control(active['id'], 'cancel', confirm=True)
                page.wait_for_function("() => !window.activeStudioJob")

            # A failed bootstrap offers recovery without revealing the editor.
            retry = browser.new_page()
            retry.on("pageerror", lambda error: errors.append(str(error)))
            retry.route("**/api/bootstrap", lambda route: route.fulfill(status=503, json={"error": "测试连接中断"}))
            retry.goto(base)
            retry.get_by_role("button", name="重新连接", exact=True).wait_for()
            assert not retry.locator(".shell").is_visible()
            retry.unroute("**/api/bootstrap")
            retry.get_by_role("button", name="重新连接", exact=True).click()
            retry.locator(".home-choices").wait_for()
            retry.route("**/drawing.js", lambda route: route.abort())
            retry.get_by_role("button", name="开始一个新故事", exact=True).click()
            retry.locator("#notice").filter(has_text="绘图工具加载失败").wait_for()
            assert not retry.locator(".shell").is_visible()
            retry.unroute("**/drawing.js")
            retry.get_by_role("button", name="返回刚才的工作台", exact=True).click()
            retry.wait_for_function("() => document.body.dataset.stage === 'script' && !restoringWorkspace")
            assert retry.locator(".shell").is_visible()
            assert not errors, errors
            print(json.dumps({"result": "passed", "checks": [
                "no editor flash during delayed bootstrap", "one bootstrap request",
                "home independent of status probes", "no startup draft writes",
                "lazy workspace restore", "prompt draft retained", "lazy drawing retry/deduplication",
                "new story and queue navigation", "bootstrap recovery", "failed editor load recovery",
                "shared catalogs fetched once", "8-to-4 save before resume", "pause does not remount drawing editor",
                "lazy panel image selection", "layout presets loaded on demand",
            ], "page_errors": errors}, ensure_ascii=False))
            browser.close()
    finally:
        app.night_queue.close()
        server.shutdown()
        server.server_close()
