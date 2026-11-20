"""Read-only UI regression on the real library; all writes are intercepted."""
import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    out = ROOT / "data/test-results/ui-refresh"
    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context(viewport={"width": 1920, "height": 1080},
                                      permissions=["clipboard-read", "clipboard-write"])
        page = context.new_page()
        workspace = {"stage": "prompt", "views": {}, "updated": 1}
        actions, errors, submissions, inventory_requests = [], [], [], []

        def action(route):
            body = route.request.post_data_json
            actions.append(body["action"])
            if body["action"] == "create_quick_sample":
                submissions.append(body)
                route.fulfill(status=400, json={"error": "请刷新本地界面后操作" if len(submissions) == 1 else "Test submission captured"})
                return
            if body["action"] == "save_workspace":
                workspace.clear()
                workspace.update(body["state"])
            route.fulfill(json={"ok": True})

        page.route("**/api/action", action)
        page.route("**/api/workspace?*", lambda r: r.fulfill(json=workspace))
        page.add_init_script("localStorage.setItem('studio-last-view', 'new')")
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: inventory_requests.append(request.url)
                if "/api/drawing-options" in request.url else None)
        start = time.perf_counter()
        page.goto("http://127.0.0.1:8765")
        page.locator(".prompt-start").wait_for()
        page.wait_for_function("!restoringWorkspace")
        load_seconds = round(time.perf_counter() - start, 3)
        assert "墨格" not in page.title()
        assert not page.locator(".prompt-preview").evaluate("e => e.open")
        option_count = page.locator("option").count()
        lora_count = page.locator(".lora-compact-row").count()
        assert option_count < 10000, option_count
        positive = page.locator('[data-edit-key="prompt-positive"]')
        negative = page.locator('[data-edit-key="prompt-negative"]')
        positive.fill("adult character, blue coat, city street")
        negative.fill("text, watermark")
        for name, expected in [("正向提示词", positive.input_value()), ("负面提示词", negative.input_value())]:
            page.get_by_role("button", name="复制" + name, exact=True).click()
            assert page.evaluate("navigator.clipboard.readText()") == expected

        # Exercise lazy dropdown population and automatic character/LoRA binding.
        owner = page.locator(".lora-compact-row:visible > select").first
        current = owner.input_value()
        assert owner.locator("option").count() <= 2
        owner.focus()
        assert owner.locator("option").count() > 2
        assert owner.input_value() == current
        character = page.locator(f'[data-edit-key="prompt-character-use-{current}"]')
        assert character.count() == 1
        name = character.evaluate("e => e.parentElement.textContent.split(' · ')[0].trim()")
        page.get_by_role("searchbox", name="搜索角色", exact=True).fill(name)
        character.check()
        assert page.locator('[data-edit-key^="prompt-lora-use-"]:checked').count() > 0
        before_requests = len(inventory_requests)
        page.get_by_role("button", name="开始画图", exact=True).click()
        page.get_by_text("Test submission captured", exact=True).wait_for()
        assert len(submissions) == 2  # Session renewal retries only rejected writes.
        assert submissions[-1]["characters"][0]["name"] == name
        assert submissions[-1]["loras"]
        assert submissions[-1]["text"] == positive.input_value()
        assert submissions[-1]["negative"] == negative.input_value()
        assert len(inventory_requests) == before_requests
        page.get_by_role("button", name="一键清除", exact=True).click()
        assert positive.input_value() == negative.input_value() == ""
        assert page.locator('[data-edit-key^="prompt-character-use-"]:checked').count() == 0
        assert page.locator('[data-edit-key^="prompt-lora-use-"]:checked').count() == 0
        assert page.locator(".prompt-preview textarea").input_value() == ""
        page.reload()
        page.locator(".prompt-start").wait_for()
        page.wait_for_function("!restoringWorkspace")
        assert positive.input_value() == negative.input_value() == ""
        assert page.locator('[data-edit-key^="prompt-character-use-"]:checked').count() == 0
        assert page.locator('[data-edit-key^="prompt-lora-use-"]:checked').count() == 0
        for width, height, filename in [(1920, 1080, "desktop.png"), (1366, 768, "laptop.png"), (390, 844, "mobile.png")]:
            page.set_viewport_size({"width": width, "height": height})
            page.wait_for_timeout(250)
            assert positive.bounding_box()["height"] >= 200
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path=str(out / filename), full_page=True)
        assert not errors, errors
        assert set(actions) <= {"save_workspace", "create_quick_sample"}, actions
        report = {"load_seconds": load_seconds, "loras": lora_count, "options": option_count,
                  "copy": "passed", "clear_and_reload": "passed", "lazy_owners": "passed",
                  "layouts": "passed", "submit_and_session_retry": "passed",
                  "errors": errors, "production_writes": False}
        (out / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report), flush=True)
        browser.close()


if __name__ == "__main__":
    main()
