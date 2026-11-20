/* File exchange is local; no external service receives data automatically. */
function promptChecks(root, result, node) {
  const warnings=result.warnings||[];
  if(!warnings.length)return;
  const box=node("details");box.open=warnings.length<=6;
  box.append(node("summary",`发现 ${warnings.length} 项需要核对的提示词问题`));
  for(const warning of warnings)box.append(node("p",warning.message,"warning"));
  root.append(box);
}
function panelCast(card, result, row, node) {
  const info=result.panel_details?.[row.panel_id];if(!info)return;
  card.append(node("p",`分镜出场：${info.visible_characters.map(c=>c.name).join("、")||"无人"}；镜头：${info.shot} / ${info.angle}`));
  if(info.loras)card.append(node("p",`当前模型的 LoRA：${info.loras.map(l=>`${l.label}（${l.weight}）`).join("、")||"无"}`));
  if(info.reference)card.append(node("p",`角色参考图：${info.reference.character}（保留原图比例；作用于整幅画面，不限定人物区域）`));
}
window.confirmLiteralDrawing = async function(ctx, action, request) {
  const {act,node,button,notify}=ctx;
  if(action==="queue_drawing"&&window.studioQueueAvailable===false)throw Error("夜间列表需要新版后台。请等当前生成结束后更新工作室，已填内容保留。");
  await window.flushLoraSettings?.();
  const preview=await act("preview_drawing",request);
  if(!preview.panels.length){notify("这些画格已经选好图片，无需再次出图。");return;}
  const dialog=node("dialog",undefined,"exchange-preview");
  dialog.setAttribute("aria-label","确认本次出图提示词");
  dialog.append(node("h2","确认本次出图提示词"),node("p",`${preview.panels.length} 格 × ${request.candidate_count} 张。下方文字将原样发送，加载所选 LoRA，不自动改写、审核或补画。触发词请直接写入正向提示词。`));
  dialog.append(node("p","同格候选只改变随机种子，不会自动改变镜头。请先核对人数、动作和每个人的外观归属。"));
  promptChecks(dialog,preview,node);
  const list=node("div",undefined,"exchange-preview-list"), edits=[];
  for(const row of preview.panels){
    const card=node("article");card.append(node("h3",row.panel_id),node("p",row.summary));
    panelCast(card,preview,row,node);
    const fields={};
    for(const [key,label] of [["positive","正向提示词"],["negative","负向提示词"]]){
      const area=node("textarea"),field=node("label",label);area.rows=4;area.value=row[key];
      area.placeholder=key==="positive"?"填写你确认的画面描述；也可以先导入提示词文件":"可留空";
      field.append(area);card.append(field);fields[key]=area;
    }
    edits.push({row,fields});list.append(card);
  }
  const queued=action==="queue_drawing";
  const confirm=button(queued?"确认并加入任务列表":"确认原样出图",async()=>{
    const rows=edits.map(({row,fields})=>({...row,positive:fields.positive.value,negative:fields.negative.value}));
    if(rows.some(r=>!r.positive.trim()))throw Error("请填写每格正向提示词，不会自动补写。");
    confirm.disabled=true;
    try{await window.flushLoraSettings?.();const added=await act(action,{...request,confirm_prompts:true,prompt_rows:rows});dialog.close();notify(queued?"已加入任务列表，可继续添加其他任务，最后打开列表开始执行。":"已按确认的提示词开始出图，完成后由你选图。");if(queued){await refreshStatus();await window.afterQueuedTask?.(added);}}
    finally{confirm.disabled=false;}
  },"primary");
  const controls=node("div",undefined,"dialog-actions");controls.append(button("返回修改",()=>dialog.close()),confirm);
  dialog.append(list,controls);dialog.onclose=()=>dialog.remove();document.body.append(dialog);dialog.showModal();
};
window.exportStoryboardPrompts = async function(pid, api, notify) {
  const data=await api("/api/prompt-exchange?id="+encodeURIComponent(pid));
  const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:"application/json;charset=utf-8"}));
  const link=document.createElement("a");link.href=url;link.download=`${pid}-storyboard-prompts.json`;
  document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),10000);
  notify("已导出。填写 positive / negative，保留画格编号和版本标记；第三步「分镜提示词」可导入。");
};
window.renderPromptExchange = async function(root, ctx) {
  const {pid, api, act, node, button, notify} = ctx;
  const box = node("section", undefined, "prompt-exchange");
  box.append(node("h3", "分镜提示词"), node("p", "用本地 Qwen 自动填写，或导出给外部工具；核对确认后再出图。"));
  const actions = node("div", undefined, "exchange-actions");
  const status = node("p", "正在读取导入记录…", "exchange-status");
  const checks=node("div");
  let imported = null, stale = false;
  const upload = node("input");upload.type="file";upload.accept=".json,application/json";upload.hidden=true;
  upload.setAttribute("aria-label", "选择提示词 JSON 文件");
  const exportButton = button("① 导出分镜文件",()=>window.exportStoryboardPrompts(pid,api,notify));
  const localStatus=node("p", "", "exchange-status");
  const localButton=button("Flash Next 自动填写提示词",async()=>{
    localButton.disabled=true;
    try {
      await act("fill_local_prompts",{project:pid});
      localStatus.textContent="正在用本地 Qwen 逐格填写，进度见顶部任务栏。完成后点击「核对本地草稿」。";
      notify("已开始使用 GPU 填写提示词；已确认的提示词保留。");
    } finally {localButton.disabled=Boolean(window.activeStudioJob);}
  },"primary");
  localButton.dataset.startJob="true";
  localButton.disabled=Boolean(window.activeStudioJob);
  const reviewLocal=button("核对本地草稿",async()=>{
    const result=await api("/api/local-prompts?id="+encodeURIComponent(pid));
    if(!result.draft)throw Error("尚无本地草稿，请先点击自动填写。");
    if(result.stale)throw Error("分镜或角色已变化，请重新生成提示词。");
    const documentData=structuredClone(result.draft.document);
    documentData.panels=documentData.panels.filter(row=>row.positive.trim());
    if(!documentData.panels.length)throw Error("尚未完成第一格，请查看顶部任务进度。");
    await showPreview(documentData);
  });
  reviewLocal.hidden=true;
  actions.append(localButton,reviewLocal,exportButton,button("② 导入提示词文件",()=>{upload.value="";upload.click();}));
  const settings = node("details", undefined, "exchange-settings");
  settings.append(node("summary", "导入出图设置"));
  const target = node("select"), count=ctx.countControl, steps=node("input"), seed=node("input");
  const model=node("select");
  for(const name of ctx.models || []) {const option=node("option",ctx.modelProfiles?.[name]?.label||name);option.value=name;model.append(option);}
  model.value=ctx.checkpoint || "";
  steps.type="number";steps.min="1";steps.max="100";steps.placeholder="模型默认";
  seed.type="number";seed.min="0";seed.max="9007199254740991";seed.placeholder="随机";
  for(const [text, control] of [["绘制范围",target],["出图模型",model],["步数",steps],["种子",seed]]) {
    const label=node("label",text);label.append(control);settings.append(label);
  }
  const reference=node("select"), referenceLabel=node("label","固定参考图（本次范围共用）");
  const referencePreview=node("img");referencePreview.alt="本次固定参考图";referencePreview.hidden=true;referencePreview.style.maxWidth="240px";
  let referenceImages=new Map();
  reference.onchange=()=>{const file=referenceImages.get(reference.value);referencePreview.hidden=!file;
    if(file){const [bundle,name]=file.split("/");referencePreview.src=`/api/image?id=${encodeURIComponent(pid)}&bundle=${encodeURIComponent(bundle)}&file=${encodeURIComponent(name)}`;}};
  reference.setAttribute("aria-label","固定参考图（本次范围共用）");
  referenceLabel.append(reference);settings.append(referenceLabel,referencePreview,
    node("small","试用功能：Anima Delta v6 可复用一张已生成图片，辅助保持人物、服装和道具一致。逐格生成，不同时加载整页。当前参考容易带入原图姿势与补边，建议先试一格再画整页。"));
  const editBox=node("section"), editExtras=[];
  const editing=()=>model.value==="qwen_image_edit_2511_fp8mixed.safetensors";
  editBox.append(node("p","多图参考试验：上方为 image 1，下方依次为 image 2、image 3。请在提示词里分别说明保留谁的外观、哪处场景或哪个道具，以及本格的新动作。当前画格比例须与第一张参考图相同；请在绘图设置里关闭 LoRA。参考顺序固定，每张成图仍须核对。"));
  for(let n=2;n<=3;n++){
    const select=node("select"),label=node("label",`参考图 ${n}（可不选）`),preview=node("img");
    select.setAttribute("aria-label",`参考图 ${n}`);preview.alt=`参考图 ${n}`;preview.hidden=true;preview.style.maxWidth="180px";
    select.onchange=()=>{const file=referenceImages.get(select.value);preview.hidden=!file;if(file){const [bundle,name]=file.split("/");preview.src=`/api/image?id=${encodeURIComponent(pid)}&bundle=${encodeURIComponent(bundle)}&file=${encodeURIComponent(name)}`;}};
    label.append(select);editBox.append(label,preview);editExtras.push(select);
  }
  settings.append(editBox);
  const estimate=node("p");
  const chosenTargets=()=> (imported?.panels||[]).filter(r=>!target.value ||
    (target.value.startsWith("page:") ? r.page_id===target.value.slice(5) : r.panel_id===target.value)).map(r=>r.panel_id);
  function updateEstimate() {const n=chosenTargets().length;estimate.textContent=imported?`本次 ${n} 格 × ${count.value} 张 = ${n*Number(count.value)} 张。`:"";}
  target.onchange=updateEstimate;count.addEventListener("change",updateEstimate);
  async function submitImported(queued=false) {
    if(queued&&window.studioQueueAvailable===false)throw Error("夜间列表需要新版后台。请等当前生成结束后更新工作室，已导入的提示词保留。");
    if(!steps.checkValidity() || !seed.checkValidity()) throw Error("请检查步数和种子范围");
    await window.flushLoraSettings?.();
    const editRefs=[reference,...editExtras].map(s=>s.value).filter(Boolean);
    if(editing() && (!reference.value || new Set(editRefs).size!==editRefs.length))throw Error("请选择第一张参考图；多张参考不能重复。");
    if(editing() && !editExtras[0].value && editExtras[1].value)throw Error("请按顺序填写参考图 2、3。");
    const added=await act(queued?"queue_imported":"draw_imported", {project:pid, bundle:imported.id,
      targets:chosenTargets(), candidate_count:Number(count.value), checkpoint:model.value,
      references_by_panel:reference.disabled || !reference.value ? {} : Object.fromEntries(chosenTargets().map(id=>[id,editing()?editRefs:reference.value])),
      steps:steps.value===""?null:Number(steps.value),seed:seed.value===""?null:Number(seed.value)});
    notify(queued?"已加入任务列表，导入提示词、LoRA 和出图设置已保存。":"已开始按导入原文逐格绘制，可在顶部暂停或取消。");
    await refreshStatus();
    if(queued)await window.afterQueuedTask?.(added);
  }
  const draw = button("③ 按导入提示词绘制",()=>submitImported(), "primary");
  const enqueue = button("把导入提示词加入任务列表",()=>submitImported(true));
  draw.dataset.startJob="true";draw.dataset.unavailable="true";draw.disabled=true;
  model.onchange=()=>{reference.disabled=!editing() && model.value!=="animaDeltaMix38BQwen0_deltaMixV60.safetensors";editBox.hidden=!editing();draw.dataset.unavailable=String(!imported || stale || !model.value);draw.disabled=draw.dataset.unavailable==="true" || Boolean(window.activeStudioJob);enqueue.disabled=draw.dataset.unavailable==="true";};
  async function load() {
    const [result,local]=await Promise.all([
      api("/api/prompt-import?id="+encodeURIComponent(pid)), api("/api/local-prompts?id="+encodeURIComponent(pid))]);
    imported=result.bundle;stale=result.stale;
    checks.replaceChildren();if(!stale)promptChecks(checks,result,node);
    status.textContent=imported?`已导入 ${imported.panels.length} 格${stale?" · 分镜已变化，请重新导入":" · 提示词原文已保存"}`:"尚未导入提示词文件。";
    target.replaceChildren(node("option","全部已导入画格"));target.firstChild.value="";
    for(const id of new Set((imported?.panels||[]).map(r=>r.page_id))) {const option=node("option",id+" 整页");option.value="page:"+id;target.append(option);}
    for(const row of imported?.panels||[]) {const option=node("option",row.panel_id);option.value=row.panel_id;target.append(option);}
    const drawing=ctx.images ? {images:ctx.images} : await api("/api/images?id="+encodeURIComponent(pid));
    const previousReference=reference.value;
    reference.replaceChildren(node("option","不使用固定参考图"));reference.firstChild.value="";
    referenceImages=new Map();const referenceCounts={};
    for(const batch of [...drawing.images.batches].reverse())for(const image of batch.images||[]) {
      const selected=drawing.images.selections[batch.panel_id]?.image?.id===image.id;
      referenceCounts[batch.panel_id]=(referenceCounts[batch.panel_id]||0)+1;
      const option=node("option",`${batch.panel_id} · ${selected?"已选 · ":""}图片 ${referenceCounts[batch.panel_id]}`);
      referenceImages.set(image.id,image.file);
      option.value=image.id;reference.append(option);
    }
    if([...reference.options].some(o=>o.value===previousReference))reference.value=previousReference;
    for(const select of editExtras){const previous=select.value;select.replaceChildren(...[...reference.options].map(o=>o.cloneNode(true)));if(referenceImages.has(previous))select.value=previous;select.onchange();}
    reference.onchange();
    settings.hidden=!imported;draw.hidden=!imported;enqueue.hidden=!imported;updateEstimate();model.onchange();
    reviewLocal.hidden=!local.draft;
    reviewLocal.disabled=Boolean(local.stale) || !local.draft?.completed;
    localStatus.textContent=local.draft
      ? `本地草稿：已填写 ${local.draft.completed} / ${local.draft.document.panels.length} 格${local.stale?"；分镜已变化，请重新生成。":local.draft.status==="complete"?"；待核对确认。":"；已完成的内容已保留，任务状态见顶部。"}`
      : "点击后使用本地 Qwen 和 GPU 生成提示词，逐格保存，可暂停或取消；结束后自动卸载模型。";
  }
  upload.onchange=async()=>{
    try {
      const file=upload.files?.[0];if(!file)return;
      if(file.size>2_000_000)throw Error("文件超过 2 MB，请分批导入");
      let text=(await file.text()).replace(/^\uFEFF/,"").trim();
      if(text.startsWith("```"))text=text.replace(/^```(?:json)?\s*/i,"").replace(/\s*```$/,"");
      let documentData;try {documentData=JSON.parse(text);}catch {throw Error("文件不是有效 JSON，请导出完整 JSON 文件后再导入。");}
      await showPreview(documentData);
    } catch(error) {notify(error.message,true);}
  };
  async function showPreview(documentData) {
      const result=await act("preview_prompt_import",{project:pid,document:documentData});
      const dialog=node("dialog",undefined,"exchange-preview");dialog.setAttribute("aria-label","导入提示词预览");
      dialog.append(node("h2","核对逐格提示词"),node("p",`识别 ${result.panels.length} 格 / 共 ${result.total} 格。`));
      for(const error of result.errors)dialog.append(node("p",error,"warning"));
      promptChecks(dialog,result,node);
      if(result.missing.length)dialog.append(node("p",`未提供 ${result.missing.length} 格：${result.missing.join("、")}。确认后只导入已提供的画格。`,"warning"));
      const list=node("div",undefined,"exchange-preview-list"), edits=[];
      for(const row of result.panels) {
        const article=node("article");article.append(node("h3",row.page_id+" / "+row.panel_id),node("p",row.summary || ""));
        panelCast(article,result,row,node);
        const fields={};
        for(const [key,label] of [["positive","正向提示词"],["negative","负向提示词"]]) {
          const field=node("label",label),area=node("textarea");area.value=row[key];area.rows=3;field.append(area);article.append(field);fields[key]=area;
        }
        edits.push({row,fields});
        list.append(article);
      }
      const controls=node("div",undefined,"dialog-actions");
      const confirm=button(`确认使用 ${result.panels.length} 格提示词`,async()=>{
        confirm.disabled=true;
        try {
          for(const {row,fields} of edits) {
            const target=documentData.panels.find(p=>p.panel_id===row.panel_id);
            target.positive=fields.positive.value;target.negative=fields.negative.value;
          }
          const checked=await act("preview_prompt_import",{project:pid,document:documentData});
          if(!checked.valid)throw Error(checked.errors.join("；"));
          await act("save_prompt_import",{project:pid,document:documentData,preview_hash:checked.preview_hash,confirm:true});
          await load();dialog.close();notify("已保存提示词。检查出图设置后点击绘制。");
        }
        finally {confirm.disabled=!result.valid;}
      },"primary");confirm.disabled=!result.valid;
      controls.append(button("返回，不导入",()=>dialog.close()),confirm);
      dialog.append(list,controls);dialog.onclose=()=>dialog.remove();document.body.append(dialog);dialog.showModal();
  }
  box.append(actions,localStatus,upload,status,checks,settings,estimate,draw,enqueue,node("small","数量使用上方「每格生成数量」；同格只换随机种子，不自动换镜头。LoRA 与第一步共用，以最后一次选择为准；角色绑定决定加载哪些 LoRA，并不保证多人特征隔离。提示词保持原文，所需触发词请写入提示词。不自动审核补画，已有图片和选图保留。"));
  root.append(box);
  try {await load();}catch(error) {status.textContent=error.message;}
  return {refresh:updateEstimate, setCheckpoint(value) {model.value=value;model.onchange();}};
};
