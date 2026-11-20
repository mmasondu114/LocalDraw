// Exercise the production story UI with storage writes denied and a tiny DOM.
// No browser profile, user data, language model, or GPU is used.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const rootDir = path.resolve(__dirname, '..');
const app = fs.readFileSync(path.join(rootDir, 'web/app.js'), 'utf8');
const creative = fs.readFileSync(path.join(rootDir, 'web/creative.js'), 'utf8');

class Element {
  constructor(tag, text = '', className = '') {
    Object.assign(this, {tag, textContent:text, className, children:[], dataset:{},
      value:'', disabled:false, classList:{add() {}}, attributes:{}});
  }
  append(...items) {
    for (const item of items) {
      if (item.parent) item.parent.children = item.parent.children.filter(x => x !== item);
      item.parent = this; this.children.push(item);
    }
  }
  prepend(...items) {this.append(...items); this.children.unshift(...this.children.splice(-items.length));}
  replaceChildren(...items) {for (const c of this.children) c.parent = null; this.children = []; this.append(...items);}
  insertBefore(item, before) {this.append(item); this.children.pop(); this.children.splice(this.children.indexOf(before), 0, item);}
  setAttribute(key, value) {this.attributes[key] = value;}
  removeAttribute(key) {delete this.attributes[key];}
  async click() {if (!this.disabled) await this.onclick?.();}
  addEventListener() {}
  focus() {}
}
const walk = el => [el, ...el.children.flatMap(walk)];
const result = {
  original:'小林在雨后的书店发现一只纸船。', mode:'expand',
  payload:{story:'小林在书店门前拾起纸船，抬头询问店主：“这是你折的吗？”',
    brief:{title:'纸船',page_count:2,reading_direction:'LTR'},
    canon:{characters:[]}, assumptions:[], changes:['增加对白']},
};

async function fixture({source=result.original, saved, jobs=[{status:'done',result}]} = {}) {
  const root = new Element('section'); root.dataset.renderToken = '1';
  const actions = [], timers = [], calls = [];
  const ids = new Map();
  const context = {
    console, structuredClone, setTimeout: fn => {timers.push(fn); return timers.length;}, clearTimeout() {},
    window:{}, document:{querySelectorAll: selector => walk(root).filter(e => selector === '[data-edit-key]' && e.dataset.editKey)},
    localStorage:{getItem:()=>null, setItem:()=>{throw Error('QuotaExceededError');}},
    node:(...args)=>new Element(...args),
    $: id => {if (!ids.has(id)) ids.set(id,new Element('input')); return ids.get(id);},
    readObject:()=>new Element('pre'), jobProgress:()=>new Element('div'), notify() {},
    newMode:true, pid:null, snap:null, dirty:false, restoringWorkspace:false,
    stage:'script', target:'', mode:'form', valueBaseHash:null, workspaceTimer:null,
    workspaceState:{views:{'script:':{fields:{'story-text':source}}}, ...(saved?{storyResult:structuredClone(saved)}:{})},
    workspaceProject:()=>context.newMode?'new':context.pid,
    workspaceKey:()=>`${context.stage}:${context.target}`,
    reloadProjects:async()=>{}, renderStage:async()=>{calls.push('renderStage');},
    loadWorkspace:async()=>{context.workspaceState={views:{}};},
    api:async(url, body)=>{
      calls.push(url);
      if (url === '/api/characters') return {characters:[],fields:{}};
      if (url === '/api/status') return {jobs};
      if (url.startsWith('/api/job')) return {status:'done',result:structuredClone(result)};
      if (url.startsWith('/api/project?')) return {project:{script:result.payload.story}};
      if (body?.action === 'save_workspace') {actions.push(structuredClone(body)); return {saved:true};}
      throw Error(`Unexpected API: ${url}`);
    },
    act:async(action, body)=>{
      actions.push({action,...structuredClone(body)});
      if (action === 'story_draft') return {job:'test-job'};
      if (action === 'create_story') return {project:'test-story'};
      throw Error(`Unexpected action: ${action}`);
    },
  };
  context.window.renderLoraSettings = async()=>({read:()=>({mode:'none'}),flush:async()=>{}});
  vm.createContext(context);
  // Use the real button wrapper: it disables controls before calling handlers.
  vm.runInContext(app.slice(app.indexOf('function safe('), app.indexOf('function blank(')),context);
  vm.runInContext(app.slice(app.indexOf('function cacheLocal('), app.indexOf('const $ =')),context);
  vm.runInContext(app.slice(app.indexOf('function saveWorkspace('), app.indexOf('window.saveWorkspace=')),context);
  vm.runInContext(app.slice(app.indexOf('async function openProject('), app.indexOf('function currentValue(')),context);
  vm.runInContext(creative,context);
  await context.window.renderStoryStart(root,null);
  return {root,context,actions,calls,flush:async()=>{while(timers.length) await timers.shift()();},
    field:label=>walk(root).find(e=>e.attributes['aria-label']===label),
    button:label=>walk(root).find(e=>e.tag==='button'&&e.textContent===label)};
}

(async()=>{
  const recovered = await fixture();
  assert.equal(recovered.root.children[2].className,'story-review workspace-pane');
  assert.equal(recovered.field('AI 剧本草稿（可修改）').value,result.payload.story);
  assert.equal(recovered.button('下一步').disabled,false);
  await recovered.flush();
  assert.equal(recovered.actions.find(a=>a.action==='save_workspace').state.storyResult.payload.story,result.payload.story);
  await recovered.button('下一步').click();
  assert.equal(recovered.actions.filter(a=>a.action==='create_story').length,1);
  assert.equal(recovered.context.stage,'page');
  assert.equal(recovered.context.pid,'test-story');
  assert.ok(recovered.calls.includes('renderStage'));

  const restored = await fixture({source:'different input',saved:result,jobs:[]});
  assert.equal(restored.field('AI 剧本草稿（可修改）').value,result.payload.story);
  const edited = '小林把纸船递给店主。';
  restored.field('AI 剧本草稿（可修改）').value=edited;
  await restored.button('下一步').click();
  assert.equal(restored.actions.find(a=>a.action==='create_story').result.payload.story,edited);
  assert.equal(restored.actions.filter(a=>a.action==='story_draft').length,1);

  const blank = await fixture({source:''});
  assert.equal(blank.field('AI 剧本草稿（可修改）'),undefined);
  assert.equal(blank.button('下一步').disabled,true);
  blank.field('故事文本').value=result.original;
  await blank.button('让 AI 整理剧本').click();
  assert.equal(blank.field('AI 剧本草稿（可修改）').value,result.payload.story);
  assert.equal(blank.button('下一步').disabled,false);
  await blank.flush();
  assert.ok(blank.actions.some(a=>a.action==='save_workspace'&&a.state.storyResult));
  console.log('PASS: quota-full generation, automatic recovery, disk save, reload, one-click next, edits, blank workspace isolation');
})().catch(error=>{console.error(error);process.exitCode=1;});
