// Exercise the real dialog and handlers without generating images.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Element {
 constructor(tag,text='',cls=''){Object.assign(this,{tag,textContent:text,className:cls,dataset:{},children:[],value:'',disabled:false});}
 append(...items){for(const item of items){item.parent=this;this.children.push(item);}}
 setAttribute(){} focus(){} showModal(){this.open=true;} close(){this.open=false;this.onclose?.();}
 remove(){this.parent.children=this.parent.children.filter(x=>x!==this);}
}
const node=(...args)=>new Element(...args), walk=e=>[e,...e.children.flatMap(walk)];
const moves=[],calls=[],body=node('body');
const sandbox={window:{openProblemPanels:async()=>{}},node,button:(text,onclick,cls)=>Object.assign(node('button',text,cls),{onclick}),
 fullscreenButton:node('button','全屏选图'),p:{pages:{P001:{id:'P001',panels:[{id:'P001-K01'},{id:'P001-K02'}]}}},
 goNextPanel(id){moves.push(id);return false;},updateUnselectedNavigation(){},currentPage:'P001',ctx:{activePanel:'P001-K02'},problemItems:new Map(),data:{images:{selections:{}}},
 act:async(action,request)=>{calls.push({action,request});return {items:request.marked===false?[]:[{panel_id:request.panel_id}]};},notify(){}};
const source=fs.readFileSync('web/drawing.js','utf8'),begin=source.indexOf('  const markProblemButton ='),end=source.indexOf('  const pagePicker =',begin);
vm.createContext(sandbox);vm.runInContext(source.slice(begin,end)+'\nglobalThis.controls={markProblemButton,pageActions,updatePageRedraw};',sandbox);
const {markProblemButton:button,pageActions,updatePageRedraw}=sandbox.controls;
(async()=>{
 updatePageRedraw();assert.equal(button.disabled,false);
 assert.deepEqual(pageActions.children.slice(0,2).map(b=>b.textContent),['全屏选图','标记有问题']);
 await button.onclick();assert.equal(calls[0].action,'save_problem');assert.equal(calls[0].request.panel_id,'P001-K02');assert.equal(calls[0].request.marked,true);
 assert.deepEqual(moves,['P001-K02']);assert.match(button.textContent,/已标记/);await button.onclick();assert.equal(moves.length,1);assert.equal(calls[1].request.marked,false);
 assert.equal(button.textContent,'标记有问题');
 sandbox.ctx.activePanel='missing';updatePageRedraw();assert.equal(button.disabled,true);await assert.rejects(button.onclick(),/选择/);
 console.log('Problem marking scope checks passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
