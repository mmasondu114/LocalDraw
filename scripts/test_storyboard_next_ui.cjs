// Test the actual click wrapper and storyboard navigation without model calls.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../web/app.js'),'utf8');
class Element {
  constructor(tag,text='',className=''){Object.assign(this,{tag,textContent:text,className,children:[],dataset:{},disabled:false});}
  append(...items){this.children.push(...items);}
  prepend(...items){this.children.unshift(...items);}
  setAttribute(){}
  removeAttribute(){}
  async click(){if(!this.disabled)await this.onclick?.();}
}
const walk=e=>[e,...e.children.flatMap(walk)];
const page={id:'P001',panels:[],lettering:[]};
const bundle=(id,status='complete',base_hash='current',created=id)=>({
  id,status,base_hash,created,pages:{P001:{payload:page,metadata:{}}},plan:{pages:[{id:'P001'}]},
});
async function fixture(bundles,confirmed=false,fail=false){
  const root=new Element('section'), calls=[],errors=[];
  const project={plan:confirmed?{pages:[{id:'P001'}]}:null,pages:confirmed?{P001:page}:{}};
  const context={
    node:(...args)=>new Element(...args),pid:'test-story',snap:{hash:'current',project},
    stage:'page',target:'P001',window:{},cacheLocal(){},
    $:id=>id==='simple-page'?root:new Element('div'),
    notify:message=>errors.push(message),preserve:async()=>calls.push('preserve'),
    appendReadablePanels(){},renderAuditCards(){},currentValue:()=>page,
    loadWorkspace:async()=>{throw Error('Existing project must not restore an old stage');},
    reloadProjects:async()=>{},renderStage:async()=>calls.push('render:'+context.stage),
    api:async url=>url.startsWith('/api/storyboard-bundles')?bundles:{hash:'updated',project},
    act:async(action,args)=>{calls.push({action,...args});if(fail)throw Error('版本已变化');},
  };
  vm.createContext(context);
  for(const [start,end] of [
    ['function safe(', 'function blank('],
    ['async function openProject(', 'function currentValue('],
    ['async function renderStoryboardBundles(', 'function node('],
    ['async function renderSimplePage(', 'function appendReadablePanels('],
  ])vm.runInContext(source.slice(source.indexOf(start),source.indexOf(end)),context);
  if(confirmed)await context.renderSimplePage(project);
  else await context.renderStoryboardBundles(root);
  return {root,context,calls,errors,next:walk(root).find(e=>e.tag==='button'&&e.textContent==='下一步')};
}
(async()=>{
  const ready=await fixture([bundle('old','complete','current','1'),bundle('new','complete','current','2')]);
  assert.equal(ready.root.children[0].className,'storyboard-next');
  await ready.next.click();
  assert.deepEqual(ready.calls.find(x=>x.action),{action:'accept_storyboard_bundle',bundle:'new'});
  assert.ok(ready.calls.includes('render:drawing'));
  assert.equal(ready.context.target,'');
  assert.deepEqual(ready.errors,[]);
  for(const b of [bundle('running','running'),bundle('failed','failed'),bundle('stale','complete','old')]){
    const f=await fixture([b]);assert.equal(f.next.disabled,true);await f.next.click();assert.equal(f.calls.length,0);
  }
  const retry=await fixture([bundle('running','running','current','2'),bundle('ready','complete','current','1')]);
  assert.equal(retry.next.disabled,false);
  const failed=await fixture([bundle('ready')],false,true);
  await failed.next.click();assert.equal(failed.context.stage,'page');assert.equal(failed.next.disabled,false);
  assert.deepEqual(failed.errors,['版本已变化']);
  const confirmed=await fixture([],true);
  assert.equal(confirmed.root.children[0].className,'storyboard-next');
  await confirmed.next.click();assert.ok(confirmed.calls.includes('render:drawing'));
  assert.equal(confirmed.calls.filter(x=>x.action).length,0);
  console.log('PASS: completed draft confirmation + drawing navigation, confirmed pages, latest bundle, running/failed/stale safeguards, failure retry');
})().catch(e=>{console.error(e);process.exitCode=1;});
