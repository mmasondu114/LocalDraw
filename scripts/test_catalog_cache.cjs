// Shared reads are deduplicated, isolated from caller mutations, and invalidated
// on edits. Project/job data must never enter this short-lived catalog cache.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('web/app.js','utf8');
const calls=[];
const sandbox={structuredClone,AbortSignal,boot:{token:'test'},
  fetch:async(url,options)=>{
    calls.push(url);
    return {ok:true,json:async()=>({characters:[{name:'original'}],url})};
  }};
vm.createContext(sandbox);
vm.runInContext(source.slice(source.indexOf('const catalogCache ='),source.indexOf('async function act(')),sandbox);
(async()=>{
  const [a,b]=await Promise.all([sandbox.api('/api/characters'),sandbox.api('/api/characters')]);
  assert.equal(calls.length,1);
  a.characters[0].name='edited';
  assert.equal(b.characters[0].name,'original');
  assert.equal((await sandbox.api('/api/characters')).characters[0].name,'original');
  await sandbox.api('/api/action',{action:'save_workspace'});
  await sandbox.api('/api/characters');
  assert.equal(calls.filter(x=>x==='/api/characters').length,1);
  await sandbox.api('/api/action',{action:'character_save'});
  await sandbox.api('/api/characters');
  assert.equal(calls.filter(x=>x==='/api/characters').length,2);
  vm.runInContext('catalogCache.get("/api/characters").expires=0',sandbox);
  await sandbox.api('/api/characters');
  assert.equal(calls.filter(x=>x==='/api/characters').length,3);
  await sandbox.api('/api/project?id=a');await sandbox.api('/api/project?id=a');
  assert.equal(calls.filter(x=>x==='/api/project?id=a').length,2);
  const fetch=sandbox.fetch;
  sandbox.fetch=async()=>{throw Error('offline');};
  await assert.rejects(sandbox.api('/api/drawing-options'),/无法连接/);
  sandbox.fetch=fetch;
  await sandbox.api('/api/drawing-options');
  assert.equal(calls.filter(x=>x==='/api/drawing-options').length,1);
  console.log('Catalog cache: deduplication, isolation, invalidation, expiry and failure recovery passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
