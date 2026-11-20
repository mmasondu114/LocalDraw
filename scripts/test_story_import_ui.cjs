// Exercise the import dialog using the production script and a minimal DOM.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

class Element {
  constructor(tag, text = '', className = '') {
    Object.assign(this, {tag, textContent: text, className, children: [], attributes: {}, disabled: false, open: false});
  }
  append(...items) { for (const item of items) {item.parent = this; this.children.push(item);} }
  replaceChildren(...items) {this.children = []; this.append(...items);}
  setAttribute(key, value) {this.attributes[key] = value;}
  addEventListener(name, fn) {this['on' + name] = fn;}
  showModal() {this.open = true;}
  close() {this.open = false; this.onclose?.();}
  remove() {this.parent.children = this.parent.children.filter(x => x !== this);}
  async click() {if (!this.disabled) await this.onclick?.();}
}
const walk = root => [root, ...root.children.flatMap(walk)];
const source = fs.readFileSync(path.join(__dirname, '../web/story-import.js'), 'utf8');
const result = {
  title: '<img src=x onerror=bad()>', page_count: 1, panel_count: 1, reading_direction: 'LTR', preview_hash: 'hash',
  characters: [{id: 'C001', name: 'Doctor', local_match: false}], warnings: [],
  pages: [{number: 1, panels: [{id: 'P001-K01', shot: 'Close-up', main_instant: 'A pause.', drawing_description: 'Keep this exact image description.'}],
    lettering: [{panel_id: 'P001-K01', speaker_id: 'C001', kind: 'dialogue', order: 1, text: 'Keep this exact dialogue.'}]}],
};
function fixture() {
  const body = new Element('body'), actions = [], opened = [], events = [];
  const context = {
    window: {}, document: {body}, structuredClone, crypto: {randomUUID: () => 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'},
    node: (...args) => new Element(...args),
    button: (text, handler, cls) => {const e = new Element('button', text, cls); e.onclick = handler; return e;},
    act: async (action, data) => {actions.push({action, ...data}); return action === 'preview_story_import' ? structuredClone(result) : {project: 'new-story'};},
    api: async (url, data) => {events.push(data.action);},
    preserve: async () => {events.push('preserve');},
    clearTimeout() {}, workspaceTimer: 1, workspaceProject: () => 'old-story', stage: 'script', workspaceState: {draft: 'keep'},
    openProject: async id => {events.push('open'); opened.push(id);}, notify() {},
  };
  vm.createContext(context); vm.runInContext(source, context);
  context.window.openStoryImport();
  const dialog = body.children[0], upload = walk(dialog).find(x => x.tag === 'input');
  const confirm = walk(dialog).find(x => x.textContent === '导入为新作品');
  const status = walk(dialog).find(x => x.attributes.role === 'status');
  const choose = async (text, name = 'story.json', size = 100) => {
    upload.files = [{name, size, text: async () => text}];
    await upload.onchange();
  };
  return {context, dialog, upload, confirm, status, choose, actions, opened, events, body};
}

(async () => {
  const f = fixture();
  assert.equal(f.confirm.disabled, true);
  await f.choose('\ufeff{"brief":{}}');
  assert.equal(f.confirm.disabled, false);
  assert.equal(f.actions[0].document, '\ufeff{"brief":{}}');
  assert(walk(f.dialog).some(x => x.textContent === result.title));
  assert(!walk(f.dialog).some(x => x.tag === 'img'));
  assert(walk(f.dialog).some(x => x.textContent === 'Doctor：Keep this exact dialogue.'));
  await f.confirm.click();
  assert.deepEqual(f.events, ['preserve', 'save_workspace', 'open']);
  assert.equal(f.actions[1].preview_hash, 'hash');
  assert.equal(f.actions[1].confirm, true);
  assert.equal(f.actions[1].import_key, 'a'.repeat(32));
  assert.deepEqual(f.opened, ['new-story']);
  assert.equal(f.body.children.length, 0);

  const cancel = fixture();
  await cancel.choose('{}'); cancel.dialog.close();
  assert(!cancel.actions.some(x => x.action === 'save_story_import'));
  assert.equal(cancel.opened.length, 0);

  const failure = fixture();
  await failure.choose('{}');
  failure.context.act = async () => {throw Error('P002：本页初始状态与前页结尾不一致');};
  await failure.choose('{bad}');
  assert.equal(failure.confirm.disabled, true);
  assert(failure.status.textContent.includes('P002'));
  assert.equal(failure.opened.length, 0);
  await failure.choose('{}', 'story.json', 5_000_001);
  assert(failure.status.textContent.includes('5 MB'));
  await failure.choose('{}', 'story.txt');
  assert(failure.status.textContent.includes('.json'));

  const race = fixture();
  let resolveFirst;
  race.context.act = async (action, data) => data.document === 'first' ? new Promise(resolve => {resolveFirst = resolve;}) : structuredClone(result);
  const first = race.choose('first');
  await new Promise(resolve => setImmediate(resolve));
  await race.choose('second');
  resolveFirst({...structuredClone(result), title: 'stale'});
  await first;
  assert(!walk(race.dialog).some(x => x.textContent === 'stale'));

  const retry = fixture();
  await retry.choose('source');
  let attempts = 0;
  retry.context.act = async (action, data) => {
    retry.actions.push({action, ...data});
    if (++attempts === 1) throw Error('Connection lost after save');
    return {project: 'same-story'};
  };
  await retry.confirm.click();
  assert.equal(retry.confirm.disabled, false);
  await retry.confirm.click();
  assert.equal(retry.actions[1].import_key, retry.actions[2].import_key);
  assert.deepEqual(retry.opened, ['same-story']);
  console.log('PASS: preview, exact text, inert markup, current draft preservation, cancel, invalid files, stale previews and retry identity');
})().catch(error => {console.error(error); process.exitCode = 1;});
