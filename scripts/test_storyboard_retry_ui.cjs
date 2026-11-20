// Exercise the real task-card renderer without loading a model or user project.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Element {
  constructor(tag, text, cls) {
    Object.assign(this, {tag, textContent:text, className:cls, children:[], dataset:{}, disabled:false});
  }
  append(...items) { this.children.push(...items); }
  setAttribute() {}
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
const retry = value => walk(sandbox.jobProgress(value)).find(el => el.dataset.control === 'failed-book-retry');
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
console.log('Storyboard retry task-card checks passed.');
