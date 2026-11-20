const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class El {
  constructor(tag,text){Object.assign(this,{tag,textContent:text,children:[],dataset:{},value:'',style:{}});}
  append(...xs){this.children.push(...xs);}
  replaceChildren(...xs){this.children=xs;}
  setAttribute(k,v){this[k]=v;}
  addEventListener(){}
  checkValidity(){return true;}
  get options(){return this.children;}
  get firstChild(){return this.children[0];}
  cloneNode(){return Object.assign(new El(this.tag,this.textContent),{value:this.value});}
}
const root=new El('root'),calls=[];
const model='qwen_image_edit_2511_fp8mixed.safetensors';
const node=(...args)=>new El(...args),walk=n=>[n,...n.children.flatMap(walk)];
const context={window:{},structuredClone,refreshStatus:async()=>{}};
vm.runInNewContext(fs.readFileSync('web/prompt-exchange.js','utf8'),context);
(async()=>{
  const count=node('input');count.value='4';
  await context.window.renderPromptExchange(root,{
    pid:'work',node,button:(label,click)=>Object.assign(node('button',label),{click}),notify:()=>{},
    models:[model],checkpoint:model,countControl:count,
    images:{selections:{},batches:[{panel_id:'P001-K01',images:[0,1,2].map(i=>({id:'ref'+i,file:'b'+i+'/1.png'}))}]},
    api:async path=>path.includes('prompt-import')?{bundle:{id:'bundle',panels:[{panel_id:'P002-K02',page_id:'P002'}]},stale:false}: {},
    act:async(action,args)=>{calls.push({action,args});return {};},
  });
  const controls=walk(root),first=controls.find(n=>n['aria-label']==='固定参考图（本次范围共用）');
  const second=controls.find(n=>n['aria-label']==='参考图 2'),third=controls.find(n=>n['aria-label']==='参考图 3');
  const submit=controls.find(n=>n.tag==='button'&&n.textContent==='③ 按导入提示词绘制');
  first.value='ref0';second.value='ref1';third.value='ref2';await submit.click();
  assert.deepEqual(JSON.parse(JSON.stringify(calls[0].args.references_by_panel)),{'P002-K02':['ref0','ref1','ref2']});
  assert.equal(calls[0].args.candidate_count,4);
  third.value='ref1';await assert.rejects(submit.click(),/重复/);
  second.value='';third.value='ref2';await assert.rejects(submit.click(),/顺序/);
  first.value='';await assert.rejects(submit.click(),/第一张/);
  assert.equal(calls.length,1);
  console.log('Qwen reference UI: ordered references and invalid-selection guards passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
