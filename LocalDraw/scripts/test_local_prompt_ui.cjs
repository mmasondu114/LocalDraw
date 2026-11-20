// Exercise real UI handlers without starting any inference process.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
class Element {
  constructor(tag, textContent = '', className = '') {
    Object.assign(this, {tag, textContent, className, children: [], dataset: {}, attributes: {}, value: '', disabled: false});
  }
  append(...items) {for (const x of items) {x.parent = this; this.children.push(x);}}
  replaceChildren(...items) {this.children = []; this.append(...items);}
  get firstChild() {return this.children[0];}
  setAttribute(key, value) {this.attributes[key] = value;}
  addEventListener(name, fn) {this['on' + name] = fn;}
  showModal() {this.open = true;}
  close() {this.open = false; this.onclose?.();}
  remove() {this.parent.children = this.parent.children.filter(x => x !== this);}
  async click() {if (!this.disabled) return this.onclick?.();}
}
const walk = root => [root, ...root.children.flatMap(walk)];
const source = fs.readFileSync(path.join(__dirname, '../web/prompt-exchange.js'), 'utf8');
async function fixture({stale = false, partial = false, active = false} = {}) {
  const body = new Element('body'), root = new Element('section'); body.append(root);
  const actions = [], notices = [];
  const row = {panel_id: 'P001-K01', page_id: 'P001', positive: 'One person reads.', negative: '', summary: 'Reads a book.'};
  const documentData = {format: 'comic-prompt-exchange/v1', project_id: 'story-test', storyboard_hash: 'original',
    panels: [structuredClone(row), ...(partial ? [{...row, panel_id: 'P001-K02', positive: ''}] : [])]};
  let imported = null;
  const api = async url => url.startsWith('/api/local-prompts')
    ? {draft: {document: documentData, completed: 1, status: partial ? 'failed' : 'complete'}, stale}
    : {bundle: imported, stale: false};
  const act = async (action, data) => {
    actions.push({action, ...structuredClone(data)});
    if (action === 'fill_local_prompts') return {job: 'prompt-job'};
    if (action === 'preview_prompt_import') return {panels: data.document.panels, total: 2, errors: [], warnings: [],
      missing: partial ? ['P001-K02'] : [], valid: !stale, preview_hash: data.document.panels[0].positive};
    if (action === 'save_prompt_import') {imported = {id: 'saved', panels: data.document.panels}; return imported;}
    throw Error('Unexpected action: ' + action);
  };
  const node = (...args) => new Element(...args);
  const button = (text, handler, cls) => {const el = node('button', text, cls); el.onclick = handler; return el;};
  const context = {window: {activeStudioJob: active ? {id: 'existing'} : null}, document: {body}, structuredClone};
  vm.createContext(context); vm.runInContext(source, context);
  const countControl = node('select'); countControl.value = '2';
  await context.window.renderPromptExchange(root, {pid: 'story-test', api, act, node, button,
    notify: text => notices.push(text), countControl, models: ['test.safetensors'], checkpoint: 'test.safetensors'});
  const find = text => walk(body).find(x => x.textContent === text);
  return {body, actions, notices, find};
}
(async () => {
  const f = await fixture();
  await f.find('本地 Qwen 自动填写提示词').click();
  assert.equal(f.actions[0].action, 'fill_local_prompts');
  assert(f.notices.some(text => text.includes('GPU')));
  assert(!walk(f.body).some(x => x.textContent.includes('CPU')));
  assert(!f.actions.some(x => x.action.includes('draw') || x.action === 'save_prompt_import'));
  await f.find('核对本地草稿').click();
  const positive = walk(f.body).find(x => x.tag === 'textarea');
  assert(!positive.readOnly);
  positive.value = 'Edited close-up of one reader.';
  await f.find('确认使用 1 格提示词').click();
  const save = f.actions.find(x => x.action === 'save_prompt_import');
  assert.equal(save.document.panels[0].positive, positive.value);
  assert.equal(save.preview_hash, positive.value); // Edited text was revalidated.
  assert.equal(save.confirm, true);
  assert.equal(save.document.storyboard_hash, 'original');
  assert(!f.actions.some(x => x.action.includes('draw')));
  assert(!walk(f.body).some(x => x.tag === 'dialog'));

  const cancelled = await fixture();
  await cancelled.find('核对本地草稿').click();
  await cancelled.find('返回，不导入').click();
  assert(!cancelled.actions.some(x => x.action === 'save_prompt_import'));
  const outdated = await fixture({stale: true});
  assert.equal(outdated.find('核对本地草稿').disabled, true);
  const partial = await fixture({partial: true});
  await partial.find('核对本地草稿').click();
  assert.equal(partial.actions[0].document.panels.length, 1);
  assert(walk(partial.body).some(x => x.textContent.includes('未提供 1 格')));
  const busy = await fixture({active: true});
  assert.equal(busy.find('本地 Qwen 自动填写提示词').disabled, true);
  const upload = await fixture();
  const input = walk(upload.body).find(x => x.tag === 'input' && x.type === 'file');
  input.files = [{size: 100, text: async () => JSON.stringify({panels: [{panel_id: 'P001-K01', page_id: 'P001', positive: 'External prompt', negative: ''}]})}];
  await input.onchange();
  assert(upload.find('确认使用 1 格提示词'));
  assert.equal(upload.actions[0].action, 'preview_prompt_import');
  console.log('PASS: local button, editable review, revalidation, no automatic drawing, cancel, stale/partial drafts, busy state and external import.');
})().catch(error => {console.error(error); process.exitCode = 1;});
