"use strict";
function legacyDrawingSettings(data, projectId) {
  return {mode:localStorage.getItem("lora-mode-"+projectId)||"characters",checkpoint:data.comfy.configured,
    items:(data.comfy.loras||[]).map(file=>{
      const label=data.lora_labels?.[file]||{};
      return {file,name:label.name||file.split("/").pop().replace(/\.safetensors$/i,""),
        ...("character_id" in label?{character_id:label.character_id}:{}),character:label.character||"",
        characters:label.character?[label.character]:[],scope:label.character?"characters":"story",
        selected:localStorage.getItem("lora-use-"+projectId+"-"+file)==="yes",weight:window.defaultLoraWeight(file,data,0.7),
        trigger_words:label.trigger_words||"",confirmed:data.lora_inspection?.[file]?.structurally_compatible===true||!!label.confirmed};
    })};
}
function lorasForPanel(prefs,project,panel) {
  if(prefs.mode==="none")return [];
  const cast=panel.characters.map(c=>project.canon.characters.find(x=>x.id===c.character_id)).filter(Boolean);
  const refs=project.brief.character_refs||[];
  return prefs.items.filter(i=>{
    if(i.incompatible)return false;
    const role=("character_id" in i)?!!i.character_id:i.scope==="characters";
    const matches=cast.some(c=>i.character_id?i.character_id==="project:"+c.id||refs.some(r=>r.name===c.name&&r.id===i.character_id):(i.characters||[i.character]).includes(c.name));
    return role?matches&&(prefs.mode==="characters"||i.selected):i.selected;
  }).map(i=>({file:i.file,label:i.name,trigger_words:i.trigger_words||"",weight:i.weight,
    ...("character_id" in i?{character_id:i.character_id,character:i.character||""}:{scope:i.scope,characters:i.characters||[i.character||""]}),
    clip_weight:i.weight,compatible_checkpoint:prefs.checkpoint,confirmed_compatibility:i.confirmed}));
}
window.renderLoraSettings=async function(root,project,projectId,onChange=()=>{}) {
  const [data, characterData, projectSettings]=await Promise.all([
    api("/api/drawing-options"), api("/api/characters"),
    projectId ? api("/api/drawing-settings?id="+encodeURIComponent(projectId)) : null]);
  data.drawing_settings=projectSettings;
  const cards=characterData.characters;
  const chars=[...cards,...(project?.canon?.characters||[]).filter(c=>!cards.some(x=>x.name===c.name)).map(c=>({...c,id:"project:"+c.id}))];
  const key="comic-drawing-settings-"+(projectId||"new");let local;try{local=JSON.parse(localStorage.getItem(key));}catch{}
  const prefs=data.drawing_settings||local||legacyDrawingSettings(data,projectId);
  for(const item of prefs.items)item.file=resolveLoraFile(item.file,data);
  for(const item of legacyDrawingSettings(data,projectId).items)if(!prefs.items.some(x=>x.file===item.file))prefs.items.push(item);
  prefs.items=prefs.items.filter(i=>data.comfy.loras.includes(i.file));
  root.replaceChildren();const section=node("details",undefined,"collection");section.open=true;section.append(node("summary","LoRA · 风格与角色"));root.append(section);
  const status=node("small"),toolbar=node("div",undefined,"lora-tools"),mode=node("select"),model=node("select");
  const option=(s,v,t)=>{const o=node("option",t);o.value=v;s.append(o);};
  for(const [v,t] of [["none","不用 LoRA"],["manual","仅使用勾选项"],["characters","自动匹配出场角色"]])option(mode,v,t);
  mode.value=prefs.mode;mode.setAttribute("aria-label","LoRA 模式");
  for(const file of data.comfy.checkpoints)option(model,file,data.diffusion_models?.[file]?.label||file);model.value=prefs.checkpoint;model.setAttribute("aria-label","绘图模型");
  let saving=Promise.resolve(),saveError=null;
  function persist(){
    cacheLocal(key,JSON.stringify(prefs));const snapshot=structuredClone(prefs);
    onChange(snapshot);
    status.textContent=projectId?"正在保存…":"已暂存，随新作品保存。";
    if(projectId)saving=saving.then(async()=>{try{await act("save_drawing_settings",{project:projectId,settings:snapshot});saveError=null;status.textContent="已保存";}catch(e){saveError=e;status.textContent="保存失败："+e.message;}});
  }
  const sizeBox=node("section",undefined,"collection"),sizeFields=node("div",undefined,"image-size-fields");
  sizeBox.append(node("h3","画格尺寸"),sizeFields);root.prepend(sizeBox);
  const imageSize=createImageSizeControls(sizeFields,prefs,{comic:true,onChange:()=>{
    Object.assign(prefs,imageSize.read());persist();
  }});
  sizeBox.append(node("small","先选择画格纵横比，再选择分辨率；“按各格分镜比例”保留每格构图。此设置用于画格出图，整页排版尺寸不变。"));
  const consistencyLabel=node("label"),consistency=node("input"),consistencyNote=node("small","独立于下方手动勾选：不选角色也会加载；选中一个有参考图的角色时附加原图参考。无参考图时不锁定具体外观。");
  consistency.type="checkbox";consistency.checked=prefs.consistency_lora??data.comfy.consistency_enabled??true;
  consistencyLabel.append(consistency,document.createTextNode("自动加载一致性 LoRA · Anima Edit v2"));
  consistency.onchange=()=>{prefs.consistency_lora=consistency.checked;persist();};
  section.append(consistencyLabel,consistencyNote);
  const referenceLabel=node("label","指定角色参考图（该角色出镜时使用）"),referenceCharacter=node("select");
  option(referenceCharacter,"","不指定，沿用自动设置");
  for(const c of project?.canon?.characters||[]){
    const saved=(project?.brief?.character_refs||[]).find(r=>r.name.toLowerCase()===c.name.toLowerCase());
    if(saved?.image_id)option(referenceCharacter,c.id,c.name);
  }
  referenceCharacter.value=prefs.reference_character_id||"";
  referenceCharacter.onchange=()=>{prefs.reference_character_id=referenceCharacter.value;if(referenceCharacter.value){prefs.consistency_lora=true;consistency.checked=true;}persist();};
  referenceLabel.append(referenceCharacter);section.append(referenceLabel,node("small","指定后也适用于导入提示词与多人画面；参考图作用于整幅画面，不限定人物区域。"));
  toolbar.append(mode,model);section.append(toolbar,node("small","关联角色后，按该角色是否出场加载 LoRA。每张最多 5 个 LoRA。"));
  const picker=createLoraPicker(section,data,prefs.items,chars,()=>{if(mode.value==="none"){mode.value="manual";prefs.mode="manual";}persist();});
  function modelCompatibility(){
    imageSize.setAlignment(data.diffusion_models?.[model.value]?.family==="moss_image"?16:8);
    consistency.disabled=model.value!=="animaDeltaMix38BQwen0_deltaMixV60.safetensors";
    consistencyLabel.hidden=consistencyNote.hidden=consistency.disabled;
    referenceCharacter.disabled=consistency.disabled;
    picker.setCheckpoint(model.value);
  }
  modelCompatibility();
  mode.onchange=()=>{prefs.mode=mode.value;persist();};model.onchange=()=>{prefs.checkpoint=model.value;modelCompatibility();persist();};
  section.append(status,node("small","LoRA 随绘图模型自动切换，只显示与当前模型架构匹配的文件。角色关联继续保留。"));
  const control={read:()=>structuredClone(prefs),flush:async()=>{await picker.flush();await saving;if(saveError)throw saveError;}};
  window.flushLoraSettings=control.flush;return control;
};
window.renderComfy = async function (root, ctx) {
  const renderToken = root.dataset.renderToken;
  const { pid, snap, api, act, node, button, notify } = ctx;
  const [data, problemState] = await Promise.all([api("/api/drawing?id=" + pid), api("/api/problems?id=" + pid)]);
  const problemItems = new Map(problemState.items.map(item => [item.panel_id,item]));
  const p = snap.project;
  if (root.dataset.renderToken !== renderToken) return;
  root.className = "drawing-studio";
  root.replaceChildren(
    node("h2", "绘制与选图"),
    node(
      "p",
      "每格选一张喜欢的图。已生成的候选可直接查看，选择后自动保存。",
    ),
  );
  const prefs = data.drawing_settings || legacyDrawingSettings(data, pid);
  const cp = { value: prefs.checkpoint || data.comfy.configured };
  function chosenLoras(panel) {
    return lorasForPanel(prefs, p, panel);
  }
  root.append(
    node(
      "p",
      "默认每格 2 张。确认提示词后原样出图，使用第一步保存的 LoRA；不自动改写、审核或补画，由你选图。",
    ),
  );
  const allPanels = Object.values(p.pages).flatMap((page) => page.panels);
  const pageSections = new Map();
  const panelPickers = new Map();
  const panelRenderers = new Map();
  function ensurePanel(id) {
    const render = panelRenderers.get(id);
    if (render) {render();panelRenderers.delete(id);}
  }
  let currentPage = (ctx.activePanel || "").split("-")[0];
  const pagesPane = node("div", undefined, "drawing-pages");
  const pageTools = node("div", undefined, "drawing-page-tools");
  const fullscreenButton = button("全屏选图", () => {
    const placeholder = document.createComment("drawing-pages");
    const scrollTop = pagesPane.scrollTop;
    const dialog = node("dialog", undefined, "drawing-fullscreen");
    dialog.setAttribute("aria-label", "全屏选择候选图片");
    const toolbar = node("div", undefined, "drawing-fullscreen-toolbar");
    function exitFullscreen() {
      if (placeholder.isConnected) placeholder.replaceWith(pagesPane);
      dialog.close();
      dialog.remove();
      pagesPane.scrollTop = scrollTop;
      fullscreenButton.focus({preventScroll:true});
    }
    const exit = button("← 退出全屏", exitFullscreen);
    toolbar.append(exit, node("span", "全屏选图 · 按 Esc 退出"));
    dialog.addEventListener("cancel", event => {
      if (event.target !== dialog) return;
      event.preventDefault();
      exitFullscreen();
    });
    dialog.addEventListener("click", event => {
      const link = event.target.closest(".candidate-thumb, .candidate-info a");
      if (!link || !pagesPane.contains(link)) return;
      event.preventDefault();
      const viewer = node("dialog", undefined, "drawing-image-preview");
      viewer.setAttribute("aria-label", "候选图片放大预览");
      const previewToolbar = node("div", undefined, "drawing-fullscreen-toolbar");
      const image = node("img");
      image.src = link.href;
      image.alt = link.querySelector("img")?.alt || link.textContent;
      function closePreview() {
        viewer.close();
        viewer.remove();
        link.focus({preventScroll:true});
      }
      const back = button("← 返回选图", closePreview);
      previewToolbar.append(back, node("span", "图片预览 · 按 Esc 返回选图"));
      viewer.append(previewToolbar, image);
      viewer.addEventListener("cancel", event => {
        event.preventDefault();
        event.stopPropagation();
        closePreview();
      });
      dialog.append(viewer);
      viewer.showModal();
      back.focus({preventScroll:true});
    });
    pagesPane.before(placeholder);
    dialog.append(toolbar, pagesPane);
    root.append(dialog);
    dialog.showModal();
    pagesPane.scrollTop = 0;
    exit.focus({preventScroll:true});
  }, "drawing-fullscreen-enter");
  fullscreenButton.setAttribute("aria-haspopup", "dialog");
  const markProblemButton = button("标记有问题", async () => {
    const panel = p.pages[currentPage]?.panels.find(item => item.id === ctx.activePanel);
    if (!panel) throw Error("请先选择格子。");
    const marked = problemItems.has(panel.id);
    const image = data.images.selections[panel.id]?.image;
    const result = await act("save_problem", {panel_id:panel.id,marked:!marked,
      ...(!marked && image ? {image_id:image.id} : {})});
    problemItems.clear();result.items.forEach(item=>problemItems.set(item.panel_id,item));
    updatePageRedraw();updateUnselectedNavigation();
    const moved = !marked && goNextPanel(panel.id);
    notify(marked ? "已解除问题标记。" : moved ? "已标记有问题，继续查看下一格。" : "已标记，所有格子已检查完，可进入「问题格子」统一处理。");
  }, "drawing-page-redraw");
  const problemsShortcut=button("问题格子汇总",()=>window.openProblemPanels());
  function updatePageRedraw() {
    const panel = p.pages[currentPage]?.panels.find(item => item.id === ctx.activePanel);
    const marked = panel && problemItems.has(panel.id);
    markProblemButton.dataset.unavailable = String(!panel);
    markProblemButton.disabled = !panel;
    markProblemButton.textContent = marked ? "✓ 已标记有问题 · 取消标记" : "标记有问题";
    markProblemButton.setAttribute("aria-pressed",String(Boolean(marked)));
    markProblemButton.title=panel?`标记 ${panel.id}，稍后统一处理`:"请先选择格子";
    problemsShortcut.textContent=`问题格子汇总 · ${problemItems.size} 格`;
  }
  const pageActions = node("div", undefined, "drawing-page-actions");
  pageActions.append(fullscreenButton, markProblemButton, problemsShortcut);
  const pagePicker = node("select"); pagePicker.setAttribute("aria-label", "绘图页面");
  const unselectedNavigation = node("div", undefined, "unselected-navigation");
  unselectedNavigation.setAttribute("role", "group");
  unselectedNavigation.setAttribute("aria-label", "跳至未选择格子");
  const previousUnselected = button("← 上一个未选择格子", () => goUnselectedPanel(ctx.activePanel, -1));
  const nextUnselected = button("下一个未选择格子 →", () => goUnselectedPanel(ctx.activePanel, 1));
  const unselectedStatus = node("small");
  unselectedStatus.setAttribute("aria-live", "polite");
  const finishShortcut=button("选好了，检查问题格子",()=>window.openProblemPanels(),"primary finish-shortcut");finishShortcut.hidden=true;
  unselectedNavigation.append(previousUnselected, nextUnselected, unselectedStatus,finishShortcut);
  function isUnselectedPanel(panel) {
    if (problemItems.has(panel.id)) return false;
    const selection = data.images.selections[panel.id];
    return !selection?.image?.id || selection.panel_hash !== data.panel_hashes?.[panel.id];
  }
  function findUnselectedPanel(panelId, direction) {
    const at = allPanels.findIndex(panel => panel.id === panelId);
    const start = at < 0 ? (direction > 0 ? -1 : 0) : at;
    const limit = at < 0 ? allPanels.length : allPanels.length - 1;
    for (let step = 1; step <= limit; step++) {
      const panel = allPanels[(start + direction * step + allPanels.length) % allPanels.length];
      if (isUnselectedPanel(panel)) return panel;
    }
    return null;
  }
  function updateUnselectedNavigation(panelId = ctx.activePanel) {
    const remaining = allPanels.filter(isUnselectedPanel).length;
    previousUnselected.disabled = !findUnselectedPanel(panelId, -1);
    nextUnselected.disabled = !findUnselectedPanel(panelId, 1);
    finishShortcut.hidden=!allPanels.length||remaining>0;
    previousUnselected.hidden=nextUnselected.hidden=!finishShortcut.hidden;
    unselectedStatus.textContent = !allPanels.length ? "暂无画格" : !remaining ? "全部已选好" :
      remaining === 1 && previousUnselected.disabled ? "只剩当前格未选择" : `还剩 ${remaining} 格未选择 · 可跨页循环跳转`;
  }
  for (const page of Object.values(p.pages)) {
    const option = node("option", page.id + " · " + page.panels.length + " 格");
    option.value = page.id; pagePicker.append(option);
  }
  function showPage(id) {
    currentPage = pageSections.has(id) ? id : pageSections.keys().next().value;
    pagePicker.value = currentPage || "";
    for (const [key, section] of pageSections) section.hidden = key !== currentPage;
    updatePageRedraw();
  }
  function showPanel(id) {
    const pageId = id?.split("-")[0];
    if (!pageSections.has(pageId)) return;
    ensurePanel(id);
    ctx.activePanel = id;
    showPage(pageId);
    panelPickers.get(pageId).value = id;
    for (const article of pageSections.get(pageId).querySelectorAll(".panel-choice")) {
      article.hidden = article.id !== "choice-" + id;
      article.open = !article.hidden;
    }
    updateUnselectedNavigation(id);
  }
  pagePicker.addEventListener("change", () => {
    showPage(pagePicker.value);
    const panel = p.pages[currentPage]?.panels[0];
    if (panel) {ctx.activePanel = panel.id; showPanel(panel.id);}
    window.saveWorkspace?.();
  });
  pageTools.append(node("strong", "候选图片与排版"), pagePicker, pageActions, unselectedNavigation);
  pagesPane.append(pageTools);
  const failures = data.images.batches.flatMap(b => b.images).filter(i => i.review?.verdict === "reject");
  if (failures.length) {
    root.append(button(`查看历史淘汰图（${failures.length} 张）`, () => {
      for(const panel of allPanels)ensurePanel(panel.id);
      for (const d of root.querySelectorAll("details.panel-choice, details.failed-images")) d.open = true;
      root.querySelector("details.failed-images")?.scrollIntoView({behavior:"smooth", block:"start"});
    }));
  }
  const selectedCount = allPanels.filter(
    (panel) =>
      data.images.selections[panel.id]?.panel_hash ===
      data.panel_hashes?.[panel.id],
  ).length;
  const advanced = node("details", undefined, "drawing-advanced");
  advanced.append(node("summary", "高级选项"), node("p", "这些高级参数只影响新任务；暂停任务的后续数量可在左侧调整。"));
  const candidateCount = node("select"); candidateCount.dataset.editKey = "literal-candidate-count-v2";
  for (let n=1;n<=16;n++) {const opt=node("option", `${n} 张`);opt.value=String(n);candidateCount.append(opt);}
  candidateCount.value="2";
  const sharedCount = node("div", undefined, "drawing-shared-count");
  const countLabel = node("label", "每格生成数量");countLabel.append(candidateCount);
  const countHelp=node("small");
  sharedCount.append(countLabel,countHelp,
    node("p", "LoRA：第一步和这里共用设置，以最后一次选择为准；全图风格用于每张图，角色 LoRA 按出场人物加载。"));
  const steps = node("input");steps.type="number";steps.min="1";steps.max="100";steps.placeholder="模型默认";steps.dataset.editKey="batch-steps";
  const seed = node("input");seed.type="number";seed.min="0";seed.max="9007199254740991";seed.placeholder="随机";seed.dataset.editKey="batch-seed";
  for(const [text,control] of [["采样步数",steps],["起始种子",seed]]) {const label=node("label",text);label.append(control);advanced.append(label);}
  advanced.append(node("small", "种子留空为随机；填写后各候选按序递增。步数越多通常耗时越长。"));
  function batchOptions() {
    if(!steps.checkValidity() || !seed.checkValidity()) throw Error("请检查高级选项中的步数和种子范围。");
    return {candidate_count:Number(candidateCount.value), steps:steps.value===""?null:Number(steps.value), seed:seed.value===""?null:Number(seed.value)};
  }
  const batchSummary=node("div",undefined,"batch-summary");
  const batchScale=node("strong"),batchFormula=node("p");batchSummary.append(batchScale,batchFormula);
  function updateBatchEstimate() {
    const count=Number(candidateCount.value), remaining=allPanels.length-allPanels.filter(panel=>data.images.selections[panel.id]?.panel_hash===data.panel_hashes?.[panel.id]).length;
    batchScale.textContent=`${Object.keys(p.pages).length} 页 · ${allPanels.length} 个画格`;
    batchFormula.textContent=`${remaining} 格待选 × ${count} 张 = 本次最多 ${remaining*count} 张。旧图保留，不自动审核或补画。`;
  }
  candidateCount.addEventListener("change",updateBatchEstimate);updateBatchEstimate();
  window.syncDrawingQuantity = job=>{
    if(root.dataset.renderToken!==renderToken)return;
    const current=job?.project===pid && job.request?.action==="draw_imported" && ["running","pausing","paused","cancelling"].includes(job.status);
    if(current)candidateCount.value=String(window.pausedDrawingCount(job));
    candidateCount.disabled=Boolean(current && (job.status!=="paused" || !window.studioFeatures?.candidate_count_total || window.studioDisconnected));
    countHelp.textContent=current
      ? "当前任务数量；暂停后修改会自动保存，继续时按新数量执行。已生成图片计入每格总数并全部保留。"
      : "普通绘制与导入提示词出图共用；此处设置新任务每格数量。";
    updateBatchEstimate();
  };
  candidateCount.addEventListener("change",safe(async()=>{
    const job=window.activeStudioJob;
    if(job?.project===pid && job.request?.action==="draw_imported" && job.status==="paused") {
      await window.setPausedDrawingCount(job,Number(candidateCount.value));
      window.saveWorkspace?.();
    }
  }));
  window.syncDrawingQuantity(window.activeStudioJob);
  const batchButton = button(
    "开始绘制未完成画格",
    async () => {
      await window.confirmLiteralDrawing({act,node,button,notify}, "draw_all", {
        ...batchOptions(),
        base_hash: snap.hash,
        checkpoint: cp.value,
        loras_by_panel: Object.fromEntries(
          allPanels.map((panel) => [panel.id, chosenLoras(panel)]),
        ),
      });
    },
    "primary",
  );
  const missingPages = (p.plan?.pages || []).filter(
    (page) => !p.pages[page.id],
  );
  batchButton.disabled =
    !allPanels.length || missingPages.length > 0 || !cp.value;
  batchButton.dataset.startJob="true";batchButton.dataset.unavailable=String(batchButton.disabled);
  batchButton.disabled ||= Boolean(window.activeStudioJob);
  const selectionSummary=node("p");
  function updateSelectionCount(){selectionSummary.textContent=`共 ${allPanels.length} 格 · 已选 ${allPanels.filter(panel=>!isUnselectedPanel(panel)).length} 格 · 每格选一张，选择立即保存。`;updateUnselectedNavigation();}
  updateSelectionCount();root.prepend(batchSummary,batchButton);root.append(selectionSummary,advanced);
  const queueDrawing = button("加入任务列表 · 稍后绘制", async () => {
    const dialog=node("dialog",undefined,"queue-add-dialog");dialog.setAttribute("aria-label","安排漫画出图");
    const heading=node("div",undefined,"queue-heading");heading.append(node("h2","安排漫画出图"),button("返回修改",()=>dialog.close()));
    const scope=node("select"),scopeLabel=node("label","绘制范围"),queueCount=node("select"),countLabel=node("label","每格生成数量");
    scope.setAttribute("aria-label","排队绘制范围");queueCount.setAttribute("aria-label","排队每格生成数量");
    const remaining=allPanels.filter(panel=>isUnselectedPanel(panel));
    const ranges=[{id:"remaining",label:`尚未选好图片的画格 · ${remaining.length} 格`,panels:remaining},
      {id:"all",label:`整部漫画 · ${allPanels.length} 格`,panels:allPanels}];
    for(const page of Object.values(p.pages))ranges.push({id:page.id,label:`第 ${Number(page.id.replace(/\D/g,""))} 页 · ${page.panels.length} 格`,panels:page.panels});
    for(const panel of allPanels)ranges.push({id:panel.id,label:panel.id.replace(/^P0*(\d+)-K0*(\d+)$/,"第 $1 页 · 第 $2 格"),panels:[panel]});
    for(const range of ranges){const opt=node("option",range.label);opt.value=range.id;opt.disabled=!range.panels.length;scope.append(opt);}scope.value=remaining.length?"remaining":"all";
    for(const n of [1,2,4,8,16]){const opt=node("option",`${n} 张`);opt.value=String(n);queueCount.append(opt);}queueCount.value=candidateCount.value;
    scopeLabel.append(scope);countLabel.append(queueCount);const fields=node("div",undefined,"queue-range-fields");fields.append(scopeLabel,countLabel);
    const taskName=node("input"),nameLabel=node("label","任务名称（可选）","queue-name-field");taskName.maxLength=80;taskName.placeholder="例如：第一页 · 暖色版";taskName.setAttribute("aria-label","漫画排队任务名称");nameLabel.dataset.queueNameField="true";nameLabel.hidden=!window.lastStudioStatus?.queue?.can_rename;nameLabel.append(taskName);fields.append(nameLabel);
    const estimate=node("p",undefined,"queue-range-estimate");
    const updateEstimate=()=>{const n=ranges.find(r=>r.id===scope.value).panels.length;estimate.textContent=`本次 ${n} 格 × ${queueCount.value} 张 = ${n*Number(queueCount.value)} 张新候选。已有图片和选图保留。`;};
    scope.onchange=queueCount.onchange=updateEstimate;updateEstimate();
    const confirm=button("下一步 · 核对逐格提示词",async()=>{
      const targets=ranges.find(r=>r.id===scope.value).panels.map(panel=>panel.id);
      if(!targets.length)throw Error("请选择至少一个画格");
      await window.confirmLiteralDrawing({act,node,button,notify}, "queue_drawing", {
        ...batchOptions(),candidate_count:Number(queueCount.value),task_name:taskName.value,targets,force:scope.value!=="remaining",base_hash:snap.hash,checkpoint:cp.value});
      dialog.close();
    },"primary");
    dialog.append(heading,node("p","选择范围和数量，再逐格确认提示词。模型与 LoRA 使用第三步当前的最后选择。"),fields,estimate,confirm);
    dialog.onclose=()=>dialog.remove();document.body.append(dialog);dialog.showModal();
  });
  queueDrawing.disabled = !allPanels.length || missingPages.length > 0 || !cp.value;
  if (missingPages.length)
    root.append(
      node(
        "p",
        `还有 ${missingPages.length} 页分镜未确认，请先完成第二步。`,
        "warning",
      ),
    );
  const activePanel =
    ctx.activePanel ||
    allPanels.find(
      (panel) =>
        data.images.selections[panel.id]?.panel_hash !==
        data.panel_hashes?.[panel.id],
    )?.id ||
    allPanels[0]?.id;
  let savingSelection = false;
  function goNextPanel(panelId) {
    return goUnselectedPanel(panelId, 1);
  }
  function goUnselectedPanel(panelId, direction) {
    const next = findUnselectedPanel(panelId, direction);
    if (!next) return false;
    const current = document.getElementById("choice-" + panelId);
    const destination = document.getElementById("choice-" + next.id);
    if (!destination) return false;
    showPage(next.id.split("-")[0]);
    if (current) current.open = false;
    destination.open = true;
    ctx.activePanel = next.id;
    showPanel(next.id);
    panelPickers.get(next.id.split("-")[0])?.focus({preventScroll:true});
    destination.scrollIntoView({behavior:"instant",block:"start"});
    window.saveWorkspace?.();
    return true;
  }

  for (const page of Object.values(p.pages)) {
    const section = node("section", undefined, "drawing-page");
    pageSections.set(page.id, section);
    const layoutPane = node("details", undefined, "drawing-layout workspace-pane");
    section.append(node("h2", "第 " + Number(page.id.replace(/\D/g,"")) + " 页 · " + page.panels.length + " 个画格"));
    for (const panel of page.panels) {
      const article = node("details", undefined, "candidate panel-choice");
      article.dataset.persist = "panel-" + panel.id;
      article.id = "choice-" + panel.id;
      article.open = panel.id === activePanel;
      const selection = data.images.selections[panel.id];
      let selected =
        selection?.panel_hash === data.panel_hashes?.[panel.id]
          ? selection.image.id
          : null;
      article.append(
        node(
          "summary",
          `${panel.id} · ${panel.purpose} · ${selected ? "已选好" : "待选图"}`,
        ),
      );
      article.addEventListener("toggle", () => {
        if (article.open) {ensurePanel(panel.id);ctx.activePanel = panel.id;}
      });
      panelRenderers.set(panel.id,()=>{
      article.append(window.panelStoryPreview(p,panel.id,node));
      const scene = node("details", undefined, "panel-scene");
      scene.append(node("summary", "查看画格说明"), node("p", panel.drawing_description));
      const writing = p.drawing_prompts?.[panel.id]?.payload;
      if (writing) {
        const explanation = node("details", undefined, "prompt-explanation");
        explanation.dataset.persist = "prompt-" + panel.id;
        explanation.append(node("summary", "已保存的绘图说明与提示词"));
        const plan = writing.visual_plan;
        if (plan) {
          explanation.append(node("p", plan.detailed_scene));
          for (const [title, items] of [["必须画出的细节", plan.must_show], ["动作与物体关系", plan.relationships], ["仍需核对", plan.uncertain]]) {
            if (items?.length) {
              explanation.append(node("h4", title));
              const list = node("ul"); for (const item of items) list.append(node("li", item)); explanation.append(list);
            }
          }
        } else explanation.append(node("p", "这是已保存的提示词。出图前可核对并手动修改，不会由本地模型改写。"));
        explanation.append(node("h4", "已保存的正向提示词（出图前核对）"), node("p", writing.positive));
        article.append(explanation);
      }
      const batches = data.images.batches
        .filter((b) => b.panel_id === panel.id)
        .slice()
        .reverse();
      const current = batches.filter(
        (b) => b.panel_hash === data.panel_hashes?.[panel.id],
      );
      const latestMain = current.find(b=>b.render_options?.diagnostic !== "small_batch" && b.images.length);
      const importRun = latestMain?.render_options?.import_run;
      const mainBatches = importRun ? current.filter(b=>b.render_options?.import_run === importRun) : current.filter(b=>!b.render_options?.external_prompt);
      const usable = mainBatches
        .filter(b => b.render_options?.diagnostic !== "small_batch")
        .flatMap((b) => b.images)
        .filter(
          (image) => image.review?.verdict !== "reject",
        );
      const unique = new Map();
      // Keep the user's choice in its slot even when newer candidates exist.
      for (const image of [
        ...usable.filter((image) => image.id === selected),
        ...usable,
      ]) {
        const key = image.view_index || image.id;
        if (!unique.has(key)) unique.set(key, image);
      }
      const visible = [...unique.values()].sort((a,b)=>(a.view_index||0)-(b.view_index||0)).slice(0, 16);
      const grid = node("div", undefined, "image-grid choice-grid");
      grid.setAttribute("role", "group");
      grid.setAttribute("aria-label", panel.id + " 候选图片，选择一张");
      function imageCard(image, selectable = true) {
        const card = node(
          "article",
          undefined,
          "image-option" + (image.id === selected ? " selected" : ""),
        );
        card.dataset.imageId = image.id;
        card.dataset.unreviewed = String(image.review?.verdict === "pending");
        const img = node("img");
        const [bundle, file] = image.file.split("/");
        img.src = `/api/image?id=${pid}&bundle=${bundle}&file=${file}`;
        img.alt = `${panel.id} 候选 ${image.view_index || "未知"}`;
        img.loading = "lazy";
        const choice = node("button", image.id === selected ? "✓ 已选择" : image.review?.verdict === "pending" ? "选用此图（未审核）" : "选用此图", "choose-image");
        choice.type = "button";
        choice.disabled = !selectable;
        choice.setAttribute("aria-pressed", String(image.id === selected));
        choice.setAttribute(
          "aria-label",
          `${panel.id} 选择候选 ${image.view_index || "未知"} ${image.id.slice(-5)}`,
        );
        choice.onclick = safe(async () => {
          if (savingSelection) return;
          savingSelection = true;
          choice.disabled = true;
          try {
            await act("select_image", { target: panel.id, image: image.id, allow_rejected: image.review?.verdict === "reject", allow_unreviewed: image.review?.verdict === "pending" });
            selected = image.id;
            data.images.selections[panel.id] = {panel_hash:data.panel_hashes[panel.id], image};
            for(const other of article.querySelectorAll(".image-option")) {
              const chosen = other.dataset.imageId === image.id;
              other.classList.toggle("selected", chosen);
              const button = other.querySelector(".choose-image");
              button.textContent = chosen ? "✓ 已选择" : other.dataset.unreviewed === "true" ? "选用此图（未审核）" : "选用此图";
              button.setAttribute("aria-pressed", String(chosen));
            }
            updateSelectionCount();updateBatchEstimate();
            article.querySelector("summary").textContent = `${panel.id} · ${panel.purpose} · 已选好`;
            window.saveWorkspace?.();
            const moved = goNextPanel(panel.id);
            notify(moved ? "已保存，继续选择下一格。" : "已保存，全部画格已选好，可以查看成品。");
          } finally {
            savingSelection = false;
            choice.disabled = false;
          }
        });
        const verdict = {
          pass: "AI 核对通过",
          uncertain: "需你重点核对",
          unreviewed: "未自动审核 · 请自行核对",
          reject: "AI 判定不符 · 可手动选用",
          pending: "尚未审核 · 请自行核对",
        }[image.review?.verdict || "unreviewed"];
        const preview = node("a");preview.href=img.src;preview.target="_blank";preview.rel="noopener";preview.title="打开原图";preview.className="candidate-thumb";preview.append(img);
        const info = node("div", undefined, "candidate-info");
        info.append(
          node(
            "span",
            `候选 ${image.view_index || "?"} · ${verdict}`,
          ),
        );
        const details = node("details", undefined, "candidate-tools");
        details.append(node("summary", "高级 · 详情与修复"),node("small", "种子：" + (image.seed ?? "未记录")));
        if (image.review?.reason)
          details.append(node("small", image.review.reason));
        if (image.positive) {
          const actual = node("details"); actual.append(node("summary", "这张图实际使用的提示词"));
          const origin = batches.find(b => b.images.some(i => i.id === image.id));
          actual.append(node("p", "模型：" + (origin?.checkpoint || "历史记录未保存")), node("p", image.positive));
          if (image.negative) actual.append(node("p", "排除项：" + image.negative));
          details.append(actual);
        }
        const link=node("a","查看大图");link.href=img.src;link.target="_blank";link.rel="noopener";info.append(link);
        if(image.hand_detail){
          const detail=image.hand_detail,status={done:"已完成局部细化",no_hands:"未检测到手部",failed:"增强失败，保留原图",uncertain:"任务状态待确认，保留原图"};
          details.append(node("p","手部增强："+(status[detail.status]||detail.status)+(detail.error?" · "+detail.error:"")));
          const [bundle,file]=detail.base_file.split("/"),base=node("a","查看增强前原图");
          base.href=`/api/image?id=${encodeURIComponent(pid)}&bundle=${encodeURIComponent(bundle)}&file=${encodeURIComponent(file)}`;
          base.target="_blank";base.rel="noopener";details.append(base);
        }
        info.append(details);card.append(preview, info, choice, button("标记这格有问题",async()=>{
          const result=await act("save_problem",{panel_id:panel.id,image_id:image.id});
          problemItems.clear();result.items.forEach(item=>problemItems.set(item.panel_id,item));
          updatePageRedraw();updateUnselectedNavigation();
          const moved = goNextPanel(panel.id);
          notify(moved ? "已记录问题图，继续查看下一格。" : "已记录问题图，所有格子已检查完，可进入「问题格子」统一处理。");
        }));
        return card;
      }
      for (const image of visible) grid.append(imageCard(image));
      const samples = current.filter(b => b.render_options?.diagnostic === "small_batch");
      if (samples.length) {
        const result = node("details", undefined, "sample-results");
        result.dataset.persist = "samples-" + panel.id;
        result.open = false;
        result.append(node("summary", "少量试画结果 · 按模型对照"));
        const newest = samples[0].render_options.sample_job;
        const latest = samples.filter(b => b.render_options.sample_job === newest);
        const models = [...new Set(latest.map(b => b.checkpoint))];
        for (const model of models) {
          const group = node("section"); group.append(node("h3", catalog.find(m => m.name === model)?.label || model));
          const sampleGrid = node("div", undefined, "image-grid choice-grid");
          for (const b of latest.filter(b => b.checkpoint === model).reverse()) {
            for (const im of b.images) if (!["reject", "pending"].includes(im.review?.verdict)) sampleGrid.append(imageCard(im));
            if (b.error) group.append(node("p", "此张失败：" + b.error, "warning"));
          }
          group.append(sampleGrid); result.append(group);
        }
        const older = samples.filter(b => b.render_options.sample_job !== newest);
        if (older.length) {
          const history = node("details"); history.append(node("summary", "之前的试画（保留，可选）"));
          for (const b of older) {
            history.append(node("h4", b.checkpoint));
            const oldGrid = node("div", undefined, "image-grid choice-grid");
            for (const im of b.images) if (!["reject", "pending"].includes(im.review?.verdict)) oldGrid.append(imageCard(im));
            history.append(oldGrid);
          }
          result.append(history);
        }
        article.append(result);
      }
      article.append(node("p", `${importRun?"本次确认提示词 · ":""}本格 ${visible.length} 张候选 · 全部展开，点击图片看大图`, "gallery-count"), grid);
      if (!visible.length)
        article.append(
          node(
            "p",
            current.some((b) =>
              b.images.some((i) => i.review?.verdict === "reject"),
            )
              ? "本格暂无正常候选。可展开下方“AI 未通过与历史图片”，查看并手动选择失败图。"
              : "候选图片尚未就绪。点击上方一键绘制，完成后会自动显示在这里。",
            "warning",
          ),
        );
      const more = node("details");
      more.className = "failed-images";
      more.dataset.persist = "history-"+panel.id;
      more.append(
        node("summary", "高级选项 · 未通过与历史图片、重画"),
        node("p", "你可以手动选用 AI 未通过的图，原审核结论仍保留；这些图片不会进入上方正常候选区。"),
        button("按高级选项重画这一格", async () => {
          await window.confirmLiteralDrawing({act,node,button,notify}, "draw", {
            ...batchOptions(),
            target: panel.id,
            base_hash: snap.hash,
            checkpoint: cp.value,
            force: true,
            loras: chosenLoras(panel),
          });
        }),
      );
      more.append(scene);
      const explanation = article.querySelector(".prompt-explanation");
      if (explanation) more.append(explanation);
      const history = node("div", undefined, "image-grid choice-grid");
      let historyLoaded=false;
      more.addEventListener("toggle",()=>{
      if(!more.open || historyLoaded)return;
      const shown = new Set(visible.map((i) => i.id));
      for (const b of batches)
        for (const image of b.images)
          if (!shown.has(image.id)) {
            const canChoose =
              b.panel_hash === data.panel_hashes?.[panel.id];
            const item = imageCard(image, canChoose);
            if (b.panel_hash !== data.panel_hashes?.[panel.id])
              item.append(node("small", "旧版分镜，仅供查看"));
            history.append(item);
          }
      historyLoaded=true;
      });
      more.append(history);
      article.append(more);
      });
      section.append(article);
    }
    const layout = data.layouts[page.id];
    let presets;
    let presetRevision=0;
    const selector = node("select"),
      label = node("label", "页面版式");
    [
      ["story", "按分镜节奏（AI 大小分格）"],
      ["hero", "首格大画面＋细节格"],
      ["cinema", "宽窄交替"],
      ["balanced", "均衡分格"],
      ["vertical", "竖向条漫"],
    ].forEach(([v, t]) => {
      const o = node("option", t);
      o.value = v;
      selector.append(o);
    });
    label.append(selector);
    section.append(label);
    const canvas = node("canvas");
    canvas.width = 400;
    canvas.height = 566;
    canvas.className = "layout-preview";
    canvas.setAttribute("aria-label", page.id + " 大小分格预览");
    section.append(canvas);
    function previewLayout(l) {
      const c = canvas.getContext("2d");
      c.clearRect(0, 0, 400, 566);
      c.fillStyle = "white";
      c.fillRect(0, 0, 400, 566);
      const sx = 400 / l.width,
        sy = 566 / l.height;
      c.font = "14px sans-serif";
      for (const [key, r] of Object.entries(l.panels)) {
        c.fillStyle = "#e4eee7";
        c.fillRect(r.x * sx, r.y * sy, r.w * sx, r.h * sy);
        c.strokeStyle = "#41624b";
        c.strokeRect(r.x * sx, r.y * sy, r.w * sx, r.h * sy);
        c.fillStyle = "#172c20";
        c.fillText(key, r.x * sx + 8, r.y * sy + 22);
      }
    }

    const details = node("details", undefined, "collection");
    details.append(node("summary", "页面位置 / 气泡位置（像素，可编辑）"));
    const edit = node("textarea");
    edit.rows = 16;
    edit.dataset.editKey = "layout-"+page.id;
    edit.value = JSON.stringify(layout, null, 2);
    edit.setAttribute("aria-label", page.id + " 排版坐标");
    selector.onchange = safe(async () => {
      const revision=++presetRevision;
      presets ||= api(`/api/layout-presets?id=${pid}&page=${page.id}`).catch(error=>{presets=null;throw error;});
      const choices=await presets;
      if(revision!==presetRevision || root.dataset.renderToken!==renderToken)return;
      edit.value = JSON.stringify(choices[selector.value], null, 2);
      previewLayout(choices[selector.value]);
      window.saveWorkspace?.();
    });
    edit.oninput = () => {
      try {
        previewLayout(JSON.parse(edit.value));
      } catch {}
    };
    previewLayout(layout);
    details.append(
      node(
        "p",
        '默认铺满画格，减少留白。每格 fit 可设 cover（铺满）或 contain（完整显示），focus=[0.5,0.5] 调整裁切中心；lettering 按文字编号调整气泡位置。',
      ),
      edit,
    );
    section.append(details);
    section.append(
      button(
        "用已选图片排版成页",
        async () => {
          const r = await act("compose", {
            target: page.id,
            layout: JSON.parse(edit.value),
          });
          const results = node("div");
          const image = node("img");
          image.src = `/api/layout?id=${pid}&bundle=${r.bundle}&file=page.png`;
          image.alt = page.id + " 排版草稿";
          image.width = 600;
          results.append(image);
          for (const file of r.files) {
            const para = node("p");
            const a = node("a", file);
            a.href = `/api/layout?id=${pid}&bundle=${r.bundle}&file=${file}`;
            a.target = "_blank";
            para.append(a);
            results.append(para);
          }
          for (const w of r.warnings) results.append(node("p", w));
          layoutPane.append(results);
          notify("已生成页面 PNG、可编辑 SVG 和独立文字层。");
        },
        "primary",
      ),
    );
    const selectionPane = node("section", undefined, "drawing-selection workspace-pane");
    selectionPane.append(section.firstElementChild, ...section.querySelectorAll(":scope > .panel-choice"));
    const panelPicker = node("select"); panelPicker.setAttribute("aria-label", page.id + " · 选择画格");
    for (const panel of page.panels) {
      const option = node("option", panel.id + " · " + panel.purpose); option.value = panel.id; panelPicker.append(option);
    }
    panelPickers.set(page.id, panelPicker);
    panelPicker.addEventListener("change", () => {ctx.activePanel = panelPicker.value; showPanel(panelPicker.value); window.saveWorkspace?.();});
    const selectorRow = node("div", undefined, "drawing-selector");
    selectorRow.append(selectionPane.firstElementChild, panelPicker);selectionPane.prepend(selectorRow);
    layoutPane.prepend(node("summary", "页面排版与导出"));
    layoutPane.append(...Array.from(section.children));
    section.replaceChildren(selectionPane, layoutPane);
    pagesPane.append(section);
  }
  const controlsPane = node("section", undefined, "drawing-controls workspace-pane");
  const heading = [...root.children].find(x=>x.tagName === "H2");
  let exchangeControls = null;
  const loraSummary = node("small", undefined, "drawing-lora-summary");
  function updateLoraSettings(value) {
    Object.assign(prefs, value);
    cp.value = prefs.checkpoint || data.comfy.configured;
    updatePageRedraw();
    const selected = prefs.items.filter(item => item.selected && !item.incompatible);
    loraSummary.textContent = prefs.mode === "none" ? "当前：不用 LoRA" :
      (prefs.mode === "characters" ? "自动匹配出场角色" : "仅使用勾选项") +
      (selected.length ? " · " + selected.map(item => item.name || item.file).join("、") : " · 尚未勾选");
    exchangeControls?.setCheckpoint(cp.value);
  }
  updateLoraSettings(prefs);
  const chooseLoras = button("绘图设置 · 尺寸与 LoRA", async () => {
    await window.flushLoraSettings?.();
    const dialog = node("dialog", undefined, "drawing-lora-dialog");
    dialog.setAttribute("aria-label", "第三步绘图设置");
    const body = node("div");
    let control;
    const close = button("完成选择", async () => {await control.flush(); dialog.close();}, "primary");
    close.disabled = true;
    dialog.append(node("h2", "绘图设置 · 尺寸与 LoRA"),
      node("p", "与第一步共用设置，以最后一次选择为准。选择自动保存，接下来的出图使用最新设置。"), close, body);
    dialog.addEventListener("cancel", event => {event.preventDefault(); close.click();});
    dialog.addEventListener("close", () => dialog.remove());
    root.append(dialog); dialog.showModal();
    try {
      control = await window.renderLoraSettings(body, p, pid, updateLoraSettings);
      updateLoraSettings(control.read());
      close.disabled = false;
    } catch (error) {dialog.close(); throw error;}
  }, "drawing-choose-loras");
  const clearImages = button("一键清除所有已生成图片", async () => {
    if (window.activeStudioJob) throw Error("请等待当前任务结束，或取消任务后再清除图片");
    clearImages.dataset.unavailable = "true";
    try {
      const result = await act("clear_generated_images", {base_hash: snap.hash});
      ctx.clearFinishedCache?.();
      await refresh();
      notify(`已清除 ${result.cleared} 张图片及选图，可以重新生成。旧图片已备份。`);
    } catch (error) {
      clearImages.dataset.unavailable = "false";
      throw error;
    }
  }, "danger drawing-clear-images");
  clearImages.dataset.startJob = "true";
  clearImages.dataset.unavailable = String(!data.images.batches.length && !Object.keys(data.images.selections).length);
  clearImages.disabled = clearImages.dataset.unavailable === "true" || !!window.activeStudioJob;
  const clearHelp = node("small", "清空本作品所有画格的候选图片、选图和已生成排版，保留剧本、分镜、提示词及设置。旧图片自动备份。", "drawing-clear-help");
  controlsPane.append(heading, chooseLoras, loraSummary, batchButton, queueDrawing, clearImages, clearHelp, sharedCount, batchSummary, selectionSummary, advanced);
  const utilities = node("details", undefined, "drawing-utilities");
  utilities.append(node("summary", "试画、统计与画风说明"), ...Array.from(root.children));
  advanced.append(utilities);
  controlsPane.append(node("p", "每格选好图片后，进入「04 成品查看」阅读整页与对白；需要调整版式时展开下方排版设置。", "drawing-guide"),
    button("下一步 · 04 问题格子", () => window.openProblemPanels(), "primary"));
  exchangeControls = window.renderPromptExchange ? await window.renderPromptExchange(controlsPane, {pid,api,act,node,button,notify,
    checkpoint:cp.value,models:data.comfy.checkpoints,modelProfiles:data.diffusion_models,countControl:candidateCount,images:data.images}) : null;
  const exchange = controlsPane.querySelector(".prompt-exchange");
  if (exchange) controlsPane.insertBefore(exchange,advanced);
  root.replaceChildren(controlsPane, pagesPane);
  showPage(currentPage || activePanel?.split("-")[0]);
  showPanel(activePanel);
  window.refreshDrawingControls = () => {showPanel(ctx.activePanel || activePanel);window.syncDrawingQuantity(window.activeStudioJob);exchangeControls?.refresh();};
  if (!Object.keys(p.pages).length)
    pagesPane.append(node("p", "先在逐格分镜阶段确认至少一页。"));
  async function refresh() {
    window.saveWorkspace?.();
    const fresh = await api("/api/project?id=" + pid);
    ctx.snap = fresh;
    await window.renderComfy(root, ctx);
    window.restoreWorkspaceView?.();
  }
};

// Manga lettering is a vector layer over the unmodified artwork in page.svg.
// The same SVG is used for the reader, PNG rasterization, and editable download.
window.mangaLettering = (() => {
  const styles = [
    {id:"classic", name:"漫画横排", note:"横向气泡 · 细墨线"},
    {id:"monologue", name:"静默独白", note:"无尾轮廓 · 疏朗横排"},
    {id:"air", name:"画外心声", note:"无边框 · 白色描边"},
    {id:"impact", name:"情绪强调", note:"感叹句爆发 · 平句克制"},
  ];
  const escape = text => String(text).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&apos;"}[c]));
  // BEGIN EMBEDDED MANGA FONT (generated by scripts/build_manga_font.py)
  const mangaFontLicense = "Copyright 2021-2026 The LXGW Marker Gothic Project Authors (https://github.com/lxgw/LxgwMarkerGothic)\n\nThis Font Software is licensed under the SIL Open Font License, Version 1.1.\nThis license is copied below, and is also available with a FAQ at:\nhttps://openfontlicense.org\n\n\n-----------------------------------------------------------\nSIL OPEN FONT LICENSE Version 1.1 - 26 February 2007\n-----------------------------------------------------------\n\nPREAMBLE\nThe goals of the Open Font License (OFL) are to stimulate worldwide\ndevelopment of collaborative font projects, to support the font creation\nefforts of academic and linguistic communities, and to provide a free and\nopen framework in which fonts may be shared and improved in partnership\nwith others.\n\nThe OFL allows the licensed fonts to be used, studied, modified and\nredistributed freely as long as they are not sold by themselves. The\nfonts, including any derivative works, can be bundled, embedded, \nredistributed and/or sold with any software provided that any reserved\nnames are not used by derivative works. The fonts and derivatives,\nhowever, cannot be released under any other type of license. The\nrequirement for fonts to remain under this license does not apply\nto any document created using the fonts or their derivatives.\n\nDEFINITIONS\n\"Font Software\" refers to the set of files released by the Copyright\nHolder(s) under this license and clearly marked as such. This may\ninclude source files, build scripts and documentation.\n\n\"Reserved Font Name\" refers to any names specified as such after the\ncopyright statement(s).\n\n\"Original Version\" refers to the collection of Font Software components as\ndistributed by the Copyright Holder(s).\n\n\"Modified Version\" refers to any derivative made by adding to, deleting,\nor substituting -- in part or in whole -- any of the components of the\nOriginal Version, by changing formats or by porting the Font Software to a\nnew environment.\n\n\"Author\" refers to any designer, engineer, programmer, technical\nwriter or other person who contributed to the Font Software.\n\nPERMISSION & CONDITIONS\nPermission is hereby granted, free of charge, to any person obtaining\na copy of the Font Software, to use, study, copy, merge, embed, modify,\nredistribute, and sell modified and unmodified copies of the Font\nSoftware, subject to the following conditions:\n\n1) Neither the Font Software nor any of its individual components,\nin Original or Modified Versions, may be sold by itself.\n\n2) Original or Modified Versions of the Font Software may be bundled,\nredistributed and/or sold with any software, provided that each copy\ncontains the above copyright notice and this license. These can be\nincluded either as stand-alone text files, human-readable headers or\nin the appropriate machine-readable metadata fields within text or\nbinary files as long as those fields can be easily viewed by the user.\n\n3) No Modified Version of the Font Software may use the Reserved Font\nName(s) unless explicit written permission is granted by the corresponding\nCopyright Holder. This restriction only applies to the primary font name as\npresented to the users.\n\n4) The name(s) of the Copyright Holder(s) or the Author(s) of the Font\nSoftware shall not be used to promote, endorse or advertise any\nModified Version, except to acknowledge the contribution(s) of the\nCopyright Holder(s) and the Author(s) or with their explicit written\npermission.\n\n5) The Font Software, modified or unmodified, in part or in whole,\nmust be distributed entirely under this license, and must not be\ndistributed under any other license. The requirement for fonts to\nremain under this license does not apply to any document created\nusing the Font Software.\n\nTERMINATION\nThis license becomes null and void if any of the above conditions are\nnot met.\n\nDISCLAIMER\nTHE FONT SOFTWARE IS PROVIDED \"AS IS\", WITHOUT WARRANTY OF ANY KIND,\nEXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO ANY WARRANTIES OF\nMERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT\nOF COPYRIGHT, PATENT, TRADEMARK, OR OTHER RIGHT. IN NO EVENT SHALL THE\nCOPYRIGHT HOLDER BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,\nINCLUDING ANY GENERAL, SPECIAL, INDIRECT, INCIDENTAL, OR CONSEQUENTIAL\nDAMAGES, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING\nFROM, OUT OF THE USE OR INABILITY TO USE THE FONT SOFTWARE OR FROM\nOTHER DEALINGS IN THE FONT SOFTWARE.\n";
  // END EMBEDDED MANGA FONT
  const family = "LXGW Marker Gothic, sans-serif";
  const fontDefinition = () => `<metadata>${escape(mangaFontLicense)}</metadata>`;
  let fontData, fontLoading;
  async function ready(){
    if(!fontLoading)fontLoading=(async()=>{
      const res=await fetch("/fonts/manga-font.json.gz");
      if(!res.ok)throw new Error("漫画字体文件加载失败");
      const stream=(await res.blob()).stream().pipeThrough(new DecompressionStream("gzip"));
      fontData=JSON.parse(await new Response(stream).text());
    })();
    return fontLoading;
  }
  const closing = /^[，。！？、；：）》」』】…,.!?;:)]/u;
  const opening = /[（《「『【(]$/u;
  // Apply the user's punctuation preference to displayed lettering, including
  // saved AI columns, without changing storyboard/image selection versions.
  const punctuation = text => String(text).replace(/[。．.]/gu, "").replace(/，/gu, ",");
  function wrap(text, maxWidth, measure) {
    const lines = [];
    for (const paragraph of String(text).split("\n")) {
      const fullWidth=measure(paragraph), count=Math.max(1,Math.ceil(fullWidth/maxWidth));
      const length=Array.from(paragraph).length;
      const balancedWidth=length ? Math.min(maxWidth,Math.ceil(length/count)*fullWidth/length) : maxWidth;
      let line = "";
      for (const ch of paragraph) {
        if (line && measure(line + ch) > balancedWidth+.01 && !closing.test(ch)) {
          // An opening bracket belongs with the next word, not the previous line.
          const carry = opening.test(line) ? line.slice(-1) : "";
          const rest = carry ? line.slice(0, -1) : line;
          if (rest) lines.push(rest);
          line = carry;
        }
        line += ch;
      }
      lines.push(line);
    }
    return lines;
  }
  function shape(style, w, h, right = false, tail = null) {
    if(style==="air")return "";
    if(style==="caption")return `<rect width="${w}" height="${h}" fill="white" stroke="#171717" stroke-width="1.7"/>`;
    let d=`M ${w*.48} 0 C ${w*.12} 0 1 ${h*.21} 0 ${h*.49} C -1 ${h*.8} ${w*.15} ${h} ${w*.5} ${h} C ${w*.85} ${h} ${w} ${h*.79} ${w} ${h*.49} C ${w} ${h*.2} ${w*.86} 0 ${w*.48} 0 Z`;
    if(style==="soft")d=`M ${w*.49} 0 C ${w*.16} -1 0 ${h*.16} 1 ${h*.47} C -1 ${h*.82} ${w*.2} ${h} ${w*.52} ${h} C ${w*.86} ${h*.99} ${w} ${h*.79} ${w} ${h*.48} C ${w*.99} ${h*.16} ${w*.84} 1 ${w*.49} 0 Z`;
    const direction=tail || (right ? "left" : "right");
    if(["classic","soft","whisper"].includes(style) && direction!=="none"){
      const down=direction.startsWith("down");
      d=`M ${w*.48} 0 C ${w*.16} 0 0 ${h*.18} 0 ${h*.49} C 0 ${h*.81} ${w*.18} ${h} ${w*.5} ${h} C ${w*.76} ${h} ${w*.91} ${h*.86} ${w*.95} ${h*.73} Q ${w+2} ${h*.76} ${w+12} ${h*(down?.84:.75)} Q ${w+2} ${h*.68} ${w*.99} ${h*.64} C ${w*1.02} ${h*.34} ${w*.9} 0 ${w*.48} 0 Z`;
    }
    if(style==="impact"){
      const points=[];
      for(let i=0;i<40;i++){
        const angle=-Math.PI/2+i*Math.PI/20;
        const radius=i%2 ? .82+(i%3)*.018 : .96+(i%4)*.012;
        points.push(`${w/2+Math.cos(angle)*w/2*radius},${h/2+Math.sin(angle)*h/2*radius}`);
      }
      d="M "+points.join(" L ")+" Z";
    }
    const transform = direction.endsWith("left") ? ` transform="translate(${w} 0) scale(-1 1)"` : "";
    return `<path d="${d}"${transform} fill="#fff" stroke="#171717" stroke-width="${style==="impact"?2.4:1.9}" stroke-linejoin="round"${style==="whisper"?' stroke-dasharray="5 4"':''}/>`;
  }
  function columns(text, maxRows=6) {
    const result=[];
    for(const paragraph of text.split("\n")){
      const chars=Array.from(paragraph.trim());
      const count=Math.max(1,Math.ceil(chars.length/maxRows)), target=Math.ceil(chars.length/count);
      let column=[];
      for(const ch of chars){
        if(column.length>=target && !closing.test(ch) && !opening.test(column.at(-1)||"")){result.push(column);column=[];}
        column.push(ch);
      }
      if(column.length)result.push(column);
    }
    return result;
  }
  function textWidth(text,size){
    return Array.from(text).reduce((width,ch)=>{
      const glyph=fontData?.glyphs[ch];
      if(!glyph)throw Error(`漫画字体暂不支持字符「${ch}」，请更换该字符后重试。`);
      return width+glyph[0]*size/fontData.units;
    },0);
  }
  function horizontalText(rows,w,h,size,style){
    const step=size*1.3, height=rows.length*step;
    const startY=(h-height)/2+size*.93;
    const parts=[];
    rows.forEach((row,i)=>{
      let x=(w-textWidth(row,size))/2;
      const y=startY+i*step, scale=size/fontData.units;
      parts.push(`<g data-text-row="${i}" aria-label="${escape(row)}">`);
      for(const ch of row){
        const glyph=fontData.glyphs[ch];
        const transform=`translate(${x} ${y}) scale(${scale} ${-scale})`;
        const outline=`<path d="${glyph[1]}" transform="${transform}" fill="#151515"${style==="impact"?' stroke="#151515" stroke-width="12" stroke-linejoin="round"':''}/>`;
        parts.push(`<g aria-label="${escape(ch)}" data-font="LXGW Marker Gothic" data-font-size="${size}">${style==="air"?`<path d="${glyph[1]}" transform="${transform}" fill="white" stroke="white" stroke-width="${5/scale}" stroke-linejoin="round"/>`:""}${outline}</g>`);
        x+=glyph[0]*scale;
      }
      parts.push('</g>');
    });
    return parts.join("");
  }
  function textSide(text){
    const match=String(text||"").match(/左|右|\bleft\b|\bright\b/iu);
    return match ? /右|right/iu.test(match[0]) ? "right" : "left" : null;
  }
  function preview(style) {
    const text=punctuation(style==="impact"?"今天禁止加班！":style==="monologue"?"原来今天也能这样度过。":style==="air"?"这一刻就慢一点吧。":"老板，今天禁止加班！");
    return `<svg xmlns="http://www.w3.org/2000/svg" width="180" height="180" viewBox="0 0 180 180">${fontDefinition()}<g transform="translate(10 38)">${shape(style,156,104)}${horizontalText(wrap(text,114,t=>textWidth(t,17)),156,104,17,style)}</g></svg>`;
  }
  function layer(page, layout, style, measure, plan = {}) {
    const parts = [], warnings = [], boxes = [];
    for (const panel of page.panels) {
      const r = layout.panels[panel.id];
      const gap = 20, inset = 22;
      let nextY = r.y+inset;
      const lines = (page.lettering || []).filter(t=>t.panel_id===panel.id).sort((a,b)=>a.order-b.order);
      for (const original of lines) {
        const t={...original,text:punctuation(original.text||"")};
        if (!t.text?.trim()) continue;
        const speech = t.kind === "dialogue", narration = t.kind === "narration";
        const choice=style==="auto" ? plan[t.id] : null;
        const forms={speech:"classic",soft:"soft",thought:"monologue",whisper:"whisper",shout:"impact",caption:"caption",bare:"air"};
        const actualStyle=choice ? forms[choice.form]||"classic" : style==="auto" ? "classic" : style==="impact" && !/[！!]/u.test(t.text) ? "classic" : style;
        const size=t.kind==="sfx"?32:choice?.weight==="quiet"?25:actualStyle==="impact"?29:27;
        // Reflow source text horizontally; old AI column breaks are not geometry.
        const padding=actualStyle==="impact"?88:72;
        const maxTextWidth=Math.max(size*2,Math.min(340,r.w*.46-padding));
        const rows=wrap(t.text,maxTextWidth,value=>textWidth(value,size));
        const tw=Math.max(...rows.map(row=>textWidth(row,size))), th=rows.length*size*1.3;
        const bw=Math.ceil(Math.max(128,tw+padding));
        const bh=Math.ceil(Math.max(90,th+(actualStyle==="impact"?84:56)));
        const hint = t.bubble_position || "";
        const speaker=panel.characters?.find(c=>c.character_id===t.speaker_id);
        const speakerPosition=speaker?.position||"";
        const right = choice?.anchor ? choice.anchor.endsWith("right") : (textSide(speakerPosition) || textSide(hint.split(/[，,；;。]/)[0]) || (page.reading_direction==="RTL" ? "right" : "left")) === "right";
        const custom = layout.lettering?.[t.id];
        let x = custom?.x ?? (right ? r.x+r.w-bw-inset : r.x+inset);
        // One vertical cursor across both speakers keeps the script's reading order.
        // Manual positions may add space but cannot move a later line above an earlier one.
        let y = Math.max(nextY, custom?.y ?? nextY);
        if (!custom) {
          // Keep opposite-side bubbles from overlapping in narrow panels.
          for (const box of boxes.filter(b=>b.panel===panel.id)) {
            if (x<box.x+box.w+gap && x+bw+gap>box.x && y<box.y+box.h+gap && y+bh+23+gap>box.y) y=box.y+box.h+gap;
          }
          x = Math.max(r.x+5,Math.min(x,r.x+r.w-bw-5));
        }
        const totalHeight=bh;
        if(x<r.x || x+bw>r.x+r.w || y<r.y || y+totalHeight>r.y+r.h) warnings.push(`${t.id}：对白超出画格，可返回第三步调整位置或缩短文字。`);
        if(x<0 || y<0 || x+bw>layout.width || y+totalHeight>layout.height) throw Error(`${t.id} 的文字超出页面，请返回第三步调整气泡位置。`);
        boxes.push({id:t.id,panel:panel.id,x,y,w:bw,h:totalHeight});
        nextY=y+totalHeight+gap;
        parts.push(`<g id="${escape(t.id)}" transform="translate(${x} ${y})" data-bubble-style="${escape(style)}">`);
        if (speech) parts.push(shape(actualStyle,bw,bh,right,choice?.tail));
        else if(narration) parts.push(`<rect width="${bw}" height="${bh}" fill="#fff" stroke="#171717" stroke-width="1.3"/>`);
        parts.push(`<g aria-label="${escape(t.text)}" data-writing-mode="horizontal-tb">${horizontalText(rows,bw,bh,size,t.kind==="sfx"?"air":actualStyle)}</g>`);
        parts.push("</g>");
      }
    }
    return {svg:parts.join(""),warnings,boxes};
  }
  const dataUrl = svg => "data:image/svg+xml;charset=utf-8,"+encodeURIComponent(svg);
  async function render(svgSource, page, layout, style, plan = {}) {
    await ready();
    const doc = new DOMParser().parseFromString(svgSource,"image/svg+xml");
    if (doc.querySelector("parsererror") || !doc.querySelector("#art")) throw Error("成品图层无法读取，请刷新成品重试。");
    const canvas=document.createElement("canvas"), context=canvas.getContext("2d");
    canvas.width=layout.width;canvas.height=layout.height;
    const lettering=layer(page,layout,style,(value,size)=>{context.font=`500 ${size}px ${family}`;return context.measureText(value).width;},plan);
    const old=doc.querySelector("#lettering");if(old)old.remove();
    const textDoc=new DOMParser().parseFromString(`<svg xmlns="http://www.w3.org/2000/svg"><g id="lettering">${fontDefinition()}${lettering.svg}</g></svg>`,"image/svg+xml");
    doc.documentElement.append(doc.importNode(textDoc.documentElement.firstElementChild,true));
    const svg=new XMLSerializer().serializeToString(doc.documentElement);
    const image=new Image();
    await new Promise((resolve,reject)=>{image.onload=resolve;image.onerror=()=>reject(Error("新气泡预览失败，请刷新成品重试。"));image.src=dataUrl(svg);});
    context.fillStyle="#fff";context.fillRect(0,0,canvas.width,canvas.height);context.drawImage(image,0,0);
    // Data URLs comply with the studio's existing image policy and survive printing.
    const png=canvas.toDataURL("image/png");
    const pngBlob=await new Promise(resolve=>canvas.toBlob(resolve,"image/png"));
    if(!pngBlob)throw Error("成品导出失败，请刷新重试。");
    return {png,downloadPng:URL.createObjectURL(pngBlob),svg:URL.createObjectURL(new Blob([svg],{type:"image/svg+xml;charset=utf-8"})),warnings:lettering.warnings};
  }
  return {styles,wrap,columns,shape,preview,layer,render,dataUrl,ready,punctuation};
})();

// Step 04 finished comic reader.
"use strict";

// Composes existing selected artwork only. No drawing/model action is used here.
window.finishedComicHelpers = {
  missing(page, data) {
    return page.panels.filter(panel => {
      const selected = data.images.selections[panel.id];
      return !selected || selected.panel_hash !== data.panel_hashes[panel.id];
    }).map(panel => panel.id);
  },
  layout(page, fields, fallback) {
    const raw = fields["layout-" + page.id];
    if (!raw) return {value: fallback, note: ""};
    try {
      const value = JSON.parse(raw);
      const expected = page.panels.map(p => p.id).sort().join(",");
      if (Object.keys(value.panels || {}).sort().join(",") !== expected) throw Error("stale");
      if (!Number.isInteger(value.width) || !Number.isInteger(value.height)) throw Error("size");
      return {value, note: ""};
    } catch {
      return {value: fallback, note: "已保存的排版与当前画格不一致，本页使用当前分镜版式。可返回第三步重新调整。"};
    }
  },
  async signature(page, layout, selections) {
    const text = JSON.stringify({page, layout, images: page.panels.map(p => {
      const selected = selections[p.id];
      return [p.id, selected?.panel_hash, selected?.image?.id, selected?.image?.file];
    })});
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
    return Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, "0")).join("");
  },
};

window.renderFinishedComic = async function (root, ctx) {
  const {api, node, button, projectId, current, goDrawing} = ctx;
  const helpers = window.finishedComicHelpers;
  const lettering = window.mangaLettering;
  await lettering.ready();
  if(!current())return;
  let bubbleStyle = ctx.bubbleStyle==="auto" || lettering.styles.some(s=>s.id===ctx.bubbleStyle) ? ctx.bubbleStyle : "auto";
  let aiRunning=false, aiAvailable=true;
  const artSources = new Map();
  const downloadUrls = new Set();
  const releaseDownloads=()=>{for(const url of downloadUrls)URL.revokeObjectURL(url);downloadUrls.clear();};
  window.disposeFinishedComic=()=>{releaseDownloads();artSources.clear();};
  const cache = ctx.cache || {};
  root.className = "finished-comic";
  const toolbar = node("div", undefined, "finished-toolbar");
  const heading = node("h2", "正在读取作品…");
  const summary = node("p", "正在检查选图…", "finished-summary");
  summary.setAttribute("role", "status");
  const actions = node("div", undefined, "compact-actions");
  const pagesRoot = node("div", undefined, "finished-pages");
  const printButton = node("button", "打印 / 保存整本 PDF");
  printButton.type = "button";
  printButton.disabled = true;
  let busy = false, run = 0, totalPages = 0, rendered = 0, loaded = 0;
  printButton.onclick = () => {
    if (current() && !busy && totalPages && rendered === totalPages && loaded === totalPages) window.print();
  };
  const refreshButton = button("刷新成品", () => refresh(true));
  actions.append(button("返回 03 选图与排版", goDrawing), refreshButton, printButton);
  const aiPane=node("section",undefined,"bubble-ai-pane");
  const aiStatus=node("p","尚未运行本地 AI；当前显示基础排版。","bubble-ai-status");
  aiStatus.setAttribute("role","status");
  const pauseLabel=node("label",undefined,"bubble-game-mode"),pauseAi=node("input");
  pauseAi.type="checkbox";pauseAi.checked=ctx.aiPaused!==false;
  pauseAi.onchange=()=>{ctx.saveAiPaused?.(pauseAi.checked);updatePrint();};
  pauseLabel.append(pauseAi,node("span","游戏模式 · 暂停 AI"));
  const autoButton=button("按每句对白自动排版",async()=>{
    if(busy || aiRunning || !current())return;
    bubbleStyle="auto";ctx.saveBubbleStyle?.(bubbleStyle);await refresh(false);
  });
  const aiButton=button("让本地 AI 逐句选择气泡",async()=>{
    if(busy || aiRunning || pauseAi.checked || !current())return;
    aiRunning=true;updatePrint();
    try{
      const result=await api("/api/action",{action:"plan_bubbles",project:projectId,use_model:true});
      if(!current())return;
      ctx.saveAiJob?.(result.job);
      await watchAi(result.job);
    }catch(error){if(current())aiStatus.textContent="AI 气泡选择未完成："+error.message+"。已有方案保留，可再次点击重试。";}
    finally{aiRunning=false;if(current())updatePrint();}
  },"primary");
  const aiActions=node("div",undefined,"compact-actions");aiActions.append(autoButton,aiButton,pauseLabel);
  aiPane.append(node("h3","逐句选择气泡"),aiActions,aiStatus,node("small","AI 根据台词、说话人和分镜选形状、分列及位置，保留原文。运行会加载本地语言模型并使用 GPU；查看成品不会启动模型。"));
  async function watchAi(jobId){
    while(current()){
      const job=await api("/api/job?id="+encodeURIComponent(jobId));
      if(!current())return;
      aiStatus.textContent=job.message||"本地 AI 正在逐句选择…";
      if(job.status==="done"){
        ctx.saveAiJob?.(null);bubbleStyle="auto";ctx.saveBubbleStyle?.(bubbleStyle);
        aiRunning=false;await refresh(false);
        if(job.result?.failures?.length)aiStatus.textContent+=` · ${job.result.failures.length} 页未完成，可重试；已完成页会复用。`;
        return;
      }
      if(!["running","pausing","paused"].includes(job.status)){
        ctx.saveAiJob?.(null);throw Error(job.message||"气泡任务已结束");
      }
      await new Promise(resolve=>setTimeout(resolve,1500));
    }
  }
  const stylePicker = node("details", undefined, "bubble-style-picker");
  stylePicker.open = false;
  stylePicker.append(node("summary", "手动统一样式（覆盖逐句选择）"));
  const styleGrid=node("div",undefined,"bubble-style-grid");
  const styleButtons=[];
  for(const style of lettering.styles){
    const choice=button(style.name,async()=>{
      if(busy || aiRunning || !current() || bubbleStyle===style.id)return;
      bubbleStyle=style.id;ctx.saveBubbleStyle?.(bubbleStyle);
      await refresh(false);
    },"bubble-style-choice");
    choice.setAttribute("aria-label",style.name+"气泡");
    choice.dataset.style=style.id;
    const sample=node("img");sample.src=lettering.dataUrl(lettering.preview(style.id));sample.alt="";
    choice.replaceChildren(sample,node("strong",style.name),node("small",style.note));
    styleButtons.push(choice);styleGrid.append(choice);
  }
  stylePicker.append(styleGrid,node("small","只调整气泡和文字，不重画图片。样式自动保存，预览、PNG、SVG 和打印使用同一版式。"));
  toolbar.append(heading, summary, actions,
    node("small", "按当前选图与排版生成，已包含对白。换图或修改对白后点「刷新成品」；未选齐的页会显示缺少哪些画格。"));
  root.replaceChildren(toolbar, aiPane, stylePicker, pagesRoot);

  function url(bundle, file) {
    return "/api/layout?" + new URLSearchParams({id: projectId, bundle, file});
  }
  function updatePrint() {
    printButton.disabled = busy || !totalPages || rendered !== totalPages || loaded !== totalPages;
    aiButton.disabled=busy || aiRunning || pauseAi.checked || !aiAvailable;
    autoButton.disabled=busy || aiRunning;autoButton.setAttribute("aria-pressed",String(bubbleStyle==="auto"));
    pauseAi.disabled=aiRunning;
    for(const choice of styleButtons){choice.disabled=busy || aiRunning;choice.setAttribute("aria-pressed",String(choice.dataset.style===bubbleStyle));}
  }
  function zoom(src, title) {
    const dialog = node("dialog", undefined, "finished-zoom");
    const image = node("img"); image.src = src; image.alt = title;
    dialog.append(button("关闭大图", () => dialog.close()), image);
    dialog.addEventListener("close", () => dialog.remove());
    document.body.append(dialog); dialog.showModal();
  }

  async function refresh(force) {
    if (busy || !current()) return;
    busy = true;
    const revision = ++run;
    const valid = () => current() && run === revision;
    rendered = 0; loaded = 0; totalPages = 0;
    refreshButton.disabled = true; updatePrint();
    pagesRoot.replaceChildren();
    releaseDownloads();
    summary.textContent = "正在检查选图…";
    try {
      const [snapshot, data] = await Promise.all([
        api("/api/project?id=" + encodeURIComponent(projectId)),
        api("/api/drawing?id=" + encodeURIComponent(projectId)),
      ]);
      if (!valid()) return;
      const project = snapshot.project;
      let plans={pages:{},stale_pages:[]};
      try{plans=await api("/api/bubble-plan?id="+encodeURIComponent(projectId));aiAvailable=true;}
      catch{aiAvailable=false;}
      if(!valid())return;
      if(!aiRunning)aiStatus.textContent=!aiAvailable ? "AI 气泡接口尚未就绪，请重启工作室。基础排版可以继续查看。" : Object.keys(plans.pages).length ? `本地 AI 已选择 ${Object.keys(plans.pages).length} 页的气泡。${plans.stale_pages.length?"部分选图或分镜有变化，需要重新选择。":""}` : "尚未运行本地 AI；当前显示基础排版。关闭游戏模式后，可让 AI 逐句选择。";
      const pages = Object.values(project.pages || {}).sort((a, b) => a.number - b.number);
      totalPages = pages.length;
      const ids = new Set(pages.map(page => page.id));
      for (const key of Object.keys(cache)) if (!ids.has(key)) delete cache[key];
      heading.textContent = (project.brief?.title || "我的漫画") + " · 成品查看";
      if (!pages.length) {
        summary.textContent = "还没有确认的分镜。请先完成第二步，再到第三步选图。";
        return;
      }
      const totalPanels = pages.reduce((n, page) => n + page.panels.length, 0);
      const missingCount = pages.reduce((n, page) => n + helpers.missing(page, data).length, 0);
      let failures = 0;
      const describe = () => {
        summary.textContent = `共 ${totalPages} 页 · 已选 ${totalPanels - missingCount}/${totalPanels} 格 · 已排版 ${rendered}/${totalPages} 页` +
          (busy ? " · 正在生成页面…" : failures ? ` · ${failures} 页需要重试` : missingCount ? " · 请补齐下方缺少的选图" : " · 可放大检查对白和裁切");
      };
      describe();
      for (const page of pages) {
        if (!valid()) return;
        const card = node("article", undefined, "finished-page");
        card.dataset.page = page.id;
        const pageHeader = node("div", undefined, "finished-page-header");
        pageHeader.append(node("h3", `第 ${page.number} 页`));
        pageHeader.append(node("small",bubbleStyle==="auto" ? plans.pages[page.id] ? "本地 AI 逐句选择" : "基础排版 · 尚未运行 AI" : "手动统一样式","bubble-plan-origin"));
        const content = node("div", undefined, "finished-page-content");
        card.append(pageHeader, content); pagesRoot.append(card);
        const missing = helpers.missing(page, data);
        if (missing.length) {
          delete cache[page.id]; card.classList.add("finished-incomplete");
          content.append(node("p", "尚未选好当前分镜版本的图片：" + missing.join("、")),
            button("去第三步选图", goDrawing));
          continue;
        }
        const chosen = helpers.layout(page, ctx.layoutFields, data.layouts[page.id]);
        if (chosen.note) content.append(node("p", chosen.note, "finished-page-warning"));
        const progress = node("p", "正在用已选图片排版…"); content.append(progress);
        try {
          const signature = await helpers.signature(page, chosen.value, data.images.selections);
          if (!valid()) return;
          let result = cache[page.id]?.signature === signature && !force ? cache[page.id].result : null;
          if (!result) result = await api("/api/action", {action: "compose", project: projectId, target: page.id, layout: chosen.value});
          if (!valid()) return;
          cache[page.id] = {signature, result};
          ctx.saveCache(cache);
          const sourceUrl=url(result.bundle,"page.svg");
          let source=artSources.get(page.id)?.url===sourceUrl ? artSources.get(page.id).source : null;
          if(!source){
            const response=await fetch(sourceUrl);
            if(!response.ok)throw Error("无法读取原始画面图层，请刷新重试。");
            source=await response.text();
            // Keep only one source per page when selections or lettering change.
            artSources.set(page.id,{url:sourceUrl,source});
          }
          const styled=await lettering.render(source,page,chosen.value,bubbleStyle,plans.pages[page.id]?.bubbles||{});
          if(!valid()){
            if(styled.downloadPng)URL.revokeObjectURL(styled.downloadPng);
            if(styled.svg.startsWith("blob:"))URL.revokeObjectURL(styled.svg);
            return;
          }
          if(styled.downloadPng)downloadUrls.add(styled.downloadPng);
          if(styled.svg.startsWith("blob:"))downloadUrls.add(styled.svg);
          progress.remove();
          const image = node("img", undefined, "finished-page-image");
          image.alt = `第 ${page.number} 页漫画成品（含对白）`;
          image.loading = "eager";
          image.onload = () => {if (valid()) {loaded += 1; updatePrint();}};
          image.onerror = () => {
            if (!valid()) return;
            delete cache[page.id]; ctx.saveCache(cache);
            content.append(node("p", "页面图片未能载入，请点上方「刷新成品」重试。", "finished-page-warning"));
          };
          image.src = styled.png;
          content.append(image);
          const links = node("div", undefined, "compact-actions");
          links.append(button("放大查看", () => zoom(image.src, image.alt)));
          for (const [file, label] of [["page.png", "下载本页 PNG"], ["page.svg", "下载可编辑 SVG"]]) {
            const a = node("a", label);
            a.href = file==="page.png" ? styled.downloadPng || styled.png : styled.svg;
            a.download = `${project.brief?.title || "漫画"}-第${page.number}页.${file.split(".").pop()}`;
            links.append(a);
          }
          pageHeader.append(links);
          // Old lettering geometry is replaced; only warnings about the new layer apply.
          for (const warning of (result.warnings || []).filter(w=>!w.includes("气泡"))) content.append(node("p", warning, "finished-page-warning"));
          for (const warning of styled.warnings) content.append(node("p", warning, "finished-page-warning"));
          rendered += 1; describe();
        } catch (error) {
          if (!valid()) return;
          delete cache[page.id]; failures += 1;
          progress.textContent = "本页暂未排版成功：" + error.message;
          content.append(button("返回第三步检查选图与排版", goDrawing));
        }
      }
      if (valid()) {ctx.saveCache(cache); busy = false; describe();}
    } catch (error) {
      if (valid()) summary.textContent = "读取成品失败：" + error.message + "。可点「刷新成品」重试。";
    } finally {
      busy = false;
      if (valid()) {refreshButton.disabled = false; updatePrint();}
    }
  }
  await refresh(false);
  if(ctx.aiJob && current()){
    aiRunning=true;updatePrint();
    watchAi(ctx.aiJob).catch(error=>{if(current())aiStatus.textContent=error.message;}).finally(()=>{aiRunning=false;if(current())updatePrint();});
  }
};
