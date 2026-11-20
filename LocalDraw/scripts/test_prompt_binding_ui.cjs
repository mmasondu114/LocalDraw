// Browser regression against an isolated server; no GPU or user projects.
const { chromium } = require("playwright");
const { spawn } = require("node:child_process");
const path = require("node:path");
const assert = require("node:assert/strict");
const root = path.resolve(__dirname, "..");
const server = spawn(path.join(root, ".venv/Scripts/python.exe"),
  [path.join(root, "scripts/test_prompt_ui.py"), "--port", "8788", "--data-root",
    path.join(root, "data/test-results", "prompt-binding-ui-" + Date.now())],
  {cwd: root, windowsHide: true, env: {...process.env, PYTHONUTF8: "1"}});
let browser;
(async () => {
  await new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(Error("Fixture did not start")), 15000);
    server.stdout.on("data", data => {
      if (data.toString().includes("http://127.0.0.1:8788")) {clearTimeout(timeout); resolve();}
    });
    server.once("exit", code => {clearTimeout(timeout); reject(Error(`Fixture exited: ${code}`));});
    server.stderr.on("data", data => process.stderr.write(data));
  });
  browser = await chromium.launch({channel: "chrome", headless: true});
  const page = await browser.newPage();
  const failures = [];
  page.on("pageerror", error => failures.push(error.message));
  await page.goto("http://127.0.0.1:8788");
  await page.locator("#prompt-entry").click();
  await page.getByRole("searchbox", {name: "搜索角色", exact: true}).fill("测试甲");
  const character = page.locator('.prompt-characters > div > .candidate').filter({hasText: "测试甲"});
  await character.locator('input[type=checkbox]').first().check();
  const reference = character.locator('[data-edit-key^="prompt-character-reference-"]');
  assert.equal(await reference.isChecked(), true);
  assert.equal(await reference.isEnabled(), true);
  assert.equal(await page.locator('[data-edit-key="prompt-use-reference"]').count(), 0);
  await page.locator('[data-character-binding]').filter({hasText: "参考图 ×"}).waitFor();
  await page.locator('[data-edit-key="prompt-positive"]').fill("1girl, solo, plain background");
  await character.locator('textarea').fill("silver hair, white coat");
  await page.locator('[data-edit-key="prompt-hand-detail"]').uncheck();
  const submissions = [];
  // Capture the real form submission and stop before creating a project/job.
  await page.route("**/api/action", async route => {
    const body = route.request().postDataJSON();
    if (body.action !== "create_quick_sample") return route.continue();
    submissions.push(body);
    await route.fulfill({status: 400, contentType: "application/json",
      body: JSON.stringify({error: "Test submission captured"})});
  });
  async function submit() {
    const expected = submissions.length + 1;
    await Promise.all([
      page.waitForResponse(response => response.url().endsWith("/api/action") &&
        response.request().postDataJSON()?.action === "create_quick_sample"),
      page.getByRole("button", {name: "开始画图", exact: true}).click(),
    ]);
    assert.equal(submissions.length, expected);
    return submissions.at(-1);
  }
  let submitted = await submit();
  assert.equal(submitted.use_reference, true);
  assert.equal(submitted.characters[0].use_reference, true);
  assert.equal(submitted.characters[0].prompt, "silver hair, white coat");
  await reference.uncheck();
  submitted = await submit();
  assert.equal(submitted.use_reference, false);
  assert.equal(submitted.characters[0].use_reference, false);
  await page.reload();
  await page.locator('[data-character-binding]').filter({hasText: "仅文字，无参考图"}).waitFor();
  assert.equal(await reference.isChecked(), false);
  // A legacy draft's disabled master switch cannot disable a selected reference.
  await page.evaluate(async () => {
    const card = (await api("/api/characters")).characters.find(c => c.image_id);
    await renderPromptStudio(document.getElementById("extras"), {quick_sample: true, prompt_input: {
      text: "1girl, solo", use_reference: false, hand_detail: false,
      characters: [{name: card.name, revision: card.revision,
        prompt: "silver hair, white coat", use_reference: true}],
    }});
  });
  await page.locator('[data-character-binding]').filter({hasText: "参考图 ×"}).waitFor();
  submitted = await submit();
  assert.equal(submitted.use_reference, true);
  assert.deepEqual(failures, []);
  console.log("PASS: selected reference submits; text-only opt-out persists; legacy master switch ignored; no browser errors.");
})().catch(error => {console.error(error); process.exitCode = 1;})
  .finally(async () => {if (browser) await browser.close(); server.kill();});
