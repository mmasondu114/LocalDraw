"""Read-only browser verification of the imported library and LoRA sorting."""
import json
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/character-import/verification"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1680, "height": 1080})
        page.set_default_timeout(30000)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        # The live UI can autosave. Block every action so user drafts, bindings,
        # jobs and model processes remain untouched by this verification.
        page.route("**/api/action", lambda route: route.fulfill(status=200, content_type="application/json", body="{}"))
        page.goto("http://127.0.0.1:8765", wait_until="domcontentloaded")
        page.locator("#prompt-entry").click()
        page.locator(".lora-compact-row").first.wait_for()
        cards = page.evaluate("async()=> (await api('/api/characters')).characters")
        assert len(cards) == 269
        assert all(c.get("image_id") for c in cards)
        search = page.get_by_role("searchbox", name="搜索角色", exact=True)
        for query, name in [("甘雨", "甘雨"), ("gan yu", "甘雨"), ("GANYU", "甘雨"),
                            ("Exusiai", "能天使"), ("Baal", "雷电将军"), ("Mlynar", "玛恩纳"), ("Młynar", "玛恩纳")]:
            search.fill(query)
            visible = page.locator('.prompt-characters .candidate:visible > label')
            assert visible.count() == 1, query
            assert name in visible.inner_text(), query
        search.fill("no_such_character_123")
        assert page.locator('.prompt-characters .character-empty').inner_text() == "没有匹配的角色"
        # Start from a blank selection in this isolated browser only.
        for check in page.locator('[data-edit-key^="prompt-character-use-"]:checked').all():
            check.evaluate("el=>{el.checked=false;el.dispatchEvent(new Event('change'))}")
        search.fill("甘雨")
        character = page.locator(".prompt-characters .candidate").filter(has=page.locator('[data-edit-key^="prompt-character-use-"]')).filter(has_text="甘雨")
        character.locator('[data-edit-key^="prompt-character-use-"]').check()
        selected = page.locator('.lora-compact-row > label > input[type="checkbox"]:checked')
        assert selected.count() == 1, "Selecting one character must enable one preferred version"
        first = page.locator(".lora-compact-row:visible").first
        assert first.locator(':scope > label > input[type="checkbox"]').is_checked()
        assert "甘雨" in first.inner_text()
        # Choose a second character, keeping both selected rows above every
        # unchecked row and preserving the first character's chosen version.
        search.fill("雷电将军")
        assert selected.count() == 1, "Filtering must preserve the selected character and LoRA"
        character = page.locator(".prompt-characters .candidate").filter(has=page.locator('[data-edit-key^="prompt-character-use-"]')).filter(has_text="雷电将军")
        character.locator('[data-edit-key^="prompt-character-use-"]').check()
        assert selected.count() == 2
        assert page.locator(".lora-compact-row:visible").nth(0).locator(':scope > label > input[type="checkbox"]').is_checked()
        assert page.locator(".lora-compact-row:visible").nth(1).locator(':scope > label > input[type="checkbox"]').is_checked()
        first = page.locator(".lora-compact-row:visible").first
        first.locator("details").evaluate("element=>element.open=true")
        first.get_by_role("button", name="使用该版本外观", exact=True).click()
        assert not errors, errors
        page.screenshot(path=str(OUT / "selected-first.png"), full_page=True)
        page.get_by_role("button", name="管理角色", exact=True).click()
        library_search = page.get_by_role("searchbox", name="搜索角色库", exact=True)
        for query, names in [("gan yu", ["甘雨"]), ("raiden ei", ["雷电将军"]),
                             ("exusiai", ["能天使", "新约能天使"]), ("mlynar", ["玛恩纳"])]:
            library_search.fill(query)
            entry = page.locator('.character-library-list button[data-character-name]:visible')
            assert set(entry.all_inner_texts()) == {"/" + name for name in names}, query
        library_search.fill("no_such_character_123")
        assert page.locator('#dialog-body > .character-empty:visible').count() == 1
        library_search.fill("凯尔希·思衡托")
        entry = page.locator('.character-library-list button[data-character-name]:visible')
        assert entry.count() == 1
        entry.click()
        assert page.get_by_role("textbox", name="角色名（例如：林晴）", exact=True).input_value() == "凯尔希·思衡托"
        assert not errors, errors
        page.screenshot(path=str(OUT / "character-search.png"), full_page=True)
        page.locator('#dialog').evaluate("el=>el.close()")
        page.locator('#new-project').click()
        story_search = page.get_by_role("searchbox", name="搜索故事角色", exact=True)
        story_search.fill("exusiai")
        page.locator('.story-studio .character-library-list').get_by_role('button', name='/能天使', exact=True).click()
        story = page.get_by_role('textbox', name='故事文本', exact=True)
        assert '/能天使' in story.input_value()
        story.fill('/ganyu')
        page.locator('.mention-menu').get_by_role('button', name='/甘雨', exact=True).click()
        assert story.input_value() == '/甘雨 '
        assert not errors, errors
        browser.close()
        result = {"status": "passed", "cards": len(cards), "preferred_versions": "one per character",
                  "selected_loras_first": True, "version_appearance_button": True,
                  "library_search_middle_dot": True, "browser_errors": errors,
                  "character_search_chinese_pinyin_english": True, "story_search_and_mentions": True,
                  "writes_blocked": True, "gpu_calls": False}
        (OUT / "browser.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
