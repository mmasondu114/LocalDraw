// This fixture is injected only by DesktopUiTests.exe. It cannot run GPU jobs.
window.testErrors=[];
addEventListener('error',e=>testErrors.push(e.message));
addEventListener('unhandledrejection',e=>testErrors.push(String(e.reason)));
const store=new Map();
Object.defineProperty(window,'localStorage',{value:{getItem:key=>store.get(key)??null,setItem:(key,value)=>store.set(key,String(value)),removeItem:key=>store.delete(key),clear:()=>store.clear()}});
const projects=[
  {id:'fixture-garden',title:'庭院里的午后 · 界面测试',pages:6},
  {id:'fixture-train',title:'末班列车 · 界面测试',pages:4},
  {id:'fixture-light',title:'窗边的光 · 界面测试',pages:1,quick_sample:true},
];
const queue={running:false,items:[],stop_on_error:true};
const options={comfy:{ready:false,configured:'anima-test',checkpoints:['anima-test'],loras:[]},art_presets:{},lora_labels:{},lora_inspection:{},diffusion_models:{'anima-test':{label:'Anima · 界面测试',family:'anima'}},hand_detail:{},comparison_models:[]};
const originalFetch=window.fetch;
window.fetch=async (url,optionsArg)=>{
  const path=new URL(url,'https://localdraw.test/').pathname;
  let result;
  if(path==='/api/bootstrap')result={token:'offline-test',schemas:{},template:{},projects,jobs:[]};
  else if(path==='/api/projects')result=projects;
  else if(path==='/api/creative-drafts')result=[];
  else if(path==='/api/status')result={model:{ready:false,owned:false,file_size_ok:true},jobs:[],queue,features:{night_queue:true}};
  else if(path==='/api/queue')result=queue;
  else if(path==='/api/drawing-options')result=options;
  else if(path==='/api/characters')result={characters:[],fields:[],clothing_policy:{}};
  else if(path==='/api/workspace')result={views:{}};
  else if(path==='/api/action'){
    const action=JSON.parse(optionsArg.body).action;
    if(!['save_workspace','release_prompt'].includes(action))throw Error('Unexpected mutation in UI test: '+action);
    result={};
  }
  else if(path.startsWith('/api/'))throw Error('Missing offline fixture: '+path);
  else return originalFetch(url,optionsArg);
  return new Response(JSON.stringify(result),{status:200,headers:{'Content-Type':'application/json'}});
};
