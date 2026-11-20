// Run the shipped home and settings handlers in Node with a small in-memory DOM.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Element {
  constructor(tag, textContent = '', className = '') {
    Object.assign(this, {tag, textContent, className, value:'', dataset:{}, children:[], attributes:{}});
    this.classList = new Set();
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(name, value) { this.attributes[name] = value; }
  showModal() { this.open = true; }
  querySelectorAll(tag) { return walk(this).slice(1).filter(el=>el.tag===tag); }
}
const walk = root => [root, ...root.children.flatMap(walk)];
const node = (...args) => new Element(...args);
const body = node('body');
const calls = [];
const modelState = {
  selected:'flash_next_64k',
  options:[{id:'flash_next_64k',label:'Flash-Next 64K',available:true},
    {id:'huihui_27b',label:'Huihui 27B',available:true}]
};
const window = {};
const sandbox = {window, node, document:{body, addEventListener(){},
  getElementById:id=>walk(body).find(e=>e.id===id), createElementNS:(_,tag)=>node(tag)},
  boot:{projects:[{id:'test-book',title:'离线作品',pages:3}]}, pid:null,
  reloadProjects:async()=>{}, preserve:async()=>{}, openProject:async id=>calls.push(['project',id]),
  localStorage:{getItem:()=>null}, cacheLocal(){}, $:id=>walk(body).find(e=>e.id===id),
  api:async url=>{assert.equal(url,'/api/creative-drafts');return [];},
  act:async(action,data)=>{
    calls.push([action,data?.model]);
    if(action==='set_analysis_model')modelState.selected=data.model;
    else assert.equal(action,'analysis_model');
    return structuredClone(modelState);
  },
  button:(name,fn,cls)=>Object.assign(node('button',name,cls),{click:fn})
};
const script = fs.readFileSync(path.join(__dirname,'../web/studio-home.js'),'utf8');
vm.runInNewContext(script,sandbox);

(async()=>{
  window.openDirectStoryboardImport=()=>calls.push(['story']);
  window.openDirectPromptImport=()=>calls.push(['prompts']);
  window.openStudioQueue=()=>calls.push(['queue']);
  window.leaveStudioHome=()=>calls.push(['leave']);
  await window.showStudioHome();
  const home=sandbox.$('studio-home');
  const click=async(root,name)=>{
    const control=walk(root).find(e=>e.tag==='button'&&e.textContent===name);
    assert.ok(control,name);
    await control.click();
  };
  assert.deepEqual(walk(home).filter(e=>e.className==='home-choice')
    .map(card=>card.children.find(e=>e.tag==='h2').textContent),['提示词出图','创作新漫画','逐格提示词出图']);
  await click(home,'直接导入分镜');
  await click(home,'直接导入每格提示词');
  await click(home,'夜间任务列表');
  await click(home,'继续');
  assert.deepEqual(calls,[['story'],['prompts'],['queue'],['project','test-book'],['leave']]);

  window.openStudioDisplay();
  await new Promise(resolve=>setImmediate(resolve));
  const dlg=walk(body).find(e=>e.tag==='dialog');
  const model=walk(dlg).find(e=>e.tag==='select');
  assert.ok(dlg.open);
  assert.equal(model.value,'flash_next_64k');
  assert.equal(model.disabled,false);
  await click(dlg,'大字号');
  assert.equal(body.dataset.textSize,'large');
  assert.deepEqual(model.children.map(el=>el.value),['flash_next_64k','huihui_27b']);
  model.value='huihui_27b';
  await model.onchange();
  assert.equal(model.value,'huihui_27b');
  assert.deepEqual(calls.slice(-2),[['analysis_model',undefined],['set_analysis_model','huihui_27b']]);
  console.log('PASS: original home cards, saved project, queue, both import entries, text sizing and analysis-model selection');
})().catch(error=>{console.error(error);process.exitCode=1;});
