"use strict";

function openPromptBoard() {
  const dialog=node("dialog",undefined,"board-start"), form=node("form");
  dialog.setAttribute("aria-label","创建分镜作品");
  const title=labeledInput(form,"作品标题",false,""), story=labeledInput(form,"创作分镜",true,"");
  title.required=story.required=true; title.maxLength=120; story.maxLength=200000; story.rows=9;
  story.placeholder="P001-K01 | 16:9 | 雨后的街道，远景\nP001-K02 | 1:1 | 主角回头，近景\nP002-K01 | 2:3 | 主角推开店门";
  const hint=node("small","每行一格，可写「页格编号 | 比例 | 分镜内容」，也可以直接写画面描述。");
  story.parentElement.append(hint);
  const status=node("p");status.setAttribute("role","alert");
  const submit=node("button","进入提示词出图","primary");submit.type="submit";
  const actions=node("div",undefined,"dialog-actions");actions.append(button("取消",()=>dialog.close()),submit);
  const key=crypto.randomUUID().replaceAll("-","");
  form.append(status,actions);dialog.append(node("h2","创建分镜作品"),form);document.body.append(dialog);
  form.onsubmit=async event=>{
    event.preventDefault();if(submit.disabled)return;
    submit.disabled=true;
    try{
      await preserve();
      const saved=await act("create_prompt_board",{title:title.value,storyboard:story.value,import_key:key});
      await openProject(saved.project,true,"prompt");
      window.leaveStudioHome();dialog.close();
    }catch(error){status.textContent=error.message;}
    finally{submit.disabled=false;}
  };
  dialog.onclose=()=>dialog.remove();
  dialog.showModal();title.focus();
}

function boardPanelId(value) {
  const match=String(value||"").trim().match(/^P(\d{1,3})\s*[- ]\s*K(\d{1,2})$/i);
  return match ? "P"+match[1].padStart(3,"0")+"-K"+match[2].padStart(2,"0") : "";
}

function pastedBoardPrompt(text) {
  if (/^\s*(?:\{|\[\s*\{)/.test(text)) {
    let row;
    try {row=JSON.parse(text);} catch {throw Error("粘贴的 JSON 格式有误。");}
    if (Array.isArray(row)) {
      if(row.length!==1) throw Error("每次粘贴一格提示词。");
      row=row[0];
    }
    const id=boardPanelId(row?.id || row?.panel_id), positive=row?.visual_prompt ?? row?.positive ?? row?.prompt;
    const negative=row?.negative ?? row?.negative_prompt ?? "";
    if(!id || typeof positive!=="string" || typeof negative!=="string") throw Error("需要页格编号和本格提示词，例如 id 和 visual_prompt。");
    return {id,positive,negative};
  }
  const match=text.match(/^\s*(P\d{1,3}\s*[- ]\s*K\d{1,2})\s*(?:[|｜:：]\s*|\s+)([\s\S]+)$/i);
  return match ? {id:boardPanelId(match[1]),positive:match[2]} : null;
}

async function renderBoardLayout(root, project) {
  const pane=node("section",undefined,"board-layout"), summary=node("p"), pages=node("div"), actions=node("div",undefined,"compact-actions");
  root.replaceChildren(pane);summary.setAttribute("role","status");
  const back=button("返回逐格出图",async()=>{await preserve();stage="prompt";target="";await renderStage();});
  const url="/api/board-layout?id="+encodeURIComponent(project);
  const state=await api(url);if(!pane.isConnected)return;
  const missing=state.flatMap(p=>p.missing);
  summary.textContent=missing.length ? "尚缺 "+missing.length+" 格，请先采用图片："+missing.join("、") : "全部画格已采用，可调整每页版式并生成整图。";
  const choices=[];
  for(const page of state){
    const section=node("section",undefined,"workspace-pane board-page"), fields=node("div",undefined,"compact-actions");
    function choice(label,options,initial){
      const wrap=node("label",label), select=node("select");for(const [v,t] of options)select.add(new Option(t,v));
      select.value=initial;wrap.append(select);fields.append(wrap);return select;
    }
    const preset=choice("版式",[["story","按分镜"],["balanced","均衡"],["hero","首格突出"],["cinema","宽银幕"],["vertical","纵向排列"]],page.preset);
    const fit=choice("图片适配",[["contain","完整显示"],["cover","裁切铺满"]],page.fit);
    const output=node("div");
    function show(bundle){
      output.replaceChildren();if(!bundle)return;
      const src="/api/layout?"+new URLSearchParams({id:project,bundle,file:"page.png"});
      const image=node("img");image.src=src;image.alt=page.id+" 整图";image.loading="lazy";
      const download=node("a","下载 "+page.id+" 整图");download.href=src;download.download=page.id+".png";
      output.append(image,download);
    }
    show(page.bundle);preset.onchange=fit.onchange=()=>show(null);
    choices.push({page,preset,fit,show});
    section.append(node("h3",page.id),fields,output);pages.append(section);
  }
  const generate=button("生成整图",async()=>{
    for(const {preset,fit} of choices)preset.disabled=fit.disabled=true;
    try{
      for(const {page,preset,fit,show} of choices){
        summary.textContent="正在生成 "+page.id;
        const r=await api("/api/action",{action:"compose_board_page",project,target:page.id,signature:page.signature,preset:preset.value,fit:fit.value});
        show(r.bundle);
      }
      summary.textContent="已生成 "+state.length+" 页整图，可逐页下载。";
    }catch(error){summary.textContent=error.message;}
    finally{for(const {preset,fit} of choices)preset.disabled=fit.disabled=false;}
  },"primary");
  generate.disabled=!!missing.length;actions.append(back,generate);
  pane.append(node("h2","整图修改"),summary,actions,pages);
}
