// Exercise the real task-card renderer without loading a model or user project.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Element {
  constructor(tag, text, cls) {
    Object.assign(this, {tag, textContent:text, className:cls, children:[], dataset:{}, disabled:false});
  }
  append(...items) { this.children.push(...items); }
  setAttribute(name, value) {(this.attributes ||= {})[name]=value;}
  hasAttribute(name) {return Object.hasOwn(this.attributes||{},name);}
}
const calls = [];
const sandbox = {
  window:{},
  node:(...args) => new Element(...args),
  button:(text, fn, cls) => Object.assign(new Element('button', text, cls), {click:fn}),
  controlJob:(job, action) => calls.push([job.id, action]),
  jobProgressDetails:() => new Element('div'),
};
const source = fs.readFileSync('web/app.js', 'utf8');
vm.runInNewContext(source.slice(source.indexOf('function jobProgress(job)'), source.indexOf('function jobProgressDetails(job)')), sandbox);
const walk = el => [el, ...el.children.flatMap(walk)];
const job = {id:'failed-book', label:'生成整部分镜', status:'failed', message:'invalid layout',
  request:{action:'storyboard_all', bundle_id:'draft'}, progress:{completed:7,total:8,unit:'页'}};
const retry = value => walk(sandbox.jobProgress(value)).find(el => el.dataset.control === 'failed-book-retry' && !el.hidden);
const button = retry(job);
assert.equal(button.textContent, '重新生成当前出错页');
assert.equal(button.disabled, false);
button.click();
assert.deepEqual(calls, [['failed-book', 'retry']]);
for (const status of ['running','paused','cancelled','done','resumed','interrupted']) {
  assert.equal(retry({...job,status}), undefined);
}
assert.equal(retry({...job,request:{action:'draw_imported',bundle_id:'draft'}}), undefined);
assert.equal(retry({...job,request:{action:'storyboard_all'}}), undefined);
sandbox.window.activeStudioJob = {id:'busy'};
assert.equal(retry(job).disabled, true);
sandbox.window.activeStudioJob = null;
sandbox.window.studioDisconnected = true;
assert.equal(retry(job).disabled, true);
sandbox.window.studioDisconnected = false;
const card = sandbox.jobProgress({...job,status:'running'});
const bar = walk(card).find(e=>e.tag==='progress');
assert.equal(bar.max,8);
assert.equal(bar.value,7);
const control = name=>walk(card).find(el=>el.dataset.control==='failed-book-'+name);
assert.equal(control('pause').disabled,false);
const audited = {...job,status:'running',progress:{completed:8,total:8,unit:'页'},
  detail_progress:{completed:3,total:8,unit:'页已核对',current_page:'P005'}};
card.updateJob(audited);
assert.equal(bar.value,3);
assert.equal(bar.max,8);
assert.match(walk(card).find(el=>el.className==='task-message').textContent,/P005/);
card.updateJob({...audited,detail_progress:{...audited.detail_progress,completed:8,current_page:null},message:'核对结束'});
assert.equal(bar.value,8);
card.updateJob({...job,status:'paused'});
assert.equal(control('resume').disabled,false);
control('resume').click(); assert.deepEqual(calls.at(-1),['failed-book','resume']);
card.updateJob({...job,status:'done'});
assert.equal(control('resume').disabled,true);
console.log('PASS: retry visibility and job controls, audit and generation progress on the same card, paused resume');
