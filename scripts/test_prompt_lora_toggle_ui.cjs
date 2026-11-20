// Regression for explicit character selection versus a user's LoRA opt-out.
const {chromium} = require("playwright");
const {spawn} = require("node:child_process");
const path = require("node:path");
const assert = require("node:assert/strict");
const root = path.resolve(__dirname, "..");
const base = "http://127.0.0.1:8789";
const server = spawn(path.join(root, ".venv/Scripts/python.exe"),
  [path.join(root, "scripts/test_prompt_ui.py"), "--port", "8789", "--data-root",
    path.join(root, "data/test-results", "prompt-lora-toggle-" + Date.now())],
  {cwd: root, windowsHide: true, env: {...process.env, PYTHONUTF8: "1"}});
let browser;
(async () => {
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(Error("Fixture did not start")), 15000);
    server.stdout.on("data", data => {if (data.toString().includes(base)) {clearTimeout(timer); resolve();}});
    server.once("exit", code => {clearTimeout(timer); reject(Error(`Fixture exited: ${code}`));});
    server.stderr.on("data", data => process.stderr.write(data));
  });
  let cards;
  for (let attempt = 0; attempt < 50; attempt++) {
    try {cards = (await (await fetch(base + "/api/characters")).json()).characters; break;}
    catch (error) {if (attempt === 49) throw error; await new Promise(resolve => setTimeout(resolve, 100));}
  }
  const files = ["sdxl/测试甲.safetensors", "sdxl/测试甲_备用.safetensors", "sdxl/测试乙.safetensors", "sdxl/风格.safetensors"];
  browser = await chromium.launch({channel: "chrome", headless: true});
  const page = await browser.newPage();
  const failures = [], submissions = [];
  page.on("pageerror", e => failures.push(e.message));
  await page.route("**/api/drawing-options", async route => {
    const response = await route.fetch(), data = await response.json();
    data.comfy.loras = files;
    data.lora_labels = Object.fromEntries(files.map((file, i) => [file,
      {character_id: i === 3 ? "" : cards[i < 2 ? 0 : 1].id, preferred: i === 0}]));
    data.lora_inspection = Object.fromEntries(files.map(file => [file, {family: "SDXL", structurally_compatible: true}]));
    await route.fulfill({response, json: data});
  });
  await page.route("**/api/action", async route => {
    const body = route.request().postDataJSON();
    if (body.action !== "create_quick_sample") return route.continue();
    submissions.push(body);
    await route.fulfill({status: 400, json: {error: "Test submission captured"}});
  });
  await page.goto(base);
  await page.locator("#prompt-entry").click();
  const selected = index => page.locator(`[data-edit-key="prompt-character-use-${cards[index].id}"]`);
  const lora = index => page.locator(`[data-edit-key="prompt-lora-use-${files[index]}"]`);
  async function showCharacter(index) {
    await page.getByRole("searchbox", {name: "搜索角色", exact: true}).fill(cards[index].name);
  }
  await showCharacter(0);
  await selected(0).check();
  assert.equal(await lora(0).isChecked(), true);
  assert.equal(await lora(1).isChecked(), false);
  await lora(0).uncheck();
  await page.locator('[data-edit-key="prompt-model"]').dispatchEvent("change");
  assert.equal(await lora(0).isChecked(), false, "Model compatibility refresh must preserve opt-out");
  await page.locator('[data-edit-key="prompt-positive"]').fill("two people in a park");
  await showCharacter(1);
  await selected(1).check();
  assert.equal(await lora(2).isChecked(), true);
  assert.equal(await lora(0).isChecked(), false, "Selecting another character must preserve opt-out");
  await page.getByRole("button", {name: "刷新角色列表", exact: true}).click();
  await page.reload();
  await page.locator('[data-character-binding]').filter({hasText: cards[1].name}).waitFor();
  assert.equal(await lora(0).isChecked(), false, "Reload must preserve opt-out");
  assert.equal(await lora(2).isChecked(), true);
  await lora(2).uncheck();
  await page.locator('[data-edit-key^="prompt-character-reference-"]').evaluateAll(inputs => {
    for (const input of inputs) {input.checked = false; input.dispatchEvent(new Event("change", {bubbles: true}));}
  });
  await Promise.all([
    page.waitForResponse(r => r.url().endsWith("/api/action") && r.request().postDataJSON()?.action === "create_quick_sample"),
    page.getByRole("button", {name: "开始画图", exact: true}).click(),
  ]);
  assert.equal(submissions.length, 1);
  assert.deepEqual(submissions[0].loras, [], "Unchecked LoRAs must not be submitted");
  assert.equal(submissions[0].characters.length, 2);
  // Unselecting and explicitly selecting the character again starts a new choice.
  await showCharacter(0);
  await selected(0).uncheck();
  await selected(0).check();
  assert.equal(await lora(0).isChecked(), true);
  await lora(0).uncheck();
  await lora(1).check();
  await page.getByRole("button", {name: "刷新角色列表", exact: true}).click();
  assert.equal(await lora(0).isChecked(), false);
  assert.equal(await lora(1).isChecked(), true, "Manual alternative must survive refresh");
  // Reopening a project with characters and deliberately no LoRAs is not a new selection.
  await page.evaluate(async cards => {
    await renderPromptStudio(document.getElementById("extras"), {quick_sample: true, prompt_input: {
      text: "two people in a park", loras: [], characters: cards.map(c => ({name: c.name,
        revision: c.revision, prompt: "silver hair", use_reference: false})),
    }});
  }, cards);
  for (let i = 0; i < files.length; i++) assert.equal(await lora(i).isChecked(), false);
  assert.deepEqual(failures, []);
  console.log("PASS: auto-select on character selection; manual opt-out survives other characters, refresh, reload, submission and project reopen; explicit reselect works.");
})().catch(e => {console.error(e); process.exitCode = 1;})
  .finally(async () => {if (browser) await browser.close(); server.kill();});
