"use strict";
try{document.body.dataset.textSize=localStorage.getItem("studio-text-size")==="large"?"large":"standard";}catch{}
window.leaveStudioHome = ()=>{const home=document.getElementById("studio-home");if(home)home.hidden=true;document.body.classList.remove("at-home");};
window.afterQueuedTask = async result=>{
  window.lastAddedQueueItem=result?.queued;
  if(!window.queueComposerActive)return;
  window.queueComposerActive=false;await window.openStudioQueue();
  const card=[...document.querySelectorAll("[data-queue-item]")].find(el=>el.dataset.queueItem===result?.queued);
  if(card){card.scrollIntoView({block:"nearest"});card.focus({preventScroll:true});}
};
window.openStudioDisplay = function() {
  const dialog=node("dialog",undefined,"display-settings-dialog");dialog.setAttribute("aria-label","界面显示");
  const title=node("div",undefined,"queue-heading");title.append(node("h2","让工作台看着更舒服"),button("关闭显示设置",()=>dialog.close()));
  dialog.append(title,node("p","选择适合你的字号。偏好会记住，下次打开继续使用。"));
  const options=node("div",undefined,"display-options");
  for(const [value,label,description] of [["standard","标准字号","同屏显示更多内容"],["large","大字号","放大文字和操作按钮，阅读更轻松"]]){
    const choose=button(label,()=>{
      document.body.dataset.textSize=value;cacheLocal("studio-text-size",value);
      for(const b of options.querySelectorAll("button"))b.setAttribute("aria-pressed",String(b.dataset.value===value));
    });choose.dataset.value=value;choose.setAttribute("aria-pressed",String(document.body.dataset.textSize===value));
    const card=node("div");card.append(choose,node("p",description));options.append(card);
  }
  dialog.append(options);
  const modelLabel=node("label","分析模型"), model=node("select"), info=node("p");
  modelLabel.append(model); model.disabled=true; info.setAttribute("role","status");
  dialog.append(modelLabel,info);
  function renderModel(data) {
    model.replaceChildren();
    for(const item of data.options){
      const option=node("option",item.label);
      option.value=item.id;
      model.append(option);
    }
    model.value=data.selected;
    const chosen=data.options.find(item=>item.id===data.selected);
    info.textContent=chosen?.available ? "下次分析时加载所选模型。" : chosen?.reason || "本机模型不可用。";
    model.disabled=false;
  }
  act("analysis_model").then(renderModel).catch(error=>{info.textContent=error.message;});
  model.onchange=async()=>{
    model.disabled=true;
    try {renderModel(await act("set_analysis_model",{model:model.value}));}
    catch(error){
      try {renderModel(await act("analysis_model"));} catch {}
      info.textContent=error.message;
    }
  };
  dialog.onclose=()=>dialog.remove();document.body.append(dialog);dialog.showModal();
};
// The start page leaves the current editor mounted, so returning preserves inputs.
window.showStudioHome = async function({refresh=true} = {}) {
  await reloadProjects(refresh);
  let home = document.getElementById("studio-home");
  if (!home) {home=node("section",undefined,"studio-home");home.id="studio-home";document.body.append(home);}
  home.replaceChildren();home.hidden=false;document.body.classList.add("at-home");
  const leave=window.leaveStudioHome;
  const title=node("div",undefined,"home-intro");
  title.append(node("small","LOCAL DRAW / 创作空间"),node("h1","今天，想画点什么？"),
    node("p","把灵感画出来。从一张图，到一个完整的故事。"));
  const overview=node("div",undefined,"home-overview");
  overview.append(node("strong",String((boot?.projects||[]).length)),node("span","个已保存作品"));
  title.append(overview);
  home.append(title);
  const connection=node("p","暂时连接不到工作室后台，正在自动重连。下方为断开前的记录，恢复后会更新。","warning");connection.id="home-connection";connection.setAttribute("role","alert");connection.hidden=!window.studioDisconnected;home.append(connection);
  const activity=node("section",undefined,"home-activity");activity.id="home-activity";activity.hidden=true;home.append(activity);
  window.updateStudioHomeStatus(window.lastStudioStatus);
  const choices=node("div",undefined,"home-choices");
  function card(number,name,description,label,fn,leaveAfter=true) {
    const box=node("article",undefined,"home-choice");
    const art=document.createElementNS("http://www.w3.org/2000/svg","svg");
    art.setAttribute("viewBox","0 0 160 80");art.setAttribute("aria-hidden","true");art.classList.add("home-card-art");
    const paths={"01":["M22 12h92v56H22z","M30 57l23-24 20 18 16-13 17 19","M124 20l18 18m-18 0l18-18"],"02":["M22 12h116v56H22z","M68 12v56M22 44h46M78 56l22-30 28 30","M32 23h23M32 31h17"],"03":["M30 20h95M30 40h95M30 60h65","M20 16v8M20 36v8M20 56v8","M113 53a15 15 0 1 0 30 0a15 15 0 1 0-30 0M128 44v10l6 4"]};
    for(const value of paths[number]){const path=document.createElementNS(art.namespaceURI,"path");path.setAttribute("d",value);art.append(path);}
    box.append(node("span",number,"home-number"),art,node("h2",name),node("p",description),button(label,async()=>{
      await fn();
      // A failed first editor load must not expose the uninitialized shell.
      if(leaveAfter && document.body.dataset.stage)leave();
    },"primary"));
    choices.append(box);
  }
  card("01","提示词出图","写下标题，让模型生成分镜。确认后逐格出图、选图并合成漫画页。","创建分镜作品",()=>enterPromptBoard(),false);
  card("02","创作新漫画","写下故事，确认分镜，挑选画面。把每一页慢慢打磨好。","开始一个新故事",async()=>{await $("new-project").onclick();});
  card("03","逐格提示词出图","导入提示词后，选择角色、LoRA 和每格生成张数。","直接导入每格提示词",()=>window.openDirectPromptImport(),false);
  home.append(choices);
  const imports = node("div", undefined, "dialog-actions");
  imports.append(button("直接导入分镜", () => window.openDirectStoryboardImport()),
    button("融合任务", () => openTaskFusion()),
    button("夜间任务列表", () => window.openStudioQueue()));
  home.append(imports);
  const draftShelf=node("section",undefined,"home-recent");
  draftShelf.hidden=true;home.append(draftShelf);
  api("/api/creative-drafts").then(drafts=>{
    if(!draftShelf.isConnected)return;
    const stories=drafts.filter(d=>d.kind==="story_draft").slice(0,3);
    if(!stories.length)return;
    draftShelf.hidden=false;draftShelf.append(node("h2","已生成的剧本草稿"));
    for(const draft of stories){
      const row=node("div",undefined,"home-project");
      row.append(node("strong",draft.payload.brief.title),node("small","草稿 · 打开后可修改和确认"),
        button("打开草稿",async()=>{
          await $("new-project").onclick();
          if(!newMode||stage!=="script")return;
          workspaceState.storyResult=draft;
          workspaceState.views["script:"]={fields:{"story-text":draft.original,"story-mode":draft.mode}};
          await renderStage();leave();notify(`已打开剧本草稿《${draft.payload.brief.title}》，可直接修改并进入下一步。`);
        },"primary"));
      draftShelf.append(row);
    }
  }).catch(()=>{draftShelf.hidden=false;draftShelf.append(node("p","草稿列表暂未读取，请返回首页重试。"));});
  const recent=node("section",undefined,"home-recent");
  const recentHeading=node("div",undefined,"home-section-heading");
  recentHeading.append(node("h2","继续已有作品"),button("管理作品",async()=>$("manage-projects").onclick()));recent.append(recentHeading);
  const search=node("input"),recentList=node("div");search.type="search";search.placeholder="搜索作品，继续上次的创作";search.setAttribute("aria-label","搜索已有作品");
  if((boot?.projects||[]).length>6)recent.append(search);
  const renderRecent=()=>{
  recentList.replaceChildren();
  let lastProject=pid;try{if(!lastProject||lastProject.startsWith("tmp-"))lastProject=localStorage.getItem("studio-last-project");}catch{}
  const all=[...(boot?.projects||[])].sort((a,b)=>Number(b.id===lastProject)-Number(a.id===lastProject)), matches=all.filter(p=>p.title.toLowerCase().includes(search.value.trim().toLowerCase()));
  if(!matches.length)recentList.append(node("p",all.length?"没有找到这个作品，试试其他关键词。":"还没有作品。先选上方任意入口开始，之后可以在这里继续。"));
  for(const project of matches.slice(0,search.value?50:6)) {
    const row=node("div",undefined,"home-project");
    row.append(node("strong",project.title),node("small",(project.id===lastProject?"上次打开 · ":"")+(project.quick_sample?"提示词作品 · 已保存":`${project.pages} 页分镜 · 已保存`)),
      button("继续",async()=>{await preserve();await openProject(project.id);leave();}));
    if(project.id.startsWith("panels-")) row.append(button("出图设置",async()=>{
      const {bundle,stale}=await api("/api/prompt-import?id="+encodeURIComponent(project.id));
      if(!bundle||stale)throw Error("分镜已有变化，请重新导入提示词。");
      await openImportedSetup(project.id,bundle.id,bundle.panels);
    }));
    recentList.append(row);
  }
  if(matches.length>6&&!search.value)recentList.append(node("small",`共 ${all.length} 个作品，输入名称可查找其他作品。`));
  };search.oninput=renderRecent;renderRecent();recent.append(recentList);
  home.append(recent,button("返回刚才的工作台",async()=>{
    await window.resumeStudioWorkspace();leave();
  },"home-back"));
};

window.openStudioQueue = async function() {
  let dialog=document.getElementById("studio-queue-dialog");
  if(dialog){dialog.showModal();return;}
  dialog=node("dialog",undefined,"studio-queue-dialog");dialog.id="studio-queue-dialog";
  dialog.setAttribute("aria-label","夜间任务列表");
  const heading=node("div",undefined,"queue-heading");
  const headingActions=node("div",undefined,"queue-item-actions");
  const addTask=async()=>{
    await reloadProjects();
    const chooser=node("dialog",undefined,"queue-add-dialog");chooser.setAttribute("aria-label","添加任务");
    const top=node("div",undefined,"queue-heading");top.append(node("h2","添加一个创作任务"),button("返回时间轴",()=>chooser.close()));chooser.append(top);
    const promptChoice=node("section",undefined,"queue-add-choice");promptChoice.append(node("h3","提示词出图"),node("p","写一段画面描述，选择模型、张数和 LoRA。每次加入都会成为独立的一张任务卡片；连续添加时保留刚才的设置。"),button("填写提示词任务",async()=>{
      await $("prompt-entry").onclick();window.queueComposerActive=true;window.leaveStudioHome();chooser.close();dialog.close();notify("填好设置后点击「加入任务列表」，新卡片会排在时间轴最后。");
    },"primary"));chooser.append(promptChoice);
    const comicChoice=node("section",undefined,"queue-add-choice"),projectSelect=node("select");projectSelect.setAttribute("aria-label","选择要排队的漫画");
    const comics=(boot?.projects||[]).filter(p=>!p.quick_sample);for(const project of comics){const option=node("option",project.title);option.value=project.id;projectSelect.append(option);}if(comics.some(p=>p.id===pid))projectSelect.value=pid;
    const addComic=button("选择漫画出图范围",async()=>{await preserve();await openProject(projectSelect.value);stage="drawing";target="";await renderStage();window.queueComposerActive=true;window.leaveStudioHome();chooser.close();dialog.close();notify("选好画格和数量后，点击「加入任务列表」。");});addComic.disabled=!comics.length;
    comicChoice.append(node("h3","漫画出图"),node("p",comics.length?"选择已有漫画，再确认要画哪些格子、每格多少张。":"先创建一个漫画并完成分镜，就能在这里安排出图。"));if(comics.length)comicChoice.append(projectSelect);comicChoice.append(addComic);chooser.append(comicChoice);
    chooser.onclose=()=>chooser.remove();document.body.append(chooser);chooser.showModal();
  };
  headingActions.append(button("＋ 添加任务",addTask,"primary"),button("融合任务",()=>openTaskFusion(dialog)),button("关闭",()=>dialog.close()));
  heading.append(node("h2","创作任务时间轴"),headingActions);
  const help=node("p","任务从上往下依次执行。上一项完成，下一项自动开始；用上移、下移调整顺序。","queue-timeline-intro");
  const tips=node("details",undefined,"queue-help");tips.append(node("summary","夜间挂机与设置保存说明"),node("p","加入时保存提示词、模型和 LoRA，后续修改不影响已排好的任务。关闭网页后后台仍会继续，电脑需保持开机且不休眠。重启工作室后列表会保留，需手动开始。"));
  const controls=node("div",undefined,"queue-controls"), label=node("label"), stop=node("input");stop.type="checkbox";stop.checked=true;
  label.append(stop,document.createTextNode("遇到失败先暂停列表，等我检查"));
  const scheduled=node("input"),scheduleLabel=node("label","开始时间（留空立即开始）");scheduled.type="datetime-local";scheduled.setAttribute("aria-label","任务列表开始时间");scheduleLabel.append(scheduled);
  const start=button("开始按顺序执行",async()=>{
    if(!scheduled.checkValidity())throw Error("请填写完整的开始时间，或清空后立即开始。");
    const startAt=scheduled.value?new Date(scheduled.value).getTime()/1000:null;
    if(startAt!==null&&(!Number.isFinite(startAt)||startAt<Date.now()/1000-60))throw Error("请选择尚未到达的开始时间，或留空立即执行。");
    await act("start_queue",{stop_on_error:stop.checked,start_at:startAt});await refreshStatus();
  },"primary");
  const pause=button("做完当前任务后暂停",async()=>{await act("pause_queue");scheduled.value="";await refreshStatus();});
  const scheduleTools=node("div",undefined,"queue-schedule-tools");
  const setSchedule=value=>{scheduled.value=value;scheduled.dispatchEvent(new Event("input"));};
  const later=button("1 小时后",()=>{const date=new Date(Date.now()+3600000);setSchedule(new Date(date.getTime()-date.getTimezoneOffset()*60000).toISOString().slice(0,16));});
  const immediate=button("清空预约时间",()=>setSchedule(""));
  immediate.title="清空预约时间，点击开始执行后才会运行";
  scheduleTools.append(later,immediate);
  const updateStartLabel=()=>{start.textContent=scheduled.value?"确认预约开始":"开始按顺序执行";};
  scheduled.oninput=updateStartLabel;
  controls.append(scheduleLabel,scheduleTools,start,pause,label);
  const summary=node("p",undefined,"queue-summary"), list=node("div",undefined,"queue-list");
  const connection=node("p","后台连接中断，下面是最后一次记录。恢复连接后会自动更新任务状态。","warning");connection.hidden=!window.studioDisconnected;connection.setAttribute("role","alert");
  window.updateStudioQueueConnection=connected=>{connection.hidden=connected;if(!connected){start.disabled=true;pause.disabled=true;}};
  dialog.append(heading,connection,help,controls,summary,list,tips);
  dialog.addEventListener("close",()=>{dialog.remove();window.renderQueueSnapshot=null;window.updateStudioQueueConnection=null;});
  document.body.append(dialog);dialog.showModal();
  let last="";
  const progressNodes=new Map();
  const addProgress=(body,key)=>{
    const message=node("p"),progress=node("progress"),count=node("span",undefined,"queue-progress-count"),eta=node("small",undefined,"queue-eta");
    progress.setAttribute("aria-label","任务完成进度");body.append(message,progress,count,eta);
    progressNodes.set(key,{message,progress,count,eta});
  };
  const updateProgress=data=>{
    const entries=[...(data?.items||[])];
    if(window.activeStudioJob)entries.push({...window.activeStudioJob,id:"current-work"});
    for(const item of entries){
      const parts=progressNodes.get(item.id);if(!parts)continue;
      parts.message.hidden=!item.message;
      if(parts.message.textContent!==(item.message||""))parts.message.textContent=item.message||"";
      parts.progress.hidden=parts.count.hidden=!item.progress;
      if(item.progress){
        const total=item.progress.total||1,completed=item.progress.completed||0;
        if(parts.progress.max!==total)parts.progress.max=total;
        if(parts.progress.value!==completed)parts.progress.value=completed;
        const text=` ${completed} / ${total} ${item.progress.unit||"张已处理"}`;
        if(parts.count.textContent!==text)parts.count.textContent=text;
      }
      const seconds=item.eta?.remaining_seconds;
      const estimate=(item.job_status||item.status)==="running"&&Number.isFinite(seconds)&&seconds>0?`本阶段预计还需 ${Math.ceil(seconds/60)} 分钟` :"";
      parts.eta.hidden=!estimate;if(parts.eta.textContent!==estimate)parts.eta.textContent=estimate;
    }
  };
  window.renderQueueSnapshot=data=>{
    if(data){start.disabled=data.running||!data.items.some(i=>i.status==="waiting");pause.disabled=!data.running;start.dataset.unavailable=String(start.disabled);pause.dataset.unavailable=String(pause.disabled);stop.disabled=data.running;scheduled.disabled=later.disabled=immediate.disabled=data.running;}
    // Progress changes should not replace cards while someone is using their buttons.
    const structure=data&&{...data,items:data.items.map(({progress,message,eta,...item})=>item)};
    const signature=JSON.stringify([structure,window.activeStudioJob?.id,window.activeStudioJob?.status,window.activeStudioJob?.label,window.lastAddedQueueItem,!!(data?.running&&data.start_at>Date.now()/1000)]);
    if(signature===last){updateProgress(data);return;}
    if(!last && data){
      stop.checked=data.stop_on_error;
      if(data.running&&data.start_at>Date.now()/1000){const date=new Date(data.start_at*1000);scheduled.value=new Date(date.getTime()-date.getTimezoneOffset()*60000).toISOString().slice(0,16);}
      updateStartLabel();
    }
    last=signature;
    if(!data){summary.textContent="任务列表需要新版后台。请等当前生成结束，再重新打开工作室以启用。";start.disabled=true;pause.disabled=true;start.dataset.unavailable=pause.dataset.unavailable="true";return;}
    const waiting=data.items.filter(i=>i.status==="waiting");
    const active=data.items.find(i=>i.status==="running");
    pause.textContent=data.running&&data.start_at>Date.now()/1000?"取消预约":"做完当前任务后暂停";
    updateStartLabel();
    start.disabled=data.running||!waiting.length;pause.disabled=!data.running;stop.disabled=data.running;
    summary.textContent=`${active?.job_status==="paused"?"当前任务已暂停":data.running?"自动执行中":active?"当前任务完成后停止":waiting.length?"准备就绪，点击开始执行":"暂无等待任务"} · ${waiting.length} 项等待 · ${data.items.filter(i=>i.status==="done").length} 项完成`;
    if(data.running&&data.start_at>Date.now()/1000)summary.textContent=`已预约 ${new Date(data.start_at*1000).toLocaleString("zh-CN")} 开始 · ${waiting.length} 项等待`;
    const needsAttention=data.items.filter(i=>["failed","partial","interrupted"].includes(i.status)).length;
    if(needsAttention)summary.textContent+=` · ${needsAttention} 项需要处理`;
    if(data.running&&!active&&window.activeStudioJob)summary.textContent+=" · 等待工作台当前任务结束";
    const plannedImages=waiting.reduce((sum,item)=>sum+(item.request.candidate_count||0)*(item.request.action==="draw_imported"?item.request.targets.length:1),0);
    if(plannedImages)summary.textContent+=` · 待画约 ${plannedImages} 张（重试会复用已有图片）`;
    if(data.error)summary.textContent+=" · "+data.error;
    const historyOpen=list.querySelector(".queue-history")?.open||false;
    const focusedKey=document.activeElement?.dataset.queueKey;
    list.replaceChildren();
    progressNodes.clear();
    if(!data.items.length&&!window.activeStudioJob)list.append(node("div","还没有安排任务。点击下方「添加任务」，从一张图或一部漫画开始。","queue-empty"));
    const timeline=node("ol",undefined,"queue-timeline");timeline.setAttribute("aria-label","任务执行时间轴");list.append(timeline);
    const timelineRow=(card,number,timing,state)=>{
      const row=node("li",undefined,"queue-timeline-item"),rail=node("div",undefined,"queue-timeline-rail");row.dataset.state=state;
      rail.append(node("span",number,"queue-timeline-dot"),node("small",timing));row.append(rail,card);timeline.append(row);
    };
    if(!active&&window.activeStudioJob){
      const current=node("article",undefined,"queue-card queue-current-work");current.append(node("small","工作台当前任务","queue-badge"),node("h3",window.activeStudioJob.label),node("p",window.activeStudioJob.status==="paused"?"这项任务已暂停，继续或结束后，排队任务才能开始。":"完成这项任务后，再进入下方时间轴。"));
      addProgress(current,"current-work");
      const currentJob=window.activeStudioJob;current.append(button("查看当前任务进度",async()=>{if(currentJob.project){await preserve();await openProject(currentJob.project);}window.leaveStudioHome();dialog.close();$("jobs").scrollIntoView({block:"start"});}));
      timelineRow(current,"•",window.activeStudioJob.status==="paused"?"等待继续":"正在进行","running");
    }
    const names={waiting:"等待中",running:"正在执行",paused:"已暂停 · 等待继续",pausing:"正在暂停",cancelling:"正在结束",done:"已完成",partial:"部分失败",failed:"失败",cancelled:"已结束",interrupted:"重启后待核对"};
    const history=node("details",undefined,"queue-history");history.open=historyOpen;
    const ended=data.items.filter(i=>["done","cancelled"].includes(i.status));
    if(ended.length){history.append(node("summary",`已结束的任务 · ${ended.length} 项`),node("p","作品和图片仍保存在作品库，清理记录只整理这个列表。"),button("清理已结束记录",async()=>{await act("clear_queue_history");await refreshStatus();}));}
    const rank={running:0,waiting:1,failed:2,partial:2,interrupted:2,done:3,cancelled:3};
    const attention=node("section",undefined,"queue-attention");if(needsAttention)attention.append(node("h3","需要处理的任务"),node("p","重新排队后，这些任务会回到上方执行时间轴。"));
    let timelineOrder=0;
    for(const item of [...data.items].sort((a,b)=>(rank[a.status]??2)-(rank[b.status]??2))){
      const card=node("article",undefined,"queue-card"),actions=node("div",undefined,"queue-item-actions"),body=node("div",undefined,"queue-card-body");
      card.dataset.state=item.status;
      card.dataset.queueItem=item.id;card.tabIndex=-1;
      card.classList.toggle("queue-newly-added",item.id===window.lastAddedQueueItem);
      const stateLabel=(item.id===window.lastAddedQueueItem&&item.status==="waiting"?"刚添加 · ":"")+(names[item.job_status]||names[item.status]||item.status);
      body.append(node("small",stateLabel,"queue-badge"),node("h3",item.label));
      addProgress(body,item.id);
      const recoveryNotes={interrupted:"上次关闭时，这项任务没有确认完成。可先打开作品核对，再重新排队；已有图片会保留。",partial:"部分图片未完成。重新排队会复用已保存的结果，补齐未完成部分。",failed:"请检查上方原因和保存的设置。处理后重新排队，已有结果会保留。"};
      if(recoveryNotes[item.status])body.append(node("p",recoveryNotes[item.status],"queue-recovery-note"));
      const request=item.request,modelName=request.checkpoint_label||request.checkpoint;body.append(node("small",request.action==="draw_imported"?`漫画 · ${request.targets.length} 格 × ${request.candidate_count} 张 · ${modelName}`:request.candidate_count?`提示词出图 · ${request.candidate_count} 张 · ${modelName}`:"提示词与出图设置已保存"));
      if(item.status==="waiting")for(const [delta,text] of [[-1,"上移"],[1,"下移"]]){
        const at=waiting.findIndex(i=>i.id===item.id),move=button(text,async()=>{
          const ids=waiting.map(i=>i.id);[ids[at],ids[at+delta]]=[ids[at+delta],ids[at]];
          await act("reorder_queue",{items:ids});await refreshStatus();
        });move.disabled=at+delta<0||at+delta>=waiting.length;actions.append(move);
      }
      actions.append(button("打开作品",async()=>{await preserve();await openProject(item.project);window.leaveStudioHome();dialog.close();}));
      if(data.can_rename)actions.append(button("改名称",()=>{
        const editor=node("dialog",undefined,"queue-name-dialog");editor.setAttribute("aria-label","修改任务名称");
        const head=node("div",undefined,"queue-heading");head.append(node("h2","给这项任务起个名字"),button("取消",()=>editor.close()));
        const name=node("input"),label=node("label","任务名称");name.value=item.label;name.maxLength=80;name.setAttribute("aria-label","任务名称");label.append(name);
        const save=button("保存名称",async()=>{
          if(!name.value.trim())throw Error("请填写任务名称");
          const updated=await act("rename_queue_item",{item:item.id,task_name:name.value});window.updateStudioQueue(updated);editor.close();notify("任务名称已更新。");
        },"primary");
        editor.append(head,node("p","用来识别时间轴上的这项任务，例如「第一页 · 暖色版」。作品和出图设置继续使用已保存的内容。"),label,save);
        editor.onclose=()=>editor.remove();document.body.append(editor);editor.showModal();name.focus();name.select();
      }));
      if(item.job_status==="paused")actions.append(button("继续这项任务",async()=>{await controlJob({id:item.job},"resume");notify("已继续当前任务；其他等待项按列表的开始状态执行。");},"primary"));
      actions.append(button("查看设置",async()=>{
        const saved=await api("/api/queue-preview?item="+encodeURIComponent(item.id));
        const preview=node("dialog",undefined,"queue-preview-dialog");preview.setAttribute("aria-label","任务设置快照");
        const head=node("div",undefined,"queue-heading");head.append(node("h2",saved.label),button("关闭设置预览",()=>preview.close()));
        preview.append(head,node("p",`加入列表时保存的设置 · ${saved.panels.length} 格 × ${saved.count} 张 · ${saved.checkpoint_label||saved.checkpoint}`));
        for(const row of saved.panels){
          const panel=node("article",undefined,"queue-card");panel.append(node("h3",row.panel_id),node("strong","正向提示词"),node("pre",row.positive),node("strong","负向提示词"),node("pre",row.negative||"未填写"));
          panel.append(node("p","角色 / 风格 LoRA："+(row.loras.map(l=>`${l.label||l.file}（强度 ${l.weight}）`).join("、")||"未选择")));
          if(row.options)panel.append(node("p",`画幅 ${row.options.aspect_ratio} · 一致性辅助 ${row.options.consistency_lora?"开启":"关闭"} · 手部增强 ${row.options.hand_detail?"开启":"关闭"}`));
          if(row.has_reference)panel.append(node("small","已保存固定参考图"));preview.append(panel);
        }
        preview.onclose=()=>preview.remove();document.body.append(preview);preview.showModal();
      }));
      if(["failed","partial","cancelled"].includes(item.status)||(item.status==="interrupted"&&data.can_recover_interrupted))actions.append(button("重新排队",async()=>{
        const updated=await act("retry_queue_item",{item:item.id});window.lastAddedQueueItem=item.id;window.updateStudioQueue(updated);
        const restored=[...list.querySelectorAll("[data-queue-item]")].find(card=>card.dataset.queueItem===item.id);
        restored?.scrollIntoView({block:"nearest"});restored?.focus({preventScroll:true});
        notify(updated.running?"已重新排队，将按时间轴顺序执行。":"已重新排队；准备好后点击「开始按顺序执行」。");await refreshStatus();
      },"primary"));
      if(item.status!=="running")actions.append(button("移出列表",async()=>{await act("remove_queue_item",{item:item.id});await refreshStatus();}));
      for(const actionButton of actions.querySelectorAll("button"))actionButton.dataset.queueKey=item.id+":"+actionButton.textContent;
      card.append(body,actions);
      if(["running","waiting"].includes(item.status)){
        const first=timelineOrder===0&&!window.activeStudioJob;
        const timing=item.status==="running"?(item.job_status==="paused"?"等待继续":"正在执行"):first?(data.running&&data.start_at>Date.now()/1000?new Date(data.start_at*1000).toLocaleString("zh-CN",{month:"numeric",day:"numeric",hour:"2-digit",minute:"2-digit"}) :data.running?"即将开始":"开始后执行"):"上一项完成后";
        timelineRow(card,String(++timelineOrder).padStart(2,"0"),timing,item.status);
      }else (ended.includes(item)?history:attention).append(card);
    }
    const addCard=button("＋ 添加任务 · 排在最后",addTask,"queue-add-card");timelineRow(addCard,"＋","接着安排","add");
    if(attention.querySelector("article"))list.append(attention);
    if(ended.length)list.append(history);
    updateProgress(data);
    if(focusedKey)[...list.querySelectorAll("[data-queue-key]")].find(b=>b.dataset.queueKey===focusedKey)?.focus({preventScroll:true});
  };
  if(window.studioQueueAvailable===false)window.renderQueueSnapshot(null);
  else window.renderQueueSnapshot(await api("/api/queue"));
};
window.updateStudioHomeStatus = data=>{
  if(!data)return;window.lastStudioStatus=data;
  const box=$("home-activity");if(!box)return;
  const active=data.jobs.find(j=>["running","pausing","paused","cancelling"].includes(j.status));
  const waiting=data.queue?.items.filter(i=>i.status==="waiting").length||0;
  const completed=data.queue?.items.filter(i=>i.status==="done").length||0;
  const needsAttention=data.queue?.items.filter(i=>["failed","partial","interrupted"].includes(i.status)).length||0;
  const signature=JSON.stringify([active&&[active.id,active.label,active.status,active.message,active.progress,active.detail_progress],waiting,completed,needsAttention,data.queue?.running,data.queue?.start_at]);
  if(box.dataset.signature===signature)return;box.dataset.signature=signature;
  box.hidden=!active&&!waiting&&!completed&&!needsAttention;box.replaceChildren();
  if(active){
    const auditing=active.status==="running"&&active.request?.action==="storyboard_all"&&active.detail_progress?.unit==="页已核对";
    box.append(node("strong",active.status==="paused"?"有一项任务等你继续":auditing?"分镜已生成，正在核对":"工作室正在创作"),node("p",auditing?"逐页核对完成后即可进入下一步。":active.label+" · "+(active.message||"准备中")));
    const progress=auditing?active.detail_progress:active.progress;
    if(progress)box.append(node("small",`${progress.completed} / ${progress.total} ${progress.unit||"项"}`));
    box.append(button("查看任务进度",async()=>{if(active.project){await preserve();await openProject(active.project);}window.leaveStudioHome();$("jobs").scrollIntoView({block:"start"});}));
  }else if(needsAttention){box.append(node("strong",`${needsAttention} 项任务需要检查`),node("p",`已完成 ${completed} 项${waiting?`，还有 ${waiting} 项等待`:""}。已有结果保留，可在列表查看原因或重新排队。`),button("检查任务与结果",()=>window.openStudioQueue()));
  }else if(waiting){box.append(node("strong",`${waiting} 项创作已排队`),node("p",data.queue.running?(data.queue.start_at>Date.now()/1000?`预约于 ${new Date(data.queue.start_at*1000).toLocaleString("zh-CN")} 开始。`:"等待工作台空闲后依次开始，执行顺序可在列表中调整。") :"列表尚未开始，准备好后可以一起执行。"),button("查看任务列表",()=>window.openStudioQueue()));
  }else if(completed){box.append(node("strong",`${completed} 项创作已经完成`),node("p","图片已经保存，可以逐项打开作品查看结果。"),button("查看完成的结果",()=>window.openStudioQueue()));}
};
window.updateStudioHomeConnection = connected=>{const banner=$("home-connection");if(banner)banner.hidden=connected;};
window.updateStudioQueue = data=>{
  document.querySelectorAll("[data-queue-name-field]").forEach(field=>field.hidden=!data?.can_rename);
  if(data){const count=data.items.filter(i=>["waiting","running"].includes(i.status)).length;const entry=$("queue-entry");(entry.querySelector("span")||entry).textContent=`任务队列${count?" · "+count+" 项":""}`;}
  window.renderQueueSnapshot?.(data);
};
document.addEventListener("DOMContentLoaded",()=>{
  $("display-settings").onclick=safe(()=>window.openStudioDisplay());
  $("check-update").onclick = async () => {
    const dialog = node("dialog", undefined, "display-settings-dialog");
    const status = node("p", "正在查询 GitHub…");
    status.setAttribute("role", "status");
    const label = node("label", "GitHub 只读令牌（此私有仓库的 Contents 读取权限）");
    const token = node("input");
    token.type = "password"; token.autocomplete = "off"; label.append(token); label.hidden = true;
    const check = button("重新检测", async () => {
      check.disabled = true;
      status.textContent = "正在查询 GitHub…";
      try {
        const result = await act("check_update", {token: token.value});
        label.hidden = !result.auth_required;
        status.textContent = result.auth_required ? "当前版本 " + result.current + "。填写只读令牌可比较版本，也可以直接打开发行页。"
          : "当前版本 " + result.current + "，GitHub 最新版 " + result.latest + (result.available ? "，可前往下载。" : "，无需更新。");
      } catch (error) { label.hidden = false; status.textContent = error.message; }
      finally { token.value = ""; check.disabled = false; }
    });
    const actions = node("div", undefined, "dialog-actions");
    actions.append(check, button("打开 GitHub 下载页", safe(() => act("open_releases"))),
      button("关闭", () => dialog.close()));
    dialog.append(node("h2", "检测更新"), status, label, actions);
    dialog.onclose = () => dialog.remove();
    document.body.append(dialog); dialog.showModal(); check.click();
  };
  $("home-entry").onclick=safe(async()=>{await preserve();await window.showStudioHome();});
  $("queue-entry").onclick=safe(()=>window.openStudioQueue());
});
