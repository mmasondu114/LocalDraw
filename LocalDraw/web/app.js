"use strict";
// Browser storage is only a cache; a full quota must never block the UI or
// the authoritative workspace save to the local server.
function cacheLocal(key, value) {
  try { localStorage.setItem(key, value); return true; }
  catch { return false; }
}
const $ = (id) => document.getElementById(id);
const names = {
  brief: "创作需求",
  canon: "角色与世界观",
  options: "多个剧情方案",
  outline: "确认故事大纲",
  plan: "分页计划",
  page: "逐格分镜与对白",
  panel: "单格修改",
  audit: "连续性审核",
  drawing: "绘图与排版",
  title: "标题",
  genre: "题材",
  direction: "故事方向",
  page_count: "页数",
  panels_per_page: "每页格数",
  reading_direction: "阅读方向",
  page_mode: "阅读形式",
  first_story_page_side: "正文首页位置",
  must_include: "必须出现的情节",
  avoid: "避免出现",
  character_material: "角色资料（用户资料优先）",
  characters: "角色",
  scenes: "场景",
  props: "道具",
  world_rules: "世界观规则",
  initial_state: "初始剧情状态",
  id: "稳定编号",
  name: "名称",
  origin: "角色来源",
  source_notes: "资料来源",
  verified_facts: "原作事实",
  adaptation_choices: "改编设定",
  unknowns: "未知事项",
  appearance: "外观锚点",
  voice: "角色口吻",
  sample_lines: "台词样例",
  motivation: "动机",
  taboos: "行为禁区",
  layout: "大小 / 版式",
  landmarks: "固定地标",
  lighting: "光线",
  axis_rule: "镜头轴线",
  description: "描述",
  logline: "一句话故事",
  beats: "完整情节节点",
  ending: "结局",
  tradeoff: "方案取舍",
  selected_option: "选定剧情方案编号",
  premise: "故事前提",
  must_include_mapping: "必需情节对应",
  pages: "页面",
  number: "阅读顺序号",
  scene_ids: "场景编号",
  character_ids: "角色编号",
  panel_count: "本页格数",
  purpose: "剧情作用",
  beat: "本页情节",
  hook: "钩子",
  reveal: "翻页揭晓（无则留空）",
  turn_after: "本页之后是否可翻页",
  constraints: "本页约束",
  page_id: "所属页号",
  page_turn_note: "翻页设计说明",
  state_in: "本页开始状态",
  state_out: "本页结束状态",
  state_after: "本格结束状态",
  panels: "画格",
  lettering: "对白 / 旁白 / 拟声（独立文字层）",
  shot: "景别",
  angle: "视角",
  composition: "构图",
  scene_id: "场景编号",
  character_id: "角色编号",
  position: "位置",
  action: "动作",
  expression: "表情",
  main_instant: "唯一主要瞬间",
  background: "背景",
  prop_id: "道具编号",
  state: "道具状态",
  transition_in: "承接前格",
  transition_out: "衔接后格",
  drawing_description: "中立画面描述（不是模型专用提示词）",
  panel_id: "所属格号",
  kind: "文字类型",
  speaker_id: "说话者编号（旁白 / 拟声留空）",
  text: "文字内容",
  bubble_position: "气泡位置 / 指向",
  order: "气泡阅读顺序",
  verdict: "审核结论",
  issues: "问题",
  severity: "严重程度",
  category: "类别",
  evidence: "具体证据",
  suggestion: "最小修改建议",
  unknown_canon: "原作未知事项",
  reviewed_scope: "本次审核范围",
};
const enumNames = {
  LTR: "从左到右、从上到下",
  RTL: "从右到左、从上到下",
  single_page: "数字单页阅读",
  print_spreads: "纸质跨页",
  right: "右页",
  left: "左页",
  original: "原创角色",
  existing_work: "已有作品角色",
  dialogue: "对白",
  narration: "旁白",
  sfx: "拟声",
  pass: "本次未发现问题",
  needs_revision: "需要修改",
  error: "错误",
  warning: "提醒",
};
const help = {
  brief: "输入故事方向、角色资料、篇幅与必需情节。先确认需求，再建立设定。",
  canon:
    "把用户资料、原作事实、改编设定与未知信息分别记录。设定改动涉及已有剧情时，请先创建分支。",
  options: "至少三个不同剧情方案。确认方案集合后，在大纲阶段选择其中一个。",
  outline: "明确起因、转折和结局；你确认的大纲是后续分页的约束。",
  plan: "每页分配情节、格数和揭晓位置，区分单页阅读与纸质跨页。",
  page: "每格一个可画瞬间。选择某格即可局部修改；整页重做也须保持原有出入状态。",
  audit:
    "结构检查与语义审核分别进行。AI 报告会注明审核范围，只提出问题，不修改原稿。",
  drawing:
    "文字分镜、中立画面描述、候选图片和页面排版分别保存。绘图前自动卸载语言模型。",
};
Object.assign(names, {
  panel: "当前画格",
  gaze: "视线方向 / 目标",
  aspect_ratio: "画格宽高比",
  composition_variants: "同一情节的三种构图",
  drawing_prompt: "绘图适配",
});
let boot,
  snap,
  pid,
  stage = "script",
  target = "",
  value,
  valueBaseHash,
  mode = "form",
  dirty = false,
  newMode = false,
  activeCandidate = null,
  lastJobs = "";
let workspaceState = {views:{}}, restoringWorkspace = false, workspaceTimer;
let stageRender = 0;
// Load the drawing tools (including comic lettering) only when used. Share the
// request across callers and allow a retry after a transient connection error.
let drawingToolsLoading;
window.ensureDrawingTools = function() {
  if (!drawingToolsLoading) {
    drawingToolsLoading = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = "/drawing.js";
      script.onload = resolve;
      script.onerror = () => {
        script.remove();
        drawingToolsLoading = null;
        reject(new Error("绘图工具加载失败，请重试。"));
      };
      document.head.append(script);
    });
  }
  return drawingToolsLoading;
};
const workspaceProject = () => newMode ? "new" : pid;
const workspaceKey = () => `${stage}:${target}`;
function saveWorkspace() {
  if (stage === "prompt") {
    window.savePromptDraft?.();
    if(snap?.project.prompt_board && pid && workspaceState.stage !== stage){
      Object.assign(workspaceState,{stage,target:"",updated:Date.now()});
      cacheLocal("studio-workspace-"+pid,JSON.stringify(workspaceState));
      clearTimeout(workspaceTimer);
      const project=pid,state=structuredClone(workspaceState);
      workspaceTimer=setTimeout(()=>api("/api/action",{action:"save_workspace",project,state}).catch(()=>{}),450);
    }
    return;
  }
  if (pid?.startsWith("tmp-")) return;
  if ((!newMode && (!pid || !snap)) || restoringWorkspace) return;
  const fields={};
  const prior=workspaceState.views?.[workspaceKey()];
  if(prior?.dirty && prior.base_hash!==snap?.hash){workspaceState.recovered ||= [];workspaceState.recovered.push(structuredClone(prior));}
  for(const input of document.querySelectorAll("[data-edit-key]")) fields[input.dataset.editKey]=input.type==="checkbox"?input.checked:input.value;
  workspaceState.views ||= {};
  workspaceState.views[workspaceKey()]={base_hash:dirty?(valueBaseHash||snap?.hash):snap?.hash, dirty, value:dirty?value:null, mode, json:mode==="json"?$("json-editor").value:null, instruction:$("instruction").value, fields, scroll:window.scrollY, open:Array.from(document.querySelectorAll("details[data-persist]" )).filter(x=>x.open).map(x=>x.dataset.persist)};
  Object.assign(workspaceState,{stage,target,updated:Date.now()});
  cacheLocal("studio-workspace-"+workspaceProject(), JSON.stringify(workspaceState));
  clearTimeout(workspaceTimer);
  cacheLocal("studio-last-view",workspaceProject());
  const project=workspaceProject(), state=structuredClone(workspaceState);
  workspaceTimer=setTimeout(()=>api("/api/action",{action:"save_workspace",project,state}).catch(()=>{}),450);
}
window.saveWorkspace=saveWorkspace;
async function readWorkspace(project) {
  if (project?.startsWith("tmp-")) return {views:{},stage:"prompt",target:""};
  let local={};try{local=JSON.parse(localStorage.getItem("studio-workspace-"+project)||"{}");}catch{}
  let remote={};try{remote=await api("/api/workspace?id="+encodeURIComponent(project));}catch{}
  return (local.updated||0)>=(remote.updated||0)?local:remote;
}
function applyWorkspace(state) {
  workspaceState=state;
  workspaceState.views ||= {};
  stage=["script","page","drawing","problems","finished","prompt"].includes(workspaceState.stage)?workspaceState.stage:stage;
  target=workspaceState.target || "";
}
async function loadWorkspace() {applyWorkspace(await readWorkspace(workspaceProject()));}
function restoreWorkspaceView() {
  if (stage === "prompt") {restoringWorkspace=false;return;}
  const saved=workspaceState.views?.[workspaceKey()];
  if(!saved){restoringWorkspace=false;saveWorkspace();return;}
  restoringWorkspace=true;
  if(saved.dirty && saved.base_hash!==snap?.hash){
    const recovery=node("div",undefined,"warning");recovery.append(node("p","上次未提交的编辑已保留；正式版本已有变化，暂不覆盖。"),button("查看保留的编辑",()=>{
      $("dialog-body").replaceChildren(node("h2","保留的编辑草稿"));const raw=node("textarea");raw.rows=20;raw.value=saved.json||JSON.stringify(saved.value,null,2);$("dialog-body").append(raw);$("dialog").showModal();
    }));$("warnings").append(recovery);
  }
  $("instruction").value=saved.instruction||"";
  for(const input of document.querySelectorAll("[data-edit-key]")) if(Object.hasOwn(saved.fields||{},input.dataset.editKey)){
    if(input.type==="checkbox") input.checked=saved.fields[input.dataset.editKey]===true;
    else input.value=saved.fields[input.dataset.editKey];
  }
  window.refreshSampleControls?.();
  window.refreshPromptControls?.();
  window.refreshStoryControls?.();
  for(const item of document.querySelectorAll("details[data-persist]"))item.open=(saved.open||[]).includes(item.dataset.persist);
  if (stage === "drawing") window.refreshDrawingControls?.();
  if(saved.dirty && saved.base_hash===snap?.hash && !["script","drawing","problems","finished"].includes(stage)){
    setValue(saved.value);dirty=true;showMode(saved.mode||"form");
    if(saved.mode==="json" && saved.json!=null)$("json-editor").value=saved.json;
  }
  requestAnimationFrame(()=>requestAnimationFrame(()=>{window.scrollTo(0,saved.scroll||0);restoringWorkspace=false;if(workspaceState.characterEditor?.open && !$("dialog").open)window.openCharacterLibrary(undefined,workspaceState.characterEditor.name);}));
}
window.restoreWorkspaceView=restoreWorkspaceView;
document.addEventListener("input",()=>saveWorkspace());
document.addEventListener("change",()=>saveWorkspace());
document.addEventListener("toggle",ev=>{if(ev.target.matches?.("details[data-persist]"))saveWorkspace();},true);
let scrollSave;
window.addEventListener("scroll",()=>{clearTimeout(scrollSave);scrollSave=setTimeout(saveWorkspace,180);},{passive:true});
window.addEventListener("pagehide",()=>{
  if (stage === "prompt") window.savePromptDraft?.();
  if (pid?.startsWith("tmp-")) {
    fetch("/api/action", {method:"POST",keepalive:true,headers:{"Content-Type":"application/json","X-Studio-Token":boot.token},body:JSON.stringify({action:"release_prompt",project:pid})}).catch(()=>{});
    return;
  }
  if (stage === "prompt") return;
  saveWorkspace();
  if(workspaceProject() && boot?.token)fetch("/api/action",{method:"POST",keepalive:true,headers:{"Content-Type":"application/json","X-Studio-Token":boot.token},body:JSON.stringify({action:"save_workspace",project:workspaceProject(),state:workspaceState})}).catch(()=>{});
});

function renderAuditCards(root,audit){
  const details=node("details",undefined,"audit-cards");details.append(node("summary",`连续性核对 · ${(audit.issues||[]).length} 条`));
  for(const issue of audit.issues||[]){const card=node("article",undefined,"review-item");card.append(node("strong",`${issue.panel_id||issue.page_id} · ${issue.category}`),node("p",issue.evidence),node("p",issue.suggestion));details.append(card);}
  if(audit.reason)details.append(node("p",audit.reason));root.append(details);
}
function changeSummary(root,part){
  if(!part){root.append(node("p","尚未生成"));return;}
  const panels=part.panels || (part.panel?[part.panel]:[]);
  for(const panel of panels){root.append(node("p",panel.main_instant),node("p",panel.drawing_description));}
  for(const line of part.lettering||[])root.append(node("p",(snap.project.canon?.characters.find(c=>c.id===line.speaker_id)?.name||enumNames[line.kind]||"")+"："+line.text));
  if(!panels.length)root.append(readObject(part));
}
function changeDelta(root,before,after){
  if(before?.panel && after?.panel){
    for(const [key,value] of Object.entries(after.panel)){
      if(JSON.stringify(value)===JSON.stringify(before.panel[key]))continue;
      root.append(node("strong",names[key]||key));
      if(typeof value==="string")root.append(node("p",value));else root.append(readObject(value));
    }
    if(JSON.stringify(before.lettering)!==JSON.stringify(after.lettering)){
      root.append(node("strong","对白与独立文字"));for(const line of after.lettering)root.append(node("p",line.text));
    }
  }else changeSummary(root,after);
}
async function renderChangeCards(root,c){
  try{
    const items=await api(`/api/change-items?id=${pid}&candidate=${c.id}`);
    for(const item of items){
      const card=node("article",undefined,"change-card"),body=node("div"),actions=node("div",undefined,"change-actions");
      body.append(node("h3",item.title));
      const after=node("div",undefined,"change-preview");changeDelta(after,item.before,item.after);body.append(after);
      const before=node("details");before.append(node("summary","查看修改前"));changeSummary(before,item.before);body.append(before);
      if(item.conflict)body.append(node("p","此项有更新版本，请重新生成建议，避免覆盖。","warning"));
      if(item.decision!=="pending")actions.append(node("span",item.decision==="apply"?"✓ 已应用":"已取消","badge"));
      else for(const [decision,label] of [["apply","Apply · 应用"],["cancel","Cancel · 取消"]]){
        const b=button(label,async()=>{saveWorkspace();await act("decide_change",{candidate:c.id,unit:item.id,decision});snap=await api("/api/project?id="+pid);root.replaceChildren();await renderChangeCards(root,c);notify(decision==="apply"?"已应用这一条，其他内容保留。":"已取消这一条建议。");});
        b.disabled=decision==="apply"&&(item.conflict||!!c.invalid_reason);actions.append(b);
      }
      card.append(body,actions);root.append(card);
    }
    if(!items.length)root.append(node("p","这份候选没有内容变化。"));
  }catch(error){root.append(node("p",error.message,"warning"));}
}
async function renderStoryboardBundles(root){
  try{
    const bundles=await api("/api/storyboard-bundles?id="+pid);
    const pending=bundles.filter(x=>x.status!=="applied")
      .sort((a,b)=>(b.created||"").localeCompare(a.created||""));
    const ready=pending.find(b=>b.status==="complete"&&b.base_hash===snap.hash&&Object.keys(b.pages).length);
    const reviewing=pending.find(b=>b.status==="running"&&b.plan?.pages?.length&&b.plan.pages.every(p=>b.pages[p.id]));
    if(pending.length && !(snap.project.plan?.pages?.length && snap.project.plan.pages.every(p=>snap.project.pages[p.id]))){
      const actions=node("div",undefined,"storyboard-next");
      const next=button("下一步",async()=>{
        next.textContent="正在进入…";
        try{
          await preserve();
          const projectId=pid;
          await act("accept_storyboard_bundle",{project:projectId,bundle:ready.id});
          try {await window.exportStoryboardPrompts(projectId,api,notify);}
          finally {
            if(pid===projectId){stage="drawing";target="";await openProject(projectId);}
          }
        }finally{next.textContent="下一步";}
      },"primary");
      next.disabled=!ready;
      next.title=reviewing?"全部页面已生成，等待逐页核对结束后自动启用":"确认并导出分镜，进入绘图与排版";
      actions.append(node("p",ready?"文字分镜已完成，下一步进入绘图。":
        reviewing?"全部页面已生成，正在逐页核对；核对进度见上方任务卡，结束后「下一步」会自动启用。":
        pending.some(b=>b.status==="running")?"文字分镜正在生成或核对，完成后可进入下一步。":"分镜尚未完整或版本已变化，请先处理下方提示。"),next);
      root.prepend(actions);
    }
    for(const b of pending.filter(x=>Object.keys(x.pages).length).slice(0,3)){
      const box=node("details",undefined,"book-overview");box.open=true;box.dataset.persist="bundle-"+b.id;
      box.append(node("summary",`整部分镜草稿 · ${Object.keys(b.pages).length} 页 · ${b.status==="complete"?"待确认":b===reviewing?"已生成，核对中":"已保留部分结果"}`));
      for(const [id,entry] of Object.entries(b.pages)){const page=node("details");page.append(node("summary",id));appendReadablePanels(page,entry.payload);if(entry.metadata?.continuity_review)renderAuditCards(page,entry.metadata.continuity_review);box.append(page);}
      const accept=button("确认这批分镜，开始集中修改",async()=>{await act("accept_storyboard_bundle",{bundle:b.id});await openProject(pid);},"primary");
      accept.disabled=b.status!=="complete"||b.base_hash!==snap.hash;box.append(accept);root.append(box);
    }
  }catch(error){root.append(node("p",error.message,"warning"));}
}
function node(tag, text, cls) {
  const n = document.createElement(tag);
  if (text !== undefined) n.textContent = text;
  if (cls) n.className = cls;
  return n;
}
function notify(text, error = false) {
  $("notice").textContent = text;
  $("notice").className = error ? "error" : "";
  $("notice").hidden = false;
  setTimeout(() => ($("notice").hidden = true), error ? 16000 : 5500);
}
// Only shared catalog data is cached. Project contents, jobs and images always
// remain live; each caller gets its own copy so editors cannot mutate the cache.
const catalogCache = new Map();
const catalogTTL = new Map([["/api/characters", 30000], ["/api/drawing-options", 30000], ["/api/projects", 5000]]);
function invalidateCatalogs(action) {
  if (action.startsWith("character_") || action === "save_lora_labels") {
    catalogCache.delete("/api/characters");catalogCache.delete("/api/drawing-options");
  }
  if (!["save_workspace", "save_draft", "release_prompt", "set_remaining_candidate_count"].includes(action))
    catalogCache.delete("/api/projects");
}
async function api(url, body, retrySession = true) {
  const ttl = !body && catalogTTL.get(url);
  if (!ttl) {
    const result = await requestApi(url, body, retrySession);
    if (body) invalidateCatalogs(body.action || "");
    return result;
  }
  let entry = catalogCache.get(url);
  if (!entry || entry.expires < Date.now()) {
    entry = {expires:Infinity};
    entry.promise = requestApi(url).then(data => {
      entry.expires = Date.now() + ttl;
      return data;
    }).catch(error => {
      if (catalogCache.get(url) === entry) catalogCache.delete(url);
      throw error;
    });
    catalogCache.set(url, entry);
  }
  return structuredClone(await entry.promise);
}
async function requestApi(url, body, retrySession = true) {
  let r;
  try {
    r = await fetch(
      url,
      body
        ? {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "X-Studio-Token": boot.token,
            },
            body: JSON.stringify(body),
          }
        : url === "/api/status" ? {signal: AbortSignal.timeout(5000)} : {},
    );
  } catch (error) {
    throw Error("无法连接本地工作室。请重新打开 LocalDraw.exe，待连接恢复后再操作。");
  }
  const data = await r.json();
  if (!r.ok && body && retrySession && data.error === "请刷新本地界面后操作") {
    boot.token = (await api("/api/bootstrap")).token;
    return api(url, body, false);
  }
  if (!r.ok) throw Error(data.error || r.statusText);
  return data;
}
async function act(action, data = {}) {
  const result = await api("/api/action", { action, project: pid, ...data });
  if (result.job) void refreshStatus();
  return result;
}
const activeJobStates = new Set(["running", "pausing", "paused", "cancelling"]);
async function controlJob(job, action) {
  if (action === "resume") await window.flushPausedDrawingCount?.(job.id);
  await act(action + "_job", {job:job.id, confirm:action === "cancel"});
  await refreshStatus();
}
function confirmCancelJob(job) {
  const dialog = node("dialog", undefined, "task-confirm");
  dialog.setAttribute("aria-labelledby", "cancel-job-title");
  const title = node("h2", "结束这个任务？"); title.id = "cancel-job-title";
  const row = node("div", undefined, "dialog-actions");
  const keep = button("保留任务", () => dialog.close());
  const cancel = button("确认结束", async () => {
    cancel.disabled = true;
    try {await controlJob(job, "cancel"); dialog.close();}
    finally {cancel.disabled = false;}
  }, "danger");
  row.append(keep, cancel);
  dialog.append(title, node("p", job.label),
    node("p", "结束后不再继续剩余工作。正在处理的图片或文字会在保存后停止，已生成的图片、剧本和选图都会保留。"),
    node("p", "如果只是稍后继续，请使用暂停。", "hint"), row);
  dialog.addEventListener("close", () => dialog.remove());
  document.body.append(dialog); dialog.showModal(); keep.focus();
}
const pausedDrawingCounts = new Map();
function drawingCountState(job) {
  let state = pausedDrawingCounts.get(job.id);
  const revision = job.request.quantity_revision || 0;
  if (!state || (!state.pending && !state.error && revision > state.revision)) {
    state = {value:job.request.candidate_count, saved:job.request.candidate_count, revision, pending:null, error:null};
    pausedDrawingCounts.set(job.id, state);
  }
  return state;
}
window.pausedDrawingCount = job => drawingCountState(job).value;
window.setPausedDrawingCount = function(job, count) {
  const state = drawingCountState(job);
  state.value = count;
  if (state.pending) return state.pending;
  state.error = null;
  state.pending = (async()=>{
    while (state.saved !== state.value) {
      const value = state.value;
      const result = await act("set_remaining_candidate_count", {
        project:job.project, job:job.id, candidate_count:value, quantity_mode:"total",
      });
      state.saved = value;
      state.revision = result.quantity_revision;
      Object.assign(job.request, {candidate_count:value, quantity_revision:state.revision, quantity_mode:"total"});
    }
  })().catch(error=>{state.error=error;throw error;}).finally(()=>{
    state.pending = null;
    renderRemainingDrawingSettings(window.activeStudioJob);
  });
  renderRemainingDrawingSettings(job);
  return state.pending;
};
window.flushPausedDrawingCount = async function(jobId) {
  const state = pausedDrawingCounts.get(jobId);
  if (!state) return;
  if (state.pending) await state.pending;
  if (state.error) throw Error("数量尚未保存，任务没有继续：" + state.error.message);
};
function renderRemainingDrawingSettings(job) {
  window.syncDrawingQuantity?.(job);
  const root = $("remaining-drawing-settings");
  if (!root) return;
  if (!job || job.project !== pid || job.request?.action !== "draw_imported" || !activeJobStates.has(job.status)) {
    root.hidden = true; root.dataset.signature = ""; return;
  }
  const supported = Boolean(window.studioFeatures?.candidate_count_total);
  const disconnected = Boolean(window.studioDisconnected);
  const state = drawingCountState(job);
  const signature = JSON.stringify([job.id, job.status, state.revision,
    state.value, Boolean(state.pending), state.error?.message, supported, disconnected]);
  root.hidden = false;
  if (root.dataset.signature === signature) return; // Keep the open picker stable during polling.
  root.dataset.signature = signature;
  root.replaceChildren();
  const title = node("strong", "当前任务 · 每格数量");
  const label = node("label", "未完成格的目标总数"), count = node("select");
  count.setAttribute("aria-label", "接下来每格生成数量");
  for (let n = 1; n <= 16; n++) { const opt = node("option", `${n} 张`); opt.value = String(n); count.append(opt); }
  count.value = String(state.value || 2);
  const editable = job.status === "paused" && supported && !disconnected;
  count.disabled = !editable;
  label.append(count);
  const saveCount = async()=>{
    await window.setPausedDrawingCount(job, Number(count.value));
    notify(`已保存为每格 ${state.saved} 张；已有图片保留，继续任务时按新数量执行。`);
  };
  count.onchange = safe(saveCount);
  const save = button(state.pending?"正在保存…":state.error?"重试保存数量":"保存数量", saveCount, "primary");
  save.disabled = !editable || Boolean(state.pending);
  const hint = disconnected ? "后台连接中断，恢复连接后才能修改。"
    : !supported ? "此功能需要重启后台。请先暂停任务，再重启工作室；已有图片和断点保留。"
    : state.error ? "保存失败，请重试；继续任务已被阻止，避免沿用旧数量。"
    : state.pending ? "正在保存；继续任务会等待保存成功。"
    : job.status === "paused" ? "选择后自动保存。保持暂停，点击「继续任务」才开始。"
    : "先暂停任务，等显示「已暂停」后即可修改。";
  root.append(title, label, save, node("small", "已完成格不补画。当前格已生成的张数计入总数；超过新数量的图片也会保留。"), node("p", hint, "hint"));
}
function jobProgress(job) {
  const box = node("div", undefined, "job task-card " + job.status);
  box.dataset.jobId = job.id || "loading";
  const labels = {running:"正在进行", pausing:"正在暂停", paused:"已暂停", cancelling:"正在取消", cancelled:"已取消", done:"已完成", failed:"未完成", interrupted:"已中断", resumed:"已继续", disconnected:"后台连接中断"};
  const top = node("div", undefined, "task-top");
  const title = node("div", undefined, "task-title");
  const state = node("span", undefined, "task-state"), name = node("strong");
  title.append(state, name);
  const actions = node("div", undefined, "task-actions");
  function add(label, command, cls) {
    const b = button(label, () => command === "cancel" ? confirmCancelJob(job) : controlJob(job, command), cls);
    b.dataset.control = job.id + "-" + command;
    actions.append(b); return b;
  }
  const resume = add("继续任务", "resume", "primary");
  const pause = add("暂停任务", "pause");
  const cancel = add("结束任务", "cancel", "quiet-danger");
  const retry = add("重新生成当前出错页", "retry", "primary");
  const setText = (element, value) => { if (element.textContent !== value) element.textContent = value; };
  const setControl = (element, unavailable, reason) => {
    element.dataset.unavailable = String(unavailable);
    element.disabled = unavailable || element.hasAttribute("aria-busy");
    element.title = reason;
  };
  const updateControls = () => {
    actions.hidden = !job.id;
    const disconnected = job.status === "disconnected" || Boolean(window.studioDisconnected);
    const cannotResume = disconnected || !["paused", "pausing"].includes(job.status);
    const cannotPause = disconnected || job.status !== "running";
    const cannotCancel = disconnected || !["running", "pausing", "paused", "interrupted"].includes(job.status);
    const reason = disconnected ? "后台连接中断，重新连接后才能操作"
      : job.status === "interrupted" ? job.resume_error || "未找到可用断点，已保存的结果保留"
      : job.status === "cancelling" ? "正在保存当前结果并结束任务"
      : ["done", "failed", "cancelled", "resumed"].includes(job.status) ? "这条任务记录已结束"
      : "";
    setControl(resume, cannotResume, cannotResume ? reason || "任务正在执行，无需再次开始" : "从暂停处继续");
    setControl(pause, cannotPause, cannotPause ? reason || "任务已暂停或正在暂停" : "保存当前结果后暂停");
    setControl(cancel, cannotCancel, cannotCancel ? reason : "结束任务前需要再次确认，已有结果保留");
    retry.hidden = !(job.status === "failed" && job.request?.action === "storyboard_all" && job.request.bundle_id);
    setControl(retry, Boolean(window.activeStudioJob) || Boolean(window.studioDisconnected),
      window.studioDisconnected ? "后台连接中断，请等待重新连接"
        : window.activeStudioJob ? "请先完成或结束当前任务"
        : "从出错页继续，保留已完成页面；完成后继续剩余页面和核对");
  };
  top.append(title, actions); box.append(top);
  const statusText = {paused:"进度已保存。点击「继续任务」后才会继续。", pausing:"正在保存当前图片或文字，保存后暂停；不会开始下一项。", cancelling:"正在保存当前结果，随后结束任务。", cancelled:"已有图片和选图已保留。"};
  const message = node("div", undefined, "task-message");
  message.setAttribute("role", "status"); box.append(message);
  const hint = node("p", undefined, "hint"), line = node("div", undefined, "task-progress-line");
  const count = node("strong"), estimate = node("span"), bar = node("progress");
  line.append(count, estimate);bar.setAttribute("aria-label", "任务已完成工作量");
  box.append(hint, line, bar);
  const detail = node("details", undefined, "task-details");
  detail.dataset.jobDetail = job.id || "loading";
  const detailBody = node("div");
  detail.append(node("summary", "查看运行详情"), detailBody);
  const updateDetail = () => { if (detail.open && !detail.hidden) detailBody.replaceChildren(jobProgressDetails(job)); };
  detail.ontoggle = updateDetail;
  box.append(detail);
  box.updateJob = next => {
    job = next;
    box.className = "job task-card " + job.status;
    setText(state, labels[job.status] || job.status);
    setText(name, job.label || "准备任务");
    updateControls();
    const auditing=job.status==="running"&&job.request?.action==="storyboard_all"&&job.detail_progress?.unit==="页已核对";
    const auditDetail=job.detail_progress;
    const persistentAudit=auditing && Object.hasOwn(auditDetail,"current_page");
    const auditMessage=persistentAudit && auditDetail.current_page
      ? `全部 ${job.progress.total} 页已生成，正在核对 ${auditDetail.current_page}（已核对 ${auditDetail.completed}/${auditDetail.total} 页）。`
      : job.message;
    setText(message, statusText[job.status] || (persistentAudit?auditMessage:auditing?`全部 ${job.progress.total} 页已生成，正在核对第 ${Math.min(job.detail_progress.completed+1,job.detail_progress.total)} / ${job.detail_progress.total} 页；核对结束后可进入下一步。`:job.message) || "准备中");
    hint.hidden = job.status !== "interrupted";
    setText(hint, job.resume_error || "未找到可用断点；已有结果保留，可点击「结束任务」关闭这条记录。");
    const visibleProgress=auditing?job.detail_progress:job.progress;
    line.hidden = bar.hidden = !(visibleProgress?.total > 0);
    if (!bar.hidden) {
      const done = visibleProgress.completed, total = visibleProgress.total;
      setText(count, `${done} / ${total} ${visibleProgress.unit || "项"}`);
      const seconds = job.eta?.remaining_seconds;
      setText(estimate, job.status === "disconnected" ? "最后记录的进度，当前状态待确认" : job.status === "paused" ? "暂停期间不计剩余时间" : job.status === "running" && seconds > 0 ? `本阶段预计还需 ${Math.ceil(seconds / 60)} 分钟` : "按实际完成量更新");
      if (bar.max !== total) bar.max = total;
      if (bar.value !== done) bar.value = done;
    }
    detail.hidden = job.status === "disconnected";
    updateDetail();
  };
  box.updateJob(job);
  return box;
}
function renderJobCards(jobs) {
  const root = $("jobs"), existing = new Map([...root.children].map(card => [card.dataset.jobId, card]));
  const retained = new Set();
  jobs.forEach((job, index) => {
    let card = existing.get(job.id || "loading");
    if (!card?.updateJob) card = jobProgress(job);
    else card.updateJob(job);
    retained.add(card);
    if (root.children[index] !== card) root.insertBefore(card, root.children[index] || null);
  });
  for (const card of [...root.children]) if (!retained.has(card)) card.remove();
}
function jobProgressDetails(job) {
  const box = node("div", undefined, "job " + job.status);
  const running = job.status === "running";
  box.setAttribute("role", "status");
  box.append(
    node(
      "strong",
      job.label +
        (running
          ? " · 进行中"
          : job.status === "done"
            ? " · 已完成"
            : " · 已停止"),
    ),
  );
  const addBar = (progress, title) => {
    if (!(progress?.total > 0)) return;
    const bar = node("progress");
    bar.setAttribute("aria-label", title);
    bar.max = progress.total;
    bar.value = progress.completed;
    const percent = Math.min(
      100,
      Math.floor((progress.completed / progress.total) * 100),
    );
    box.append(
      node(
        "div",
        `${progress.completed} / ${progress.total} ${progress.unit || ""} · 已完成 ${percent}%`,
        "progress-count",
      ),
      bar,
    );
  };
  const remaining = (seconds) => {
    if (seconds == null || !Number.isFinite(seconds))
      return "暂不能估算，等待实际运行数据";
    if (seconds < 0) return "已超过此前估计，正在等待新的完成数据";
    if (seconds < 60) return `约 ${Math.ceil(seconds)} 秒`;
    return `约 ${Math.ceil(seconds / 60)} 分钟`;
  };
  const live = job.runtime;
  if (running)
    box.append(
      node(
        "strong",
        (job.eta?.scope ? job.eta.scope+"剩余：" : job.eta ? "整部绘图预计剩余：" : "本环节预计剩余：") +
          remaining(job.eta ? job.eta.remaining_seconds : live?.remaining_seconds),
        "eta",
      ),
      node("small", job.eta?.basis || live?.estimate_basis || "根据实际运行数据估算"),
    );
  addBar(job.progress, job.label + "已完成工作量");
  if (job.detail_progress) addBar(job.detail_progress, job.detail_progress.unit==="页已核对"?"已核对分镜页数":"当前轮已完成图片数");
  box.append(node("div", job.message));
  if (live) {
    box.append(node("strong", "模型当前步骤：" + live.phase, "runtime-phase"));
    addBar(live, "模型实际完成量");
    if (live.output_tokens != null)
      box.append(
        node(
          "div",
          `已生成 ${live.output_tokens} token${live.characters != null ? ` · 已接收 ${live.characters} 字符（含结构标记）` : ""}`,
        ),
      );
    if (live.tokens_per_second)
      box.append(
        node(
          "div",
          `实际生成速度：${live.tokens_per_second.toFixed(1)} token/秒`,
        ),
      );
    if (live.detail) box.append(node("small", live.detail));
    if (running) {
      if (job.eta) box.append(
        node(
          "strong",
          "本环节预计剩余：" + remaining(live.remaining_seconds),
          "eta",
        ),
      );
      if (job.eta && live.estimate_basis)
        box.append(node("small", "估算依据：" + live.estimate_basis));
      const age = Math.max(0, Math.floor(Date.now() / 1000 - live.updated_at));
      box.append(
        node(
          "small",
          `来自 ${live.source} · ${age} 秒前回报${age >= 10 ? "，尚无新的进度数据" : ""}`,
        ),
      );
    }
  } else if (running)
    box.append(node("p", "等待模型回报实际执行步骤；此时不显示完成百分比。"));
  if (running) {
    const fallback = job.created?.match(
      /^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})/,
    );
    const started = job.started_at
      ? job.started_at * 1000
      : fallback
        ? new Date(
            `${fallback[1]}-${fallback[2]}-${fallback[3]}T${fallback[4]}:${fallback[5]}:${fallback[6]}`,
          ).getTime()
        : Date.now();
    const seconds = Math.max(0, Math.floor((Date.now() - started) / 1000));
    box.append(
      node(
        "small",
        `总计已用 ${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒。剩余时间是估计值；进度条只随真实完成量变化。`,
      ),
    );
  }
  if (job.result?.unresolved?.length)
    box.append(
      node(
        "p",
        `${job.result.unresolved.length} 格达到补画上限，仍有视角不符合文本；请在下方查看，未自动选图。`,
      ),
    );
  if (job.result?.unresolved_views?.length)
    box.append(
      node(
        "p",
        "仍不符的视角：" +
          job.result.unresolved_views.join("、") +
          "，已停止补画，等待你处理。",
      ),
    );
  return box;
}
function safe(fn) {
  return async (...args) => {
    try {
      await fn(...args);
    } catch (e) {
      notify(e.message, true);
    }
  };
}
function button(text, fn, cls) {
  const b = node("button", text, cls);
  b.type = "button";
  b.onclick = safe(async (...args) => {
    b.disabled = true;
    b.setAttribute("aria-busy", "true");
    try {
      await fn(...args);
    } finally {
      b.disabled = b.dataset.unavailable === "true" || (b.dataset.startJob === "true" && !!window.activeStudioJob);
      b.removeAttribute("aria-busy");
    }
  });
  return b;
}
function blank(s) {
  if (s.enum) return s.enum[0];
  if (s.type === "object")
    return Object.fromEntries(
      Object.entries(s.properties || {}).map(([k, v]) => [k, blank(v)]),
    );
  if (s.type === "array")
    return Array.from({ length: s.minItems || 0 }, () => blank(s.items));
  if (s.type === "boolean") return false;
  if (s.type === "integer") return s.minimum || 1;
  if (Array.isArray(s.type) && s.type.includes("null")) return null;
  return "";
}
function editor(s, v, change, key = "", depth = 0) {
  const type = Array.isArray(s.type) ? "string" : s.type;
  if (type === "object") {
    const box = node("div", undefined, "fields");
    if (!s.properties) {
      box.className = "";
      let current = { ...(v || {}) };
      for (const [k, val] of Object.entries(current)) {
        const row = node("div", undefined, "row");
        const keyInput = node("input");
        keyInput.value = k;
        keyInput.setAttribute("aria-label", "状态键");
        const input = node("input");
        input.value = val;
        input.setAttribute("aria-label", k);
        keyInput.onchange = () => {
          if (
            !keyInput.value ||
            (keyInput.value !== k && keyInput.value in current)
          ) {
            notify("状态键不能为空或重复", true);
            keyInput.value = k;
            return;
          }
          delete current[k];
          current[keyInput.value] = input.value;
          change(current);
        };
        input.oninput = () => {
          current[keyInput.value] = input.value;
          change(current);
        };
        row.append(
          keyInput,
          input,
          button(
            "移除",
            () => {
              delete current[keyInput.value];
              row.remove();
              change(current);
            },
            "small",
          ),
        );
        box.append(row);
      }
      box.append(
        button(
          "＋ 添加状态项",
          () => {
            let i = 1;
            while ("state." + i in current) i++;
            current["state." + i] = "";
            change(current);
            renderForm();
          },
          "small array-add",
        ),
      );
      const details = node("details", undefined, "collection");
      details.append(node("summary", "展开编辑状态"), box);
      return details;
    }
    for (const [k, cs] of Object.entries(s.properties)) {
      const field = node(
        "div",
        undefined,
        "field" +
          (["object", "array"].includes(cs.type) ||
          [
            "direction",
            "character_material",
            "main_instant",
            "drawing_description",
            "composition",
          ].includes(k)
            ? " field-wide"
            : ""),
      );
      const label = node("label", names[k] || k);
      const id = "field-" + crypto.randomUUID();
      label.htmlFor = id;
      const input = editor(
        cs,
        v?.[k] ?? blank(cs),
        (x) => {
          v[k] = x;
          change(v);
        },
        k,
        depth + 1,
      );
      input.id = id;
      field.append(label, input);
      box.append(field);
    }
    return box;
  }
  if (type === "array") {
    const box = node("div");
    let items = v || [];
    function paint() {
      box.replaceChildren();
      items.forEach((item, i) => {
        const complex = s.items.type === "object";
        const row = node(
          complex ? "details" : "div",
          undefined,
          complex ? "collection" : "row",
        );
        if (complex) {
          row.open =
            !["panels", "lettering"].includes(key) && items.length <= 2;
          row.append(
            node(
              "summary",
              `${i + 1}. ${item.id || item.title || item.name || names[key] || "条目"}`,
            ),
          );
        }
        row.append(
          editor(
            s.items,
            item,
            (x) => {
              items[i] = x;
              change(items);
            },
            key,
            depth + 1,
          ),
          button(
            "移除",
            () => {
              items.splice(i, 1);
              change(items);
              paint();
            },
            "small danger",
          ),
        );
        box.append(row);
      });
      box.append(
        button(
          "＋ 添加" + (names[key] || "条目"),
          () => {
            items.push(blank(s.items));
            change(items);
            paint();
          },
          "small array-add",
        ),
      );
    }
    paint();
    return box;
  }
  let el;
  if (s.enum) {
    el = node("select");
    for (const x of s.enum) {
      const o = node("option", enumNames[x] || x);
      o.value = x;
      el.append(o);
    }
    el.value = v ?? s.enum[0];
    el.onchange = () => change(el.value);
  } else if (type === "boolean") {
    el = node("select");
    for (const [x, t] of [
      ["true", "是"],
      ["false", "否"],
    ]) {
      const o = node("option", t);
      o.value = x;
      el.append(o);
    }
    el.value = String(v);
    el.onchange = () => change(el.value === "true");
  } else if (type === "integer") {
    el = node("input");
    el.type = "number";
    el.min = s.minimum || 1;
    el.value = v;
    el.oninput = () => change(Number(el.value));
  } else {
    const short = [
      "id",
      "number",
      "page_id",
      "panel_id",
      "speaker_id",
      "character_id",
      "scene_id",
      "prop_id",
      "name",
      "title",
      "shot",
      "angle",
      "selected_option",
      "genre",
    ].includes(key);
    el = node(short ? "input" : "textarea");
    el.value = v ?? "";
    if (!short) el.rows = 2;
    el.oninput = () =>
      change(Array.isArray(s.type) && !el.value ? null : el.value);
  }
  el.setAttribute("aria-label", names[key] || key);
  return el;
}
function setValue(v) {
  value = structuredClone(v);
  valueBaseHash = snap?.hash;
  dirty = false;
  activeCandidate = null;
  renderForm();
}
function renderForm() {
  const actual = stage === "page" && target.includes("-K") ? "panel" : stage;
  const s = boot.schemas[actual];
  $("form").replaceChildren(
    editor(s, value, (x) => {
      value = x;
      dirty = true;
    }),
  );
  $("json-editor").value = JSON.stringify(value, null, 2);
}
function getValue() {
  if (mode === "json") value = JSON.parse($("json-editor").value);
  return value;
}
async function preserve() {
  await window.flushProblemEdits?.();
  await window.flushLoraLibrary?.();
  saveWorkspace();
  if (window.flushLoraSettings) await window.flushLoraSettings();
  if (dirty && !newMode && pid && !["drawing", "problems", "finished", "script", "prompt"].includes(stage)) {
    await act("save_draft", {
      stage: actualStage(),
      target,
      payload: getValue(),
      base_hash: valueBaseHash || snap.hash,
    });
    dirty = false;
  }
}
function actualStage() {
  return stage === "page" && target.includes("-K") ? "panel" : stage;
}
async function reloadProjects(refresh = true) {
  if (refresh) boot.projects = await api("/api/projects");
  const signature = JSON.stringify(boot.projects.map(p=>[p.id,p.title]));
  if ($("projects").dataset.signature === signature) {$("projects").value = pid || "";return;}
  $("projects").dataset.signature = signature;
  $("projects").replaceChildren();
  const placeholder=node("option","选择已有作品…");placeholder.value="";placeholder.disabled=true;$("projects").append(placeholder);
  for (const p of boot.projects) {
    const o = node("option", p.title);
    o.value = p.id;
    $("projects").append(o);
  }
  $("projects").value = pid || "";
}
let projectOpenRevision = 0;
async function openProject(id, refreshProjects = !boot.projects.some(p=>p.id===id), initialStage = "script") {
  const revision = ++projectOpenRevision;
  if (pid?.startsWith("tmp-") && pid !== id) await api("/api/action", {action:"release_prompt",project:pid});
  const switching = !snap || pid !== id;
  const [snapshot, workspace] = await Promise.all([
    api("/api/project?id=" + encodeURIComponent(id)), switching ? readWorkspace(id) : null,
  ]);
  if (revision !== projectOpenRevision) return;
  pid = id;
  newMode = false;
  snap = snapshot;
  if (switching) {
    stage=snap.project.quick_sample || snap.project.prompt_board ? "prompt" : initialStage;
    applyWorkspace(workspace);
    if(snap.project.prompt_board && !["prompt","finished"].includes(stage))stage="prompt";
  }
  const listed = boot.projects.find(project=>project.id===id);
  if (listed) Object.assign(listed, {title:snap.project.brief.title, version:snap.project.version, pages:Object.keys(snap.project.pages).length});
  if (!id.startsWith("tmp-")) {cacheLocal("studio-last-project", id);cacheLocal("studio-last-view",id);}
  await reloadProjects(refreshProjects);
  if (revision !== projectOpenRevision) return;
  await renderStage();
}
function currentValue() {
  const p = snap?.project;
  if (stage === "page") {
    const page = p?.pages[target.split("-")[0]];
    if (target.includes("-K")) {
      const panel = page?.panels.find((x) => x.id === target);
      return panel
        ? {
            panel,
            lettering: page.lettering.filter((x) => x.panel_id === target),
          }
        : null;
    }
    return page;
  }
  return p?.[stage];
}
async function renderStage() {
  await window.flushProblemEdits?.();
  window.flushProblemEdits = null;
  const revision = ++stageRender;
  if (["script", "brief", "drawing", "finished"].includes(stage)) {
    await window.ensureDrawingTools();
    if (revision !== stageRender) return;
  }
  window.disposeFinishedComic?.();
  window.disposeFinishedComic = null;
  $("extras").dataset.renderToken = String(revision);
  restoringWorkspace=true;
  const p = snap?.project;
  for(const id of ["history","fork","export","delete-project"]){
    $(id).disabled=newMode||!pid||pid.startsWith("tmp-");
    $(id).title=$(id).disabled?"创建或打开已保存的作品后可用":"";
  }
  if (stage === "brief") stage = "script";
  document.body.dataset.stage = stage;
  names.script = "剧本与角色";
  names.problems = "问题格子";
  help.problems = "集中填写问题，交给 GPT 或本地 LLM 理解，再按每格数量统一重画。";
  names.finished = p?.prompt_board ? "整图修改" : "成品查看";
  help.finished = p?.prompt_board ? "核对缺格，选择版式并下载漫画整页。" : "按页阅读已选图片与对白组成的漫画，检查后下载成品。";
  names.prompt = "提示词出图";
  help.prompt = "输入提示词，选择模型和张数，直接画图。";
  $("workflow-note").textContent = stage === "prompt"
    ? (p?.prompt_board ? "按分镜出图，采用图片后进入整图修改。" : "提示词 → 出图 → 查看 / 保存原图。本入口不自动审核或补画。")
    : "01 剧本 → 02 分镜 → 03 绘图与选图 → 04 问题格子 → 05 成品查看。选好后查看整页与对白，再下载成品。";
  help.script = "粘贴故事、选择角色，然后直接生成文字分镜。";
  $("heading").textContent = names[stage];
  $("stage-help").textContent = help[stage];
  $("version").textContent = newMode
    ? "新作品"
    : `已确认版本 ${p?.version || 1}`;
  $("provenance").textContent = newMode
    ? "从创作需求开始"
    : p?.provenance || "人工编排结构测试样例；不是本地模型输出";
  $("stages").replaceChildren();
  (p?.prompt_board ? ["prompt","finished"] : p?.quick_sample || (newMode && stage==="prompt") ? ["prompt"] : ["script", "page", "drawing", "problems", "finished"]).forEach((s, i) => {
    const b = button(
      `${String(i + 1).padStart(2, "0")}　${names[s]}`,
      async () => {
        await preserve();
        stage = s;
        target = "";
        await renderStage();
      },
      s === stage ? "active" : "",
    );
    b.disabled = newMode && !["script","prompt"].includes(s);
    if(s===stage)b.setAttribute("aria-current","step");
    $("stages").append(b);
  });
  $("target-row").hidden = !["page", "audit"].includes(stage);
  $("workspace").hidden = ["drawing", "problems", "finished", "script", "prompt"].includes(stage);
  $("extras").replaceChildren();
  $("extras").className = "";
  $("simple-page").replaceChildren();
  $("advanced-editor").open = false;
  $("warnings").replaceChildren();
  if (stage === "prompt") {
    $("candidates").replaceChildren();
    try {await window.renderPromptStudio($("extras"), newMode ? null : p); if (revision === stageRender) restoreWorkspaceView();}
    catch (error) {if (revision === stageRender) {restoringWorkspace=false;notify(error.message,true);}}
    return;
  }
  if (stage === "problems") {
    $("candidates").replaceChildren();restoringWorkspace=false;dirty=false;saveWorkspace();
    const projectId=pid;
    try {await window.renderProblemPanels($("extras"), {
      pid:projectId,project:p,api,act,node,button,notify,
      current:()=>revision===stageRender && pid===projectId && stage==="problems",
      clearFinishedCache:()=>{workspaceState.finishedPages={};saveWorkspace();},
      goDrawing:async()=>{await preserve();stage="drawing";target="";await renderStage();}
    });}catch(error){if(revision===stageRender)notify(error.message,true);}
    return;
  }
  if (stage === "finished") {
    $("candidates").replaceChildren();
    const projectId = pid;
    restoringWorkspace = false;
    dirty = false;
    saveWorkspace();
    try {
      if (p?.prompt_board) {
        await renderBoardLayout($("extras"), projectId);
        return;
      }
      await window.renderFinishedComic($("extras"), {
        projectId, api, node, button,
        layoutFields: structuredClone(workspaceState.views?.["drawing:"]?.fields || {}),
        cache: structuredClone(workspaceState.finishedPages || {}),
        bubbleStyle: workspaceState.finishedBubbleStyle || "auto",
        saveBubbleStyle: (style) => {workspaceState.finishedBubbleStyle = style; saveWorkspace();},
        aiPaused: workspaceState.finishedAiPaused !== false,
        aiJob: workspaceState.finishedAiJob,
        saveAiPaused: (paused) => {workspaceState.finishedAiPaused = paused; saveWorkspace();},
        saveAiJob: (job) => {workspaceState.finishedAiJob = job; saveWorkspace();},
        current: () => revision === stageRender && pid === projectId && stage === "finished",
        saveCache: (cache) => {workspaceState.finishedPages = cache; saveWorkspace();},
        goDrawing: async () => {await preserve(); stage = "drawing"; target = ""; await renderStage();},
      });
    } catch (error) {
      if (revision === stageRender) notify(error.message, true);
    }
    return;
  }
  if(snap?.warnings?.length){
    const issues=node("details",undefined,"audit-cards");issues.append(node("summary",`需要核对的提醒 · ${snap.warnings.length} 条（可逐条处理）`));
    workspaceState.issueDecisions ||= {};
    for(const warning of snap.warnings){
      const key=snap.hash+":"+warning;
      const card=node("article",undefined,"change-card"),body=node("div"),actions=node("div",undefined,"change-actions");body.append(node("p",warning));
      const panel=warning.match(/P\d{3}-K\d{2}/)?.[0];
      const decision=workspaceState.issueDecisions[key];
      if(decision)actions.append(node("small",decision==="cancel"?"已忽略提醒":"修改稿已排队，完成后逐条确认"));
      else {
        const apply=button("Apply · 生成修改稿",async()=>{
          saveWorkspace();
          await act("storyboard",{base_hash:snap.hash,target:panel||target.split("-")[0]||"P001",instruction:"仅处理这条提醒，保持其他内容与状态不变："+warning});
          workspaceState.issueDecisions[key]="apply";saveWorkspace();
          actions.replaceChildren(node("small","修改稿已排队，完成后逐条确认"));notify("正在生成这一条的修改候选，尚未覆盖分镜。");
        });
        actions.append(apply,button("Cancel · 忽略",()=>{workspaceState.issueDecisions[key]="cancel";saveWorkspace();actions.replaceChildren(node("small","已忽略提醒"));}));
      }
      card.append(body,actions);issues.append(card);
    }
    $("warnings").append(issues);
  }
  if (stage === "script") {
    $("candidates").replaceChildren();
    await window
      .renderStoryStart($("extras"), p)
      .then(() => {if (revision === stageRender) restoreWorkspaceView();})
      .catch((e) => {if (revision === stageRender) {restoringWorkspace=false;notify(e.message, true);}});
    return;
  }
  if (stage === "page" && !p?.plan) {
    $("workspace").hidden = true;
    $("target-row").hidden = true;
    $("candidates").replaceChildren();
    $("extras").append(
      node(
        "p",
        "剧本已确认。AI 会自动安排页数、大小不同的画格和对白，无需单独建立世界观或选择剧情方案。",
      ),
      button(
        "一键生成全部文字分镜",
        async () => {
          await act("storyboard_all", { base_hash: snap.hash, instruction: $("instruction").value });
          notify("已开始自动分页与分镜，请查看任务进度。");
        },
        "primary",
      ),
    );
    await renderStoryboardBundles($("extras"));
    restoreWorkspaceView();
    return;
  }
  if (stage === "drawing") {
    await renderDrawing();
    $("candidates").replaceChildren();
    return;
  }
  if (["page", "audit"].includes(stage)) {
    const selected = target.split("-")[0];
    $("page-select").replaceChildren();
    for (const page of p?.plan?.pages || []) {
      const o = node("option", `${page.id} · ${page.purpose}`);
      o.value = page.id;
      $("page-select").append(o);
    }
    $("page-select").value = selected || p?.plan?.pages[0]?.id || "";
    const pageId = $("page-select").value;
    if (!target) target = pageId;
    $("panel-select").replaceChildren(node("option", "整页"));
    $("panel-select").firstChild.value = "";
    for (const panel of p?.pages[pageId]?.panels || []) {
      const o = node("option", `${panel.id} · ${panel.purpose}`);
      o.value = panel.id;
      $("panel-select").append(o);
    }
    $("panel-select").value = target.includes("-K") ? target : "";
    $("panel-select").disabled = stage === "audit";
  }
  $("option-label").hidden = stage !== "outline";
  $("option-select").replaceChildren();
  for (const option of p?.options?.options || []) {
    const o = node("option", `${option.id} · ${option.title}`);
    o.value = option.id;
    $("option-select").append(o);
  }
  const s = actualStage();
  let v = currentValue();
  const d = snap?.drafts.find(
    (d) => d.stage === s && d.target === target && d.base_hash === snap.hash,
  );
  if (d) v = d.payload;
  if (!v) v = newMode ? boot.template.brief : blank(boot.schemas[s]);
  setValue(v);
  showMode("form");
  if (stage === "page") await renderSimplePage(p);
  $("propose").textContent = newMode
    ? "创建作品并确认需求"
    : "校验并查看候选差异";
  $("save-draft").disabled = newMode;
  $("generate").disabled = newMode || stage === "brief";
  $("propose").disabled = stage === "audit";
  await renderCandidates();
  restoreWorkspaceView();
}
async function renderSimplePage(p) {
  const root = $("simple-page"),
    page = p.pages[target.split("-")[0]];
  const top = node("div", undefined, "book-toolbar");
  top.append(node("h2", "整部分镜 · 集中编辑"), button("一键生成全部缺失分镜", async () => {
    await preserve();
    await act("storyboard_all", {base_hash:snap.hash,instruction:$("instruction").value});
    notify("正在一次生成全部缺失页面，已确认内容保留。");
  }, "primary"));
  root.append(top);
  if(p.plan?.pages?.length && p.plan.pages.every(pg=>p.pages[pg.id])){
    const actions=node("div",undefined,"storyboard-next");
    actions.append(button("导出分镜给外部工具",()=>window.exportStoryboardPrompts(pid,api,notify)));
    actions.append(node("p","文字分镜已齐，下一步进入绘图。"),button("下一步",async()=>{
      await preserve();stage="drawing";target="";await renderStage();
    },"primary"));
    root.prepend(actions);
  }
  const overview = node("details", undefined, "book-overview");
  overview.open = true;
  overview.dataset.persist = "book-overview";
  overview.append(node("summary", `全书浏览 · ${Object.keys(p.pages).length} 页已确认`));
  for (const pg of Object.values(p.pages)) {
    const entry = node("details");entry.dataset.persist = "overview-"+pg.id;
    entry.append(node("summary", pg.id + " · " + pg.panels.length + " 格"));
    appendReadablePanels(entry, pg);
    entry.append(button("修改这一页", async()=>{await preserve();target=pg.id;renderStage();}));
    overview.append(entry);
  }
  root.append(overview);
  await renderStoryboardBundles(root);
  root.append(
    node(
      "h2",
      page
        ? target.includes("-K")
          ? "这一格的分镜"
          : "这一页的分镜"
        : "这一页还没有分镜",
    ),
  );
  $("stage-help").textContent =
    "生成分镜 → 看画面和对白 → 确认。需要修改时写一句意见即可。";
  $("generate").textContent = page ? "按意见生成修改稿" : "生成这一页分镜";
  if (page) {
    appendReadablePanels(root, currentValue());
    const next = p.plan.pages.find((x) => !p.pages[x.id]);
    root.append(
      button(
        next ? "生成所有剩余分镜" : "分镜已齐，去一键出图",
        async () => {
          await preserve();
          if (next) {
            target = next.id;
            renderStage();
            await act("storyboard_all", { base_hash: snap.hash });
          } else {
            stage = "drawing";
            target = "";
            renderStage();
          }
        },
        "primary",
      ),
    );
  } else
    root.append(
      node(
        "p",
        "点击下方生成按钮，AI 会安排画格、镜头与对白。生成后只需阅读并确认，无需填写编号或状态。",
      ),
    );
}
function appendReadablePanels(root, page) {
  const p = snap?.project;
  for (const panel of page?.panels || (page?.panel ? [page.panel] : [])) {
    const card = node("article", undefined, "panel");
    card.append(
      node("h3", panel.id + " · " + panel.purpose),
      node("p", panel.main_instant),
      node("p", "画面：" + panel.drawing_description),
    );
    for (const line of (page.lettering || [])
      .filter((x) => x.panel_id === panel.id)
      .sort((a, b) => a.order - b.order)) {
      const speaker =
        p?.canon?.characters.find((x) => x.id === line.speaker_id)?.name ||
        enumNames[line.kind];
      card.append(
        node("div", speaker + "：「" + line.text + "」", "text-bubble"),
      );
    }
    const detail = node("details");
    detail.append(
      node("summary", "镜头与衔接详情"),
      node("p", `${panel.shot} · ${panel.angle} · ${panel.layout}`),
      node("p", panel.transition_in),
      node("p", panel.transition_out),
    );
    card.append(detail);
    root.append(card);
  }
}
function showMode(m) {
  if (mode === "json" && m !== "json") {
    value = JSON.parse($("json-editor").value);
    renderForm();
  }
  mode = m;
  for (const x of ["form", "json", "read"])
    $(x + "-tab").classList.toggle("active", x === m);
  $("form").hidden = m !== "form";
  $("json-editor").hidden = m !== "json";
  $("reader").hidden = m !== "read";
  if (m === "json") $("json-editor").value = JSON.stringify(value, null, 2);
  if (m === "read") renderReader();
}
function renderReader() {
  const root = $("reader");
  root.replaceChildren();
  const v = getValue();
  if (stage === "page") {
    const panels = v.panels || [v.panel];
    for (const p of panels.filter(Boolean)) {
      const box = node("article", undefined, "panel");
      box.append(node("h2", p.id + " · " + p.purpose));
      const dl = node("dl");
      for (const key of [
        "layout",
        "shot",
        "angle",
        "composition",
        "main_instant",
        "background",
        "transition_in",
        "transition_out",
        "drawing_description",
      ])
        dl.append(node("dt", names[key]), node("dd", p[key]));
      box.append(dl);
      for (const c of p.characters)
        box.append(
          node(
            "p",
            `${c.character_id} · ${c.position} · ${c.action} · ${c.expression}`,
          ),
        );
      for (const prop of p.props)
        box.append(
          node("p", `${prop.prop_id} · ${prop.position} · ${prop.state}`),
        );
      for (const t of (v.lettering || [])
        .filter((t) => t.panel_id === p.id)
        .sort((a, b) => a.order - b.order))
        box.append(
          node(
            "div",
            `${t.order}. ${enumNames[t.kind]} ${t.speaker_id || ""}「${t.text}」｜${t.bubble_position}`,
            "text-bubble",
          ),
        );
      root.append(box);
    }
  } else root.append(readObject(v));
}
function readObject(v) {
  const root = node("div");
  if (v && typeof v === "object") {
    for (const [k, x] of Object.entries(v)) {
      root.append(node("h3", names[k] || k));
      if (typeof x === "object") root.append(readObject(x));
      else root.append(node("p", String(x)));
    }
  } else root.append(node("p", String(v)));
  return root;
}
async function renderCandidates() {
  const root = $("candidates");
  root.replaceChildren(node("h2", "候选与审核记录"));
  const history = node("details");
  history.append(node("summary", "历史候选（已保留）"));
  for (const c of (snap?.candidates || []).filter(
    (c) => c.stage === actualStage() && c.target === target,
  )) {
    const box = node("article", undefined, "candidate");
    box.append(
      node(
        "h3",
        `${c.source} · ${names[c.stage]} ${c.target} ${c.stale ? "（历史候选）" : "（待确认）"}`,
      ),
    );
    if (c.metadata.wall_seconds)
      box.append(node("p", `本次生成 ${c.metadata.wall_seconds} 秒`));
    if (c.invalid_reason) box.append(node("p", "这份候选未通过校验，不能确认：" + c.invalid_reason, "warning"));
    if (["page", "panel"].includes(c.stage) && (!c.stale || Object.keys(c.review_decisions || {}).length)) {
      const cards=node("div");box.append(cards);await renderChangeCards(cards,c);
      if(c.metadata.continuity_review) renderAuditCards(box,c.metadata.continuity_review);
      root.append(box);
      continue;
    }
    if (c.metadata.continuity_review) {
      const audit = node("details");
      audit.open = c.metadata.continuity_review.verdict !== "pass";
      audit.append(
        node(
          "summary",
          "自动连续性审核：" +
            (c.metadata.continuity_review.verdict === "pass"
              ? "本次未发现问题"
              : c.metadata.continuity_review.verdict === "unavailable"
                ? "未能完成"
                : "有问题待核对"),
        ),
        readObject(c.metadata.continuity_review),
      );
      box.append(audit);
      if (!c.stale && c.metadata.continuity_review.verdict === "unavailable") {
        const retryAudit = node("button", "只重试审核，保留这份分镜", "secondary");
        retryAudit.onclick = safe(async () => {
          await act("audit_candidate", { candidate: c.id, base_hash: snap.hash });
          notify("正在补做审核，这份分镜不会重新生成。");
        });
        box.append(retryAudit);
      }
    }
    if (c.stage === "audit") box.append(readObject(c.payload));
    else {
      if (["page", "panel"].includes(c.stage)) {
        const readable = node("details");
        readable.open = !c.stale;
        readable.append(node("summary", "阅读这份分镜候选"));
        for (const panel of c.payload.panels || [c.payload.panel]) {
          readable.append(
            node("h3", panel.id + " · " + panel.purpose),
            node("p", panel.main_instant),
            node("p", "画面：" + panel.drawing_description),
            node(
              "p",
              "镜头：" +
                panel.shot +
                " / " +
                panel.angle +
                " / " +
                panel.layout,
            ),
          );
          for (const line of c.payload.lettering.filter(
            (t) => t.panel_id === panel.id,
          ))
            readable.append(
              node(
                "p",
                (line.speaker_id || enumNames[line.kind]) + "：" + line.text,
              ),
            );
        }
        box.append(readable);
      }
      const details = node("details");
      details.open = !c.stale && !["page", "panel"].includes(c.stage);
      details.append(node("summary", "展开候选差异"), node("pre", c.diff));
      box.append(details);
      const row = node("div", undefined, "row");
      const accept = button(
        "确认应用此候选",
        async () => {
          await act("accept", { candidate: c.id });
          await openProject(pid);
          notify("已确认，旧版本已保留。");
        },
        "primary",
      );
      accept.disabled = c.stale || Boolean(c.invalid_reason);
      row.append(
        accept,
        button("加载到编辑器", () => {
          setValue(c.payload);
          dirty = true;
          activeCandidate = c.id;
          showMode("form");
          $("advanced-editor").open = true;
          window.scrollTo({ top: 0, behavior: "smooth" });
        }),
      );
      box.append(row);
    }
    if (c.stale || c.invalid_reason) history.append(box);
    else root.append(box);
  }
  if (history.children.length > 1) root.append(history);
}
window.openProblemPanels = async function () {
  await preserve();stage="problems";target="";await renderStage();
};
window.openFinishedComic = async function () {
  await preserve();
  stage = "finished";
  target = "";
  await renderStage();
};
async function renderDrawing() {
  const box = $("extras");
  const renderToken = box.dataset.renderToken;
  box.replaceChildren(
    node("h2", "ComfyUI 绘图交接"),
    jobProgress({
      label: "载入候选图片",
      status: "running",
      message: "正在读取绘图状态…",
      started_at: Date.now() / 1000,
    }),
  );
  try {
    if (!window.renderComfy) throw Error("绘图连接模块准备中");
    await window.renderComfy(box, {
      pid,
      snap,
      api,
      act,
      node,
      button,
      notify,
      clearFinishedCache: () => {workspaceState.finishedPages = {}; saveWorkspace();},
    });
    if (box.dataset.renderToken === renderToken) restoreWorkspaceView();
  } catch (e) {
    if (box.dataset.renderToken !== renderToken) return;
    box.replaceChildren(node("h2", "ComfyUI 绘图交接"), node("p", e.message));
    restoringWorkspace=false;
  }
}
function showDisconnectedStatus() {
  window.studioDisconnected = true;
  window.updateStudioHomeConnection?.(false);
  window.updateStudioQueueConnection?.(false);
  renderRemainingDrawingSettings(window.activeStudioJob);
  $("model-status").textContent = "本地服务未连接";
  $("connection-status").hidden = false;
  $("start-model").disabled = true;
  $("stop-model").disabled = true;
  document.querySelectorAll("[data-start-job]").forEach(b => {
    b.disabled = true;
    b.title = "后台连接中断，请等待重新连接";
  });
  const previous = refreshStatus.visibleJobs || [];
  renderJobCards(previous.map(job => (
    activeJobStates.has(job.status) ? {...job, status:"disconnected",
      message:"无法确认任务是否仍在执行。下方是断开前的进度，重新连接后会更新实际状态。"} : job
  )));
  $("jobs").classList.remove("has-running");
}
async function refreshStatus() {
  if (refreshStatus.pending) return;
  refreshStatus.pending = true;
  try {
    let data;
    try {
      data = await api("/api/status");
    } catch (error) {
      showDisconnectedStatus();
      return;
    }
    window.studioDisconnected = false;
    window.activeStudioJob = data.jobs.find(j => activeJobStates.has(j.status));
    window.updateStudioHomeConnection?.(true);
    window.updateStudioQueueConnection?.(true);
    window.updateStudioQueue?.(data.queue);
    window.studioQueueAvailable = Boolean(data.features?.night_queue);
    window.updateStudioHomeStatus?.(data);
    $("connection-status").hidden = true;
    const drawingJob=window.activeStudioJob && ["draw_imported","prompt_batch"].includes(window.activeStudioJob.request?.action);
    $("model-status").textContent = drawingJob
      ? (window.activeStudioJob.status==="paused"?"绘图已暂停 · 等待继续":"正在绘图 · 查看任务进度")
      : data.model.ready
      ? (data.model.model_name?.includes("flash-next") ? "FlashNext 就绪 · " + (data.model.context_size / 1024) + "K" : "语言模型就绪")
      : data.model.owned
        ? "语言模型加载中"
        : data.model.file_size_ok
          ? "文字模型待机 · 创作时自动加载"
          : "文字模型文件尚未完整";
    $("model-status").title="文字创作和绘图使用各自的模型，工作室会按任务自动加载。";
    $("start-model").disabled = data.model.generating || (data.model.ready && data.model.owned);
    $("stop-model").disabled = data.model.generating || !data.model.owned;
    const seenJobs = new Set();
    const relevant = data.jobs
      .filter((j) => activeJobStates.has(j.status) || j.project === pid)
      .filter((j) => {
        const key = `${j.project || "studio"}:${j.label}`;
        if (seenJobs.has(key)) return false;
        seenJobs.add(key);
        return true;
      })
      .sort(
        (a, b) =>
          Number(activeJobStates.has(b.status)) - Number(activeJobStates.has(a.status)),
      )
      .slice(0, 2);
    const signature = JSON.stringify(relevant);
    const visibleJobs = relevant.some((j) => activeJobStates.has(j.status))
      ? relevant.filter((j) => activeJobStates.has(j.status))
      : relevant.slice(0, 1);
    refreshStatus.visibleJobs = visibleJobs;
    window.studioFeatures = data.features || {};
    renderRemainingDrawingSettings(window.activeStudioJob);
    window.refreshStoryControls?.();
    document.querySelectorAll("[data-start-job]").forEach(b => {
      b.disabled = Boolean(window.activeStudioJob) || b.dataset.unavailable === "true" || b.hasAttribute("aria-busy");
      b.title = window.activeStudioJob ? "请先继续或取消顶部的任务" : "";
    });
    renderJobCards(
      visibleJobs.map((j) => (
          j.status === "running" && ["启动语言模型", "启动 FlashNext"].includes(j.label)
            ? { ...j, message: data.model.message || j.message }
            : j
        )),
    );
    $("jobs").classList.toggle(
      "has-running",
      relevant.some((j) => activeJobStates.has(j.status)),
    );
    if (signature !== lastJobs) {
      const previous = lastJobs ? JSON.parse(lastJobs) : [];
      const finished = relevant.find(
        (j) =>
          ["done", "failed", "interrupted", "cancelled"].includes(j.status) &&
          previous.some((old) => old.id === j.id && ["running", "pausing", "cancelling"].includes(old.status)),
      );
      lastJobs = signature;
      if (finished && finished.project === pid && !dirty && !newMode) {
        saveWorkspace();
        snap = await api("/api/project?id=" + pid);
        if (["生成文字分镜", "生成整部分镜"].includes(finished.label) && stage === "page") {
          target = finished.result?.target || target;
          renderStage();
        } else if (stage === "prompt") await window.refreshPromptResults?.();
        else if (stage === "drawing") renderDrawing();
        else if (stage === "problems") await renderStage();
        else if (!["script", "finished"].includes(stage)) renderCandidates();
      }
    }
  } catch (e) {
    // Rendering/project refresh errors must not masquerade as a lost heartbeat.
    console.error("刷新工作室状态失败", e);
  } finally {
    refreshStatus.pending = false;
  }
}
$("projects").onchange = safe(async () => {
  await preserve();
  stage = "script";
  target = "";
  await openProject($("projects").value);
});
$("new-project").onclick = safe(async () => {
  if ($("new-project").disabled) return;
  $("new-project").disabled = true;
  try {
  await preserve();
  clearTimeout(workspaceTimer);
  if (pid?.startsWith("tmp-")) await api("/api/action", {action:"release_prompt",project:pid});
  else if (workspaceProject() && stage!=="prompt") await api("/api/action", {action:"save_workspace", project:workspaceProject(), state:structuredClone(workspaceState)});
  let previous={}, result=null;
  try {previous=JSON.parse(localStorage.getItem("studio-workspace-new")||"{}");} catch {}
  result = newMode ? workspaceState.storyResult || null : null;
  try {result ||= JSON.parse(localStorage.getItem("story-result-new")||"null");} catch {}
  const fresh = await api("/api/action", {action:"new_workspace", previous_state:previous, draft_result:result});
  restoringWorkspace = true;
  clearTimeout(workspaceTimer); clearTimeout(scrollSave);
  newMode = true;
  snap = null; pid = null; dirty = false; valueBaseHash = null;
  stage = "script";
  target = "";
  workspaceState = fresh;
  window.flushLoraSettings = null;
  cacheLocal("studio-workspace-new", JSON.stringify(fresh));
  localStorage.removeItem("story-result-new");
  cacheLocal("studio-last-view", "new");
  $("projects").value = "";
  await renderStage();
  window.scrollTo(0, 0);
  notify("已打开空白新作品。之前未完成的输入可从「找回输入草稿」恢复。");
  } finally {$("new-project").disabled = false;}
});
$("import-prompts").onclick = safe(() => window.openDirectPromptImport());
function enterPromptBoard() {
  if(typeof openPromptBoard==="function")return openPromptBoard();
  notify("请先保存临时图片，然后重启工作室后台以使用分镜出图。",true);
}
$("import-story").onclick = safe(() => window.openStoryImport());
$("prompt-entry").onclick = safe(async () => {
  await preserve();
  clearTimeout(workspaceTimer);
  // The task timeline is an overlay. Reuse its underlying prompt editor so
  // adding the next card keeps the user's latest text and LoRA selections.
  if(!snap?.project.prompt_board) {enterPromptBoard();return;}
  stage = "prompt"; target = "";
  await renderStage();
});
$("manage-projects").onclick = safe(async () => {
  await preserve();
  await reloadProjects();
  const body=$("dialog-body"), selected=new Set(), items=[...boot.projects];
  body.replaceChildren(node("h2","管理作品"),node("p","多选作品后移入回收站，可随时恢复。运行中的任务继续执行，进度和结果保留。提示词临时出图不进入作品库。"));
  const tools=node("div",undefined,"project-manager-tools"), search=node("input");
  search.type="search"; search.placeholder="搜索作品名称"; search.setAttribute("aria-label","搜索作品");
  const total=node("span"), list=node("div",undefined,"project-manager-list"), confirmation=node("div");
  const remove=button("删除所选",()=>{
    confirmation.replaceChildren(node("p",`将所选 ${selected.size} 个作品移入回收站？`));
    const confirm=button("确认删除所选作品",async()=>{
      confirm.disabled=true;
      try {
        clearTimeout(workspaceTimer);
        if(selected.has(pid) && !newMode && stage!=="prompt")
          await api("/api/action", {action:"save_workspace",project:pid,state:structuredClone(workspaceState)});
        const result=await api("/api/action",{action:"delete_projects",confirm:true,
          items:items.filter(p=>selected.has(p.id)).map(p=>({id:p.id,base_hash:p.hash}))});
        for(const p of result.deleted) localStorage.removeItem("studio-workspace-"+p.id);
        if(selected.has(pid)){
          pid=null;snap=null;newMode=true;dirty=false;stage="script";target="";workspaceState={views:{}};
          localStorage.removeItem("studio-last-project");cacheLocal("studio-last-view","new");
          await renderStage();
        }
        await reloadProjects();$("dialog").close();
        notify(`已将 ${result.deleted.length} 个作品移入回收站。`);
      } finally {confirm.disabled=false;}
    },"danger");
    confirmation.append(confirm,button("取消",()=>confirmation.replaceChildren()));
  },"danger");
  const matches=()=>items.filter(p=>p.title.toLowerCase().includes(search.value.trim().toLowerCase()));
  function render(){
    confirmation.replaceChildren(); list.replaceChildren();
    total.textContent=`已选 ${selected.size} / ${items.length} 个作品`;remove.disabled=!selected.size;
    const visible=matches();
    if(!visible.length)list.append(node("p",items.length?"没有匹配的作品":"还没有保存的作品"));
    for(const p of visible){
      const row=node("div",undefined,"project-manager-row"), label=node("label"), check=node("input");
      check.type="checkbox";check.checked=selected.has(p.id);check.setAttribute("aria-label","选择作品："+p.title);
      check.onchange=()=>{check.checked?selected.add(p.id):selected.delete(p.id);render();};
      label.append(check,node("span",p.title),node("small",`${p.quick_sample?"历史试画":"剧本作品"} · ${p.pages} 页 · v${p.version}`));
      row.append(label,button("打开",async()=>{$("dialog").close();await openProject(p.id);}));list.append(row);
    }
  }
  search.oninput=render;
  tools.append(search,button("全选当前结果",()=>{matches().forEach(p=>selected.add(p.id));render();}),
    button("取消全选",()=>{selected.clear();render();}),total,remove);
  body.append(tools,confirmation,list);render();$("dialog").showModal();
});
$("delete-project").onclick = safe(async () => {
  if (newMode || !snap || !pid) return notify("请先选择一个已保存的作品。", true);
  const project = pid, hash = snap.hash, title = snap.project.brief.title;
  const body = $("dialog-body");
  body.replaceChildren(node("h2", `删除《${title}》？`));
  body.append(node("p", "作品会移入回收站。运行中的任务继续执行，暂停及待确认状态保持，剧本、图片、进度和编辑记录全部保留，可以恢复。此操作不会释放磁盘空间。"));
  const actions = node("div", undefined, "row");
  actions.append(button("取消", () => $("dialog").close()));
  const remove = button("确认移入回收站", safe(async () => {
    remove.disabled = true;
    try {
      await preserve();
      clearTimeout(workspaceTimer);
      await api("/api/action", {action:"save_workspace", project, state:structuredClone(workspaceState)});
      await api("/api/action", {action:"delete_project", project, base_hash:hash, confirm:true});
      clearTimeout(workspaceTimer);
      pid = null; snap = null; dirty = false; newMode = true;
      workspaceState = {views:{}}; stage = "script"; target = "";
      localStorage.removeItem("studio-last-project");
      cacheLocal("studio-last-view", "new");
      $("dialog").close();
      await reloadProjects();
      if (boot.projects.length) await openProject(boot.projects[0].id);
      else {await loadWorkspace(); await renderStage();}
      notify("作品已移入回收站，可随时恢复。");
    } finally {remove.disabled = false;}
  }));
  actions.append(remove); body.append(actions); $("dialog").showModal();
});
$("project-trash").onclick = safe(async () => {
  const items = await api("/api/trash"), body = $("dialog-body");
  body.replaceChildren(node("h2", "作品回收站"), node("p", "作品、任务进度和结果全部保留。运行中的任务继续执行；暂停和待确认状态不会被删除操作改变。"), button("刷新任务状态", () => $("project-trash").onclick()));
  if (!items.length) body.append(node("p", "回收站为空。"));
  for (const item of items) {
    const row = node("div", undefined, "card");
    row.append(node("strong", item.title), node("p", `删除时间：${item.deleted_at} · ${item.id}`));
    for (const job of item.jobs || []) row.append(jobProgress(job));
    const drawingLabels = {running:"绘图运行中",queued:"绘图排队中",submitted:"已提交绘图",uncertain:"绘图状态待确认"};
    if(item.drawing_states?.length) row.append(node("small", item.drawing_states.map(s=>drawingLabels[s]||s).join(" · ")));
    const restore = button("恢复作品", safe(async () => {
      restore.disabled = true;
      try {
        await preserve();
        await api("/api/action", {action:"restore_deleted_project", project:item.id});
        $("dialog").close();
        await openProject(item.id); notify("作品及所有历史已恢复。");
      } finally {restore.disabled = false;}
    }));
    row.append(restore); body.append(row);
  }
  if (!$("dialog").open) $("dialog").showModal();
});
$("sample").onclick = safe(async () => {
  await preserve();
  const r = await act("create", { sample: true });
  stage = "page";
  target = "P001";
  await openProject(r.project);
});
$("page-select").onchange = safe(async () => {
  await preserve();
  target = $("page-select").value;
  renderStage();
});
$("panel-select").onchange = safe(async () => {
  await preserve();
  target = $("panel-select").value || $("page-select").value;
  renderStage();
});
for (const x of ["form", "json", "read"])
  $(x + "-tab").onclick = safe(() => showMode(x));
$("json-editor").oninput = () => (dirty = true);
$("save-draft").onclick = safe(async () => {
  await act("save_draft", {
    stage: actualStage(),
    target,
    payload: getValue(),
    base_hash: snap.hash,
  });
  dirty = false;
  snap = await api("/api/project?id=" + pid);
  notify("草稿已保存，尚未确认应用。");
});
$("propose").onclick = safe(async () => {
  if (newMode) {
    const r = await act("create", { brief: getValue() });
    await openProject(r.project);
    notify("作品已创建。下一步：角色与世界观。");
    return;
  }
  await act("propose", {
    stage: actualStage(),
    target,
    payload: getValue(),
    base_hash: snap.hash,
  });
  dirty = false;
  snap = await api("/api/project?id=" + pid);
  if (stage === "drawing") renderDrawing();
  else renderCandidates();
  $("candidates").scrollIntoView({ behavior: "smooth" });
});
$("generate").onclick = safe(async () => {
  await preserve();
  if (stage === "page") {
    await act("storyboard", {
      target,
      instruction: $("instruction").value,
      base_hash: snap.hash,
    });
    notify("已提交分镜候选，确认后才应用。");
    return;
  }
  let instruction = $("instruction").value;
  if (stage === "outline")
    instruction = `选择方案 ${$("option-select").value}。` + instruction;
  await act("generate", {
    stage: actualStage(),
    target,
    instruction,
    base_hash: snap.hash,
  });
  notify("已提交本地生成任务；可查看其他阶段。");
  await refreshStatus();
});
$("start-model").onclick = safe(async () => {
  await act("start_model");
  notify("已提交分析模型加载任务，请留意任务状态。");
});
$("stop-model").onclick = safe(async () => {
  await act("stop_model");
  await refreshStatus();
  notify("本项目语言模型已卸载。");
});
$("fork").onclick = safe(async () => {
  await preserve();
  if (stage === "script") {
    notify("编辑剧本后重新整理，会创建独立作品并保留当前作品。");
    return;
  }
  const from =
    stage === "page" || stage === "audit" || stage === "drawing" || stage === "problems" || stage === "finished"
      ? "pages"
      : stage;
  const r = await act("fork", { reset_from: from });
  stage = from === "pages" ? "page" : from;
  target = "";
  await openProject(r.project);
  notify("已创建独立分支；原作品的确认内容全部保留。");
});
$("history").onclick = safe(async () => {
  const body = $("dialog-body");
  body.replaceChildren(node("h2", "历史版本"));
  for (const h of snap.history) {
    const row = node("div", undefined, "candidate");
    row.append(
      node("h3", "版本 " + h.version),
      button("查看此版本", async () => {
        const p = await api(`/api/history?id=${pid}&revision=${h.id}`);
        row.append(readObject(p));
      }),
      button("恢复为新版本", async () => {
        await act("restore", { revision: h.id, base_hash: snap.hash });
        $("dialog").close();
        await openProject(pid);
        notify("历史内容已恢复为新版本；恢复前版本仍保留。");
      }),
    );
    body.append(row);
  }
  if (!snap.history.length)
    body.append(node("p", "尚无历史版本。每次确认前会自动保留旧版。"));
  $("dialog").showModal();
});
$("export").onclick = safe(async () => {
  const r = await act("export");
  const body = $("dialog-body");
  body.replaceChildren(
    node("h2", "导出完成"),
    node(
      "p",
      "JSON 是正式数据源；Markdown 是同版本阅读副本。修改后请在编辑器提交候选，再重新导出。",
    ),
  );
  for (const file of r.files) {
    const p = node("p");
    const a = node("a", file);
    a.href = `/api/export?id=${pid}&bundle=${r.bundle}&file=${file}`;
    a.target = "_blank";
    p.append(a);
    body.append(p);
  }
  $("dialog").showModal();
});
$("close-dialog").onclick = () => $("dialog").close();
$("dialog").addEventListener("close",()=>{if(workspaceState.characterEditor){workspaceState.characterEditor.open=false;saveWorkspace();}});
$("character-library").onclick = safe(() => window.openCharacterLibrary());
window.addEventListener("beforeunload", saveWorkspace);
// Restore the previous editor only when the user chooses to return to it.
// Opening the home page must not read or save any editor drafts.
window.resumeStudioWorkspace = async function() {
  if (document.body.dataset.stage) return;
  if(localStorage.getItem("studio-last-view")==="new"){
    newMode=true;snap=null;stage="script";target="";await reloadProjects(false);await loadWorkspace();await renderStage();
  } else if (boot.projects.length) {
    const preferred = localStorage.getItem("studio-last-project") ||
      boot.jobs.find((job) => activeJobStates.has(job.status) && job.project)?.project;
    pid = boot.projects.find((project) => project.id === preferred)?.id || boot.projects[0].id;
    await openProject(pid, false);
  } else {
    newMode = true;
    await loadWorkspace();
    await renderStage();
  }
};
async function startStudio() {
  boot = await api("/api/bootstrap");
  catalogCache.set("/api/projects", {expires:Date.now()+5000, promise:Promise.resolve(structuredClone(boot.projects))});
  await window.showStudioHome({refresh:false});
  // Model probes and task progress must never hold up the home screen.
  void refreshStatus();
  setInterval(refreshStatus, 1000);
}
async function bootStudio() {
  try {await startStudio();}
  catch (error) {
    const home = $("studio-home");
    home.replaceChildren(node("h1", "暂时无法打开工作室"),
      node("p", error.message), button("重新连接", bootStudio, "primary"));
  }
}
void bootStudio();
