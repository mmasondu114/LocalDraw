// Exercises the production finished reader without a browser, model, or GPU.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {webcrypto} = require('node:crypto');
class Element {
  constructor(tag, text='', cls='') {Object.assign(this,{tag,textContent:text,className:cls,children:[],dataset:{},attributes:{},disabled:false,classList:{add(){}}});}
  append(...items) {for(const x of items){x.parent=this;this.children.push(x);}}
  replaceChildren(...items) {this.children=[];this.append(...items);}
  setAttribute(k,v) {this.attributes[k]=v;}
  remove() {if(this.parent)this.parent.children=this.parent.children.filter(x=>x!==this);}
  addEventListener() {}
}
const walk=el=>[el,...el.children.flatMap(walk)];
const sandbox={window:{},crypto:webcrypto,TextEncoder,atob,Blob,Response,DecompressionStream,URLSearchParams,Uint8Array,console,fetch:async()=>({ok:true,text:async()=>'<svg/>'}),document:{body:new Element('body')}};
const source=fs.readFileSync('web/drawing.js','utf8');
const marker=source.indexOf('// Manga lettering is a vector layer');
assert(marker>0);
vm.runInNewContext(source.slice(marker),sandbox);
const manga=sandbox.window.mangaLettering;
(async()=>{
await manga.ready();
assert.equal(manga.punctuation('老板，今天不加班。好.嗯．……'),'老板,今天不加班好嗯……');
const measure=(text,size)=>Array.from(text).length*size;
assert.equal(manga.columns('我只做了路线优化。').length,2,'ordinary short dialogue should not become a long single-column bean');
assert.equal(manga.columns('老板，今天禁止加班！').flat().join(''),'老板，今天禁止加班！');
assert.deepEqual(Array.from(manga.wrap('今天很开心！明天也想来。',120,t=>measure(t,30))),['今天很开','心！明天','也想来。']);
for(const style of manga.styles){
  assert(!manga.shape(style.id,200,130).includes('<ellipse'),'tail and outline are one seamless path');
  const pg={reading_direction:'LTR',panels:[{id:'p'}],lettering:[
    {id:'a',panel_id:'p',kind:'dialogue',text:'老板，今天禁止加班！',order:1,bubble_position:'左上'},
    {id:'b',panel_id:'p',kind:'dialogue',text:'我只做了路线优化。',order:2,bubble_position:'右上'},
  ]};
  const ly={width:1600,height:2264,panels:{p:{x:24,y:24,w:1552,h:700}},lettering:{}};
  const out=manga.layer(pg,ly,style.id,measure);
  assert.equal(out.warnings.length,0);assert.equal(out.boxes.length,2);
  assert(out.boxes[0].x+out.boxes[0].w<out.boxes[1].x,'opposite corner bubbles leave the picture center clear');
  assert(out.boxes[1].y>=out.boxes[0].y+out.boxes[0].h+20,'opposite speakers follow top-to-bottom dialogue order');
  assert(out.svg.includes('<path d="'));assert(out.svg.includes('data-writing-mode="horizontal-tb"'));assert(out.svg.includes('LXGW Marker Gothic'));
  assert(!out.svg.includes('vertical-rl'));assert(out.svg.includes('data-text-row="0"'));
  pg.lettering[0].text='<>&"';assert(manga.layer(pg,ly,style.id,measure).svg.includes('&lt;&gt;&amp;&quot;'));
  ly.lettering.a={x:123,y:95};assert.equal(manga.layer(pg,ly,style.id,measure).boxes[0].x,123);
  ly.lettering.a={x:1590,y:95};assert.throws(()=>manga.layer(pg,ly,style.id,measure),/超出页面/);
}
{
  const page={reading_direction:'LTR',panels:[{id:'p',characters:[{character_id:'doctor',position:'画面右侧'}]}],lettering:[{id:'a',panel_id:'p',kind:'dialogue',text:'我只做了路线优化。',speaker_id:'doctor',order:1,bubble_position:'上方留白'}]};
  const layout={width:1600,height:2264,panels:{p:{x:24,y:24,w:1552,h:800}},lettering:{}};
  assert(manga.layer(page,layout,'auto',measure).boxes[0].x>1000,'fallback follows the known speaker side');
  const plan={a:{form:'thought',anchor:'top-left',tail:'none',columns:['我只做了','路线优化。'],weight:'quiet'}};
  const planned=manga.layer(page,layout,'auto',measure,plan);
  assert(planned.boxes[0].x<100,'a validated per-line AI placement is applied');
  assert(!planned.svg.includes('stroke-dasharray'));
  assert(planned.svg.includes('font-size="25"'),'AI quiet weight is applied');
  assert(!planned.svg.includes('aria-label="。"'),'saved AI columns also remove periods');
  assert(planned.svg.includes('aria-label="我只做了路线优化"'),'displayed text follows punctuation preference');
}
{
  const page={reading_direction:'LTR',panels:[{id:'p',characters:[{character_id:'doctor',position:'Left, beside the left chair'},{character_id:'exusiai',position:'Right of table'}]}],lettering:[
    {id:'ex',panel_id:'p',kind:'dialogue',speaker_id:'exusiai',text:'被你发现秘密分店了。',order:1,bubble_position:'Upper-left clear space; tail toward Exusiai on the right'},
    {id:'dr',panel_id:'p',kind:'dialogue',speaker_id:'doctor',text:'苹果派物流现在还负责端茶送餐了？',order:2,bubble_position:'Upper-right clear space; tail toward Doctor on the left'},
  ]};
  const layout={width:1600,height:2264,panels:{p:{x:24,y:24,w:1552,h:902}},lettering:{}};
  const out=manga.layer(page,layout,'auto',measure);
  assert.equal(out.warnings.length,0);
  assert(out.boxes[0].x>800,'English right-side speaker overrides contradictory upper-left hint');
  assert(out.boxes[1].x<400,'English left-side speaker stays on the left');
  assert(out.boxes[0].w>out.boxes[0].h,'short horizontal speech uses a wide balloon');
  const plan={ex:{form:'speech',anchor:'bottom-right'},dr:{form:'speech',anchor:'top-left'}};
  const ordered=manga.layer(page,layout,'auto',measure,plan);
  assert.equal(ordered.boxes[0].y,46,'AI bottom anchor cannot put the first line below later dialogue');
  assert(ordered.boxes[1].y>=ordered.boxes[0].y+ordered.boxes[0].h+20);
  layout.lettering.dr={x:100,y:24};
  const manual=manga.layer(page,layout,'auto',measure,plan);
  assert.equal(manual.boxes[1].x,100,'manual horizontal placement is retained');
  assert(manual.boxes[1].y>=manual.boxes[0].y+manual.boxes[0].h+20,'manual positions cannot reverse dialogue order');
  layout.lettering={};
  page.panels[0].characters=[];
  assert(manga.layer(page,layout,'auto',measure).boxes[0].x<400,'off-panel speaker can use English placement hint');
}
manga.render=async(source,page,layout,style)=>({png:'data:image/png;base64,'+style,svg:'data:image/svg+xml,'+style,warnings:[]});
const helpers=sandbox.window.finishedComicHelpers;
const fixture=()=>{
  const pages={P001:{id:'P001',number:1,panels:[{id:'P001-K01'}],lettering:[{text:'你好'}]},P002:{id:'P002',number:2,panels:[{id:'P002-K01'}],lettering:[]}};
  const data={images:{selections:{}},panel_hashes:{},layouts:{}};
  for(const p of Object.values(pages)){
    const id=p.panels[0].id;
    data.panel_hashes[id]='hash-'+id;
    data.images.selections[id]={panel_hash:data.panel_hashes[id],image:{id:id+'-1',file:id+'/1.png'}};
    data.layouts[p.id]={width:1600,height:2264,panels:{[id]:{x:24,y:24,w:1500,h:2100}},lettering:{}};
  }
  return {pages,data};
};
async function render({pages,data,cache={},layoutFields={},failPage=null,current=()=>true,onCompose=null}){
  const root=new Element('section');const calls=[];let saved,prints=0;
  sandbox.window.print=()=>prints++;
  const ctx={projectId:'book',cache,layoutFields,current,
    node:(...args)=>new Element(...args),button:(text,fn,cls)=>{const e=new Element('button',text,cls);e.onclick=fn;return e;},
    saveCache:value=>{saved=value;},goDrawing:async()=>{},
    api:async(url,body)=>{
      if(url.startsWith('/api/project'))return {project:{pages,brief:{title:'测试作品'}}};
      if(url.startsWith('/api/drawing'))return data;
      if(url.startsWith('/api/bubble-plan'))return {pages:{},stale_pages:[]};
      assert.equal(body.action,'compose','finished view must never call a generation action');
      assert.equal(body.project,'book');calls.push(body);
      if(onCompose)await onCompose(body);
      if(body.target===failPage)throw Error('test render failure');
      return {bundle:'bundle-'+body.target,files:['page.png','page.svg'],warnings:[]};
    }};
  await sandbox.window.renderFinishedComic(root,ctx);
  return {root,calls,get saved(){return saved;},get prints(){return prints;}};
}
(async()=>{
  let f=fixture();
  let result=await render(f);
  assert.equal(result.calls.length,2);
  const aiButton=walk(result.root).find(e=>e.textContent==='让本地 AI 逐句选择气泡');
  assert.equal(aiButton.disabled,true,'game mode blocks model starts by default');
  await aiButton.onclick();assert.equal(result.calls.length,2,'even directly invoking the disabled handler cannot start a model');
  const print=walk(result.root).find(e=>e.textContent==='打印 / 保存整本 PDF');
  assert.equal(print.disabled,true,'printing waits for all page images');
  for(const img of walk(result.root).filter(e=>e.className==='finished-page-image'))img.onload();
  assert.equal(print.disabled,false);print.onclick();assert.equal(result.prints,1);
  assert.equal(walk(result.root).filter(e=>e.tag==='a').length,4);
  const cached=result.saved;
  const choice=walk(result.root).find(e=>e.dataset.style==='monologue');await choice.onclick();
  assert.equal(result.calls.length,2,'changing bubble style reuses artwork without drawing or recomposing');
  assert(walk(result.root).filter(e=>e.tag==='a').every(a=>a.href.endsWith('monologue')),'downloads use the selected style');
  result=await render({...f,cache:cached});assert.equal(result.calls.length,0,'unchanged pages reuse their composed result');
  f.data.images.selections['P001-K01'].image.id='replacement';
  result=await render({...f,cache:cached});assert.deepEqual(result.calls.map(x=>x.target),['P001']);
  f.pages.P002.lettering=[{text:'换了对白'}];
  result=await render({...f,cache:cached});assert.deepEqual(result.calls.map(x=>x.target),['P002']);
  f=fixture();delete f.data.images.selections['P002-K01'];
  result=await render(f);assert.equal(result.calls.length,1);
  assert(walk(result.root).some(e=>e.textContent.includes('P002-K01')));
  assert.equal(walk(result.root).find(e=>e.textContent==='打印 / 保存整本 PDF').disabled,true);
  f=fixture();f.data.images.selections['P001-K01'].panel_hash='old';
  result=await render(f);assert.deepEqual(result.calls.map(x=>x.target),['P002'],'old storyboard selections are not silently accepted');
  f=fixture();result=await render({...f,failPage:'P001'});
  assert.equal(result.calls.length,2,'one bad page must not block later pages');
  assert(walk(result.root).some(e=>e.textContent.includes('test render failure')));
  f=fixture();let alive=true;
  result=await render({...f,current:()=>alive,onCompose:async()=>{alive=false;}});
  assert.equal(result.calls.length,1);assert.equal(result.saved,undefined,'leaving the project cannot write its cache into another project');
  f=fixture();const invalidFields={'layout-P001':JSON.stringify({width:1600,height:2264,panels:{OLD:{}}})};
  const chosen=helpers.layout(f.pages.P001,invalidFields,f.data.layouts.P001);
  assert.equal(chosen.value,f.data.layouts.P001);assert(chosen.note);
  const custom=structuredClone(f.data.layouts.P001);custom.panels['P001-K01'].fit='contain';
  result=await render({...f,layoutFields:{'layout-P001':JSON.stringify(custom)}});
  assert.equal(result.calls[0].layout.panels['P001-K01'].fit,'contain');
  const oldCache={P006:{signature:'obsolete',result:{bundle:'removed'}}};
  result=await render({...f,cache:oldCache});assert(!Object.hasOwn(result.saved,'P006'),'removed pages never reappear in the reader');
  console.log('Finished reader: selection, stale versions, cache invalidation, layout, print readiness, failure recovery, and navigation checks passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});

})().catch(error=>{console.error(error);process.exitCode=1;});
