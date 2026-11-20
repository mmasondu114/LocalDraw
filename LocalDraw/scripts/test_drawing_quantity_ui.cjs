const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Element {
  constructor(tag, text, cls) { Object.assign(this, {tag, textContent:text, className:cls, children:[], dataset:{}, disabled:false}); }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; }
  setAttribute(name, value) { this[name] = value; }
  contains(item) { return this === item || this.children.some(c => c.contains(item)); }
}
const root = new Element('section'), calls = [];
const sandbox = {
  window:{studioFeatures:{remaining_candidate_count:true,candidate_count_total:true}}, pid:'book',
  activeJobStates:new Set(['running','pausing','paused','cancelling']),
  $:() => root, node:(...args) => new Element(...args),
  button:(label, click, cls) => Object.assign(new Element('button',label,cls),{click}),
  act:async (action, args) => {calls.push({action,args});return {quantity_revision:1,quantity_change:{remaining_panels:5,per_panel:args.candidate_count}};},
  safe:fn=>fn,
  refreshStatus:async () => {}, notify:() => {},
};
const source = fs.readFileSync('web/app.js','utf8');
vm.runInNewContext(source.slice(source.indexOf('const pausedDrawingCounts ='), source.indexOf('function jobProgress(job)')),sandbox);
vm.runInNewContext(source.slice(source.indexOf('async function controlJob('), source.indexOf('function confirmCancelJob(')),sandbox);
const walk = e => [e,...e.children.flatMap(walk)];
const find = tag => walk(root).find(e => e.tag === tag);
const job = {id:'drawing',project:'book',status:'paused',request:{action:'draw_imported',candidate_count:16}};
sandbox.window.activeStudioJob=job;
(async () => {
  for (const status of ['running','pausing','cancelling']) {
    sandbox.renderRemainingDrawingSettings({...job,status});
    assert.equal(find('select').disabled,true);assert.equal(find('button').disabled,true);
  }
  sandbox.renderRemainingDrawingSettings(job);
  assert.equal(root.hidden,false);
  const select = find('select');
  assert.equal(select.children.length,16);assert.equal(select.disabled,false);
  select.value='3';
  sandbox.renderRemainingDrawingSettings(job);
  assert.equal(find('select'),select);assert.equal(select.value,'3');
  await find('button').click();
  assert.equal(calls.length,1);assert.equal(calls[0].action,'set_remaining_candidate_count');
  assert.equal(calls[0].args.candidate_count,3);assert.equal(calls[0].args.job,'drawing');
  assert.equal(calls[0].args.quantity_mode,'total');
  // A quick resume must wait for persistence, including a second selection.
  let finishSave;
  const originalAct=sandbox.act;
  sandbox.act=async(action,args)=>{
    if(action==='set_remaining_candidate_count')await new Promise(resolve=>{finishSave=resolve;});
    return originalAct(action,args);
  };
  const saving=sandbox.window.setPausedDrawingCount(job,4);
  const resuming=sandbox.controlJob(job,'resume');
  assert.equal(calls.some(call=>call.action==='resume_job'),false);
  finishSave();await saving;await resuming;
  assert.equal(calls.at(-2).args.candidate_count,4);
  assert.equal(calls.at(-1).action,'resume_job');
  sandbox.act=async()=>{throw Error('save failed');};
  await assert.rejects(sandbox.window.setPausedDrawingCount(job,2),/save failed/);
  await assert.rejects(sandbox.controlJob(job,'resume'),/数量尚未保存/);
  sandbox.act=originalAct;
  await sandbox.window.setPausedDrawingCount(job,4);
  sandbox.window.studioDisconnected=true;sandbox.renderRemainingDrawingSettings(job);
  assert.equal(find('button').disabled,true);
  sandbox.window.studioDisconnected=false;sandbox.window.studioFeatures={};sandbox.renderRemainingDrawingSettings(job);
  assert.equal(find('button').disabled,true);
  assert.ok(walk(root).some(e=>String(e.textContent).includes('重启后台')));
  sandbox.renderRemainingDrawingSettings({...job,project:'another'});assert.equal(root.hidden,true);
  sandbox.renderRemainingDrawingSettings({...job,status:'done'});assert.equal(root.hidden,true);
  const html=fs.readFileSync('web/index.html','utf8');
  assert.ok(html.indexOf('id="remaining-drawing-settings"') < html.indexOf('</aside>'));
  console.log('Paused drawing quantity sidebar checks passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
