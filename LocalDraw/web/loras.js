"use strict";
const loraModelFolders = new Set(["anime","sdxl","krea2","moss_image","qwen_edit"]);
const LORA_WEIGHT_STORAGE_KEY = "local-draw-lora-weights-v1";
window.savedLoraWeights = () => {
  try { return JSON.parse(localStorage.getItem(LORA_WEIGHT_STORAGE_KEY) || "{}") || {}; } catch { return {}; }
};
window.rememberLoraWeight = (file, value) => {
  const w = Number(value);
  if (!file || !Number.isFinite(w) || w < 0 || w > 2) return;
  try {
    const map = window.savedLoraWeights();
    map[file.replaceAll("\\", "/")] = w;
    localStorage.setItem(LORA_WEIGHT_STORAGE_KEY, JSON.stringify(map));
  } catch {}
};
window.defaultLoraWeight = (file, data, fallback=0.7) => {
  const key = String(file || "").replaceAll("\\", "/");
  const remembered = window.savedLoraWeights()[key];
  if (typeof remembered === "number" && Number.isFinite(remembered)) return remembered;
  const labelW = data?.lora_labels?.[key]?.weight;
  if (typeof labelW === "number" && Number.isFinite(labelW)) return labelW;
  return fallback;
};
window.loraDisplayName = file => {
  const parts = file.replaceAll("\\", "/").split("/");
  const stem = parts.at(-1).replace(/\.[^.]+$/, "");
  return parts.length === 2 && loraModelFolders.has(parts[0]) ? stem : stem.split("_", 1)[0];
};
window.loraVariant = file => {
  const folders=file.replaceAll("\\", "/").split("/").slice(0,-1);
  if(folders.some(part=>/^cos\s*服装$/i.test(part))) return {kind:"cos",label:"Cos 服装版",note:"侧重 Cos 服装与装扮；还原角色本人请优先选角色版。"};
  if(folders.includes("角色")) return {kind:"character",label:"角色版",note:"侧重角色外观与辨识特征。"};
  return {kind:"",label:"",note:""};
};
window.loraChoiceName = file => [loraDisplayName(file),loraVariant(file).label].filter(Boolean).join(" · ");
window.resolveLoraFile = (file, data) => data.comfy?.lora_path_aliases?.[file.replaceAll("\\", "/")] || file.replaceAll("\\", "/");
window.normalizeLoraSearch = value => value.toLowerCase().normalize("NFKD").replace(/\p{M}/gu, "").replaceAll("ł", "l").replace(/[^\p{L}\p{N}]/gu, "");
window.loraMatchesSearch = (file, info, query) => {
  const q = normalizeLoraSearch(query);
  // Never include item labels, owners, folders or the original filename suffix.
  return !q || [loraDisplayName(file), info?.pinyin || "", ...(info?.english_aliases || [])]
    .some(term => normalizeLoraSearch(term).includes(q));
};
// One compact picker shared by story settings and direct prompt drawing.
// Rows are mounted lazily into the DOM so thousands of LoRAs open instantly.
window.createLoraPicker = function(host, data, items, characters, changed, prefix="", selectedOnly=false, onBuildRow=null) {
  const box=node("section",undefined,"lora-library"), tools=node("div",undefined,"lora-tools");
  const search=node("input"), folder=node("select"), filter=node("select"), total=node("small");
  search.type="search";search.placeholder="搜索中文名、拼音或英文别名";search.setAttribute("aria-label","搜索 LoRA");
  folder.setAttribute("aria-label","LoRA 文件夹");filter.setAttribute("aria-label","LoRA 筛选");
  const add=(select,value,label)=>{const o=node("option",label);o.value=value;select.append(o);};
  let characterById=new Map(), characterByName=new Map();
  function indexCharacters(cards) {
    characters=cards;
    characterById=new Map(cards.map(c=>[c.id,c]));
    characterByName=new Map(cards.map(c=>[c.name,c]));
  }
  indexCharacters(characters);
  function populateOwner(select, value, expanded=false) {
    select.replaceChildren(); add(select,"","全图风格");
    if (expanded) {
      for(const c of characters) add(select,c.id,"角色 · "+c.name);
      if(value && !characterById.has(value)) add(select,value,"需重新关联 · 角色已移除");
    } else if (value) {
      const hit=characterById.get(value);
      add(select,value,hit?"角色 · "+hit.name:"需重新关联 · 角色已移除");
    }
    select.value=value || "";
    select.dataset.expanded=String(expanded);
  }
  function lazyOwner(select, value) {
    populateOwner(select,value,false);
    const expand=()=>{if(select.dataset.expanded!=="true")populateOwner(select,select.value,true);};
    select.addEventListener("focus",expand);
    select.addEventListener("pointerdown",expand);
  }
  let activeDirectory=null, activeCheckpoint=data.comfy.configured;
  for(const [v,t] of [["all","全部"],["role-version","角色版"],["cos-version","Cos 服装版"],["selected","已勾选"],["character","已关联角色"],["style","全图风格"],["compatible","结构兼容"]]) add(filter,v,t);
  const reload=button("刷新列表",async()=>{await preserve();location.reload();});
  tools.append(search,folder,filter,total,reload);
  if(!selectedOnly)box.append(tools);
  const list=node("div",undefined,"lora-compact-list"), rows=[];
  let page=0, pageSize=4, saving=Promise.resolve(), saveError=null;
  const saveStatus=node("small");
  function saveLabel(item) {
    if(item.character_id?.startsWith("project:")) return;
    const snapshot={...item,checkpoint:activeCheckpoint};
    saving=saving.then(async()=>{
      try {
        await act("save_lora_labels",{items:[snapshot]});
        data.lora_labels ||= {};data.lora_labels[item.file]={...snapshot};
        saveError=null;saveStatus.textContent="角色关联与标注已保存，所有出图入口共用。";
      } catch(e) {saveError=e;saveStatus.textContent="保存失败："+e.message;}
    });
  }
  for(const item of items) {
    item.name = loraDisplayName(item.file);
    const info=data.lora_inspection?.[item.file], label=data.lora_labels?.[item.file]||{};
    if("character_id" in label) Object.assign(item,{character_id:label.character_id,character:label.character});
    if(!("character_id" in item)) {
      const old=item.characters?.[0]||item.character||(info?.is_character?item.name:"");
      item.character_id=item.characters?.length>1?"unresolved":(characterByName.get(old)?.id || (old?"unresolved":""));
    }
    const variant=loraVariant(item.file), choiceName=loraChoiceName(item.file);
    const disabled=!["Anima","SDXL","Krea2","MOSS-Image"].includes(info?.family)&&info?.structurally_compatible!==true;
    if(disabled) item.selected=false;
    item.incompatible=disabled;
    const state={checked:!!item.selected&&!disabled, disabled, weight:String(item.weight??window.defaultLoraWeight(item.file,data,0.7)), owner:item.character_id||"", confirmed:!!item.confirmed};
    let mounted=null;
    function mount() {
      if(mounted) return mounted;
      const row=node("div",undefined,"lora-compact-row"), choice=node("label"), check=node("input"), title=node("span",item.name);
      const identity=node("span",undefined,"lora-identity");
      row.dataset.loraVariant=variant.kind;
      check.setAttribute("aria-label",choiceName);
      check.type="checkbox";check.checked=state.checked;check.disabled=state.disabled;
      if(prefix)check.dataset.editKey=prefix+"use-"+item.file;
      identity.append(title);
      if(variant.label)identity.append(node("small",variant.label,"lora-variant lora-variant-"+variant.kind));
      choice.append(check,identity);
      if(item.file.includes("/"))choice.append(node("small",item.file.split("/").slice(0,-1).join("/"),"lora-folder"));
      choice.title=[choiceName,variant.note,item.file,state.disabled?(info?.note||"结构未确认"):""].filter(Boolean).join("\n");
      const weight=node("input");weight.type="number";weight.min="0";weight.max="2";weight.step="0.05";weight.value=state.weight;
      weight.setAttribute("aria-label",choiceName+" 强度");if(prefix)weight.dataset.editKey=prefix+"weight-"+item.file;
      const owner=node("select");owner.setAttribute("aria-label",choiceName+" 应用到");
      lazyOwner(owner,state.owner);
      const details=node("details"), summary=node("summary","编辑");details.append(summary,node("small",item.file));
      if(variant.label)details.append(node("p",variant.label+"："+variant.note));
      const name=labeledInput(details,"名称",false,item.name),trigger=labeledInput(details,"触发词",false,item.trigger_words||"");
      delete name.dataset.editKey;delete trigger.dataset.editKey;
      details.append(node("small",info?.note||"结构尚未确认"));
      const compat=node("label","确认适配当前底模"),confirmed=node("input");confirmed.type="checkbox";confirmed.checked=state.confirmed;confirmed.disabled=state.disabled;compat.prepend(confirmed);details.append(compat);
      const notifyChange=()=>{state.checked=check.checked;state.weight=weight.value;item.selected=check.checked;item.weight=Number(weight.value);changed?.(item);refresh();};
      check.onchange=notifyChange;
      weight.onchange=()=>{window.rememberLoraWeight(item.file,weight.value);notifyChange();};
      weight.addEventListener("input",()=>window.rememberLoraWeight(item.file,weight.value));
      owner.onchange=()=>{
        state.owner=owner.value;
        item.character_id=owner.value;item.character=characterById.get(owner.value)?.name||"";
        item.characters=item.character?[item.character]:[];item.scope=owner.value?"characters":"story";
        if(owner.value && !check.disabled){check.checked=true;state.checked=true;}
        saveLabel(item);notifyChange();
      };
      name.readOnly=true;name.title=item.file;
      trigger.onchange=()=>{item.trigger_words=trigger.value;saveLabel(item);notifyChange();};
      confirmed.onchange=()=>{state.confirmed=confirmed.checked;item.confirmed=confirmed.checked;notifyChange();};
      if(selectedOnly){details.append(owner);row.append(choice,weight,details);}
      else row.append(choice,weight,owner,details);
      if(selectedOnly && r.orderIndex!=null) row.style.order=r.orderIndex;
      list.append(row);
      mounted={row,check,weight,owner,confirmed,trigger,title,details};
      onBuildRow?.(r, details);
      return mounted;
    }
    const r={
      file:item.file, item, variant, orderIndex:null,
      get mounted(){return !!mounted;},
      mount,
      get row(){return mount().row;},
      check:{
        get checked(){return mounted?mounted.check.checked:state.checked;},
        set checked(v){state.checked=!!v;if(mounted)mounted.check.checked=state.checked;},
        get disabled(){return mounted?mounted.check.disabled:state.disabled;},
        set disabled(v){state.disabled=!!v;if(mounted)mounted.check.disabled=state.disabled;},
        click(){mount().check.click();},
        focus(){mount().check.focus();},
      },
      weight:{
        get value(){return mounted?mounted.weight.value:state.weight;},
        set value(v){state.weight=String(v);if(mounted)mounted.weight.value=state.weight;},
        focus(){mount().weight.focus();},
      },
      owner:{
        get value(){return mounted?mounted.owner.value:state.owner;},
        set value(v){state.owner=v||"";if(mounted)populateOwner(mounted.owner,state.owner);},
        onchange(){if(mounted)mounted.owner.onchange();},
      },
      confirmed:{
        get checked(){return mounted?mounted.confirmed.checked:state.confirmed;},
        set checked(v){state.confirmed=!!v;if(mounted)mounted.confirmed.checked=state.confirmed;},
        get disabled(){return mounted?mounted.confirmed.disabled:state.disabled;},
        set disabled(v){if(mounted)mounted.confirmed.disabled=!!v;},
      },
      sync(){
        state.checked=!!item.selected&&!state.disabled;state.weight=String(item.weight);
        state.owner=item.character_id||"";state.confirmed=!!item.confirmed;
        if(mounted){
          mounted.check.checked=state.checked;mounted.weight.value=state.weight;
          populateOwner(mounted.owner,state.owner);mounted.confirmed.checked=state.confirmed;
          mounted.trigger.value=item.trigger_words||"";mounted.title.textContent=item.name;
        }
      },
    };
    rows.push(r);
  }
  // Sort the visible choices independently of the saved LoRA application order.
  const characterFolders = new Set(["原神", "明日方舟", "Ave Mujica", "Girls Band Cry", "角色", "热门角色", "其他角色"]);
  const generalRows = [], characterRows = [];
  for (const r of rows) {
    const folders = r.file.replaceAll("\\", "/").split("/").slice(0,-1);
    const category = r.variant.kind || folders.some(name => characterFolders.has(name));
    (category ? characterRows : generalRows).push(r);
  }
  const displayRows = [...generalRows, ...characterRows];
  if (selectedOnly) displayRows.forEach((r, index) => { r.orderIndex = index; });
  const navigation=node("div",undefined,"lora-tools lora-navigation"),pageLabel=node("small");
  const prev=button("上一页",()=>{page--;refresh();}),next=button("下一页",()=>{page++;refresh();});
  navigation.append(prev,pageLabel,next);
  function refresh() {
    if(selectedOnly){
      for(const r of rows){
        const show=r.check.checked && !r.check.disabled;
        if(show){const el=r.mount().row;el.hidden=false;}
        else if(r.mounted) r.row.hidden=true;
      }
      return;
    }
    const q=search.value.trim().toLowerCase();
    const visible=displayRows.filter(r=>{
      const dir=r.file.includes("/")?r.file.split("/").slice(0,-1).join("/"):"";
      return (!activeDirectory||r.file.startsWith(activeDirectory+"/"))
        && (folder.value==="*"||dir===folder.value||dir.startsWith(folder.value+"/"))
        && loraMatchesSearch(r.file,data.lora_inspection?.[r.file],q)
        && (filter.value==="all"||filter.value==="role-version"&&r.variant.kind==="character"||filter.value==="cos-version"&&r.variant.kind==="cos"||filter.value==="selected"&&r.check.checked||filter.value==="character"&&r.owner.value||filter.value==="style"&&!r.owner.value||filter.value==="compatible"&&!r.check.disabled);
    });
    page=Math.max(0,Math.min(page,Math.ceil(visible.length/pageSize)-1));
    const shown=new Set(visible.slice(page*pageSize,page*pageSize+pageSize));
    for(const r of rows){
      if(shown.has(r)){r.mount().row.hidden=false;}
      else if(r.mounted) r.row.hidden=true;
    }
    for(const [index,r] of [...shown].entries())r.row.style.order=index;
    list.querySelector(".lora-empty")?.remove();
    if (!visible.length) list.append(node("p", rows.length ? "没有匹配的 LoRA" : "暂无 LoRA，可放入文件后刷新列表。", "lora-empty"));
    total.textContent=`${visible.length} 个 · 已勾选 ${rows.filter(r=>r.check.checked).length}`;
    pageLabel.textContent=`${page+1} / ${Math.max(1,Math.ceil(visible.length/pageSize))}`;
    prev.disabled=page===0;next.disabled=(page+1)*pageSize>=visible.length;
  }
  for(const control of [search,folder,filter])control.addEventListener(control===search?"input":"change",()=>{page=0;refresh();});
  const bulk=node("details"),bulkOwner=node("select");bulkOwner.setAttribute("aria-label","批量关联到");
  lazyOwner(bulkOwner,"");
  bulk.append(node("summary","批量关联已勾选项"),bulkOwner,button("应用关联",()=>{
    const selected=rows.filter(r=>r.check.checked);if(!selected.length)throw Error("请先勾选 LoRA");
    for(const r of selected){const m=r.mount();populateOwner(m.owner,bulkOwner.value);m.owner.onchange();}
  }));
  box.append(list);
  if(!selectedOnly){
    box.append(navigation);
    const resize = new ResizeObserver(() => {
      if (!box.isConnected) {resize.disconnect(); return;}
      const capacity = Math.max(1, Math.min(20, Math.floor(list.clientHeight / 48)));
      if (capacity !== pageSize) {page = Math.floor(page * pageSize / capacity); pageSize = capacity; refresh();}
    });
    resize.observe(list);
  }else bulk.append(reload);
  box.append(bulk,saveStatus);host.append(box);refresh();
  const flush=async()=>{await saving;if(saveError)throw saveError;};
  window.flushLoraLibrary=flush;
  const updateCharacters=(cards)=>{
    indexCharacters(cards);
    populateOwner(bulkOwner,bulkOwner.value,bulkOwner.dataset.expanded==="true");
    for(const r of rows){if(r.mounted)populateOwner(r.row.querySelector("select"),r.owner.value,false);}
    refresh();
  };
  const setCheckpoint=(checkpoint)=>{
    activeCheckpoint=checkpoint;
    const family=data.diffusion_models?.[checkpoint]?.family;
    const anima=["anima38","anima","delta"].includes(family)||checkpoint==="Anima-3.8B-v1.1.safetensors";
    const krea=family==="krea2";
    const moss=family==="moss_image";
    const directory=krea?"krea2":moss?"moss_image":anima?"anime":"sdxl";
    if(activeDirectory!==directory){
      activeDirectory=directory;page=0;folder.replaceChildren();
      add(folder,"*",directory+" · 全部文件夹");
      const directories=new Set(items.filter(x=>x.file.startsWith(directory+"/")).map(x=>x.file.split("/").slice(0,-1).join("/")));
      for(const name of [...directories].sort())add(folder,name,name);
    }
    box.dataset.loraDirectory=directory;
    const targetFamily=krea?"Krea2":moss?"MOSS-Image":anima?"Anima":"SDXL";
    for(const r of rows){
      const f=data.lora_inspection?.[r.file]?.family;
      r.check.disabled=!r.file.startsWith(directory+"/")||f!==targetFamily;
      r.confirmed.disabled=r.check.disabled;r.item.incompatible=r.check.disabled;
      if(r.check.disabled){r.check.checked=false;r.item.selected=false;}
    }
    refresh();
  };
  return {rows,displayRows,refresh,flush,updateCharacters,setCheckpoint,sync:()=>{for(const r of rows)r.sync();refresh();}};
};
