"use strict";

async function openTaskFusion(queueDialog) {
  const [projects, queue] = await Promise.all([api("/api/projects"), api("/api/queue")]);
  const storageKey = "studio-task-fusion-draft";
  let draft = null;
  try { draft = JSON.parse(localStorage.getItem(storageKey)); } catch {}
  if (!Array.isArray(draft?.sources)) draft = {sources:[], cursor:0, review:false, key:crypto.randomUUID().replaceAll("-", "")};
  const dlg = node("dialog", undefined, "fusion-dialog");
  dlg.setAttribute("aria-label", "融合任务");
  const body = node("section"), status = node("p");
  status.setAttribute("role", "status");
  const head = node("div", undefined, "queue-heading");
  const close = button("稍后继续", () => dlg.close());
  head.append(node("h2", "融合任务"), close);
  dlg.append(head, body, status);
  let busy = false, saved = false, timer;
  function persist() {
    clearTimeout(timer);
    if (!saved && !cacheLocal(storageKey, JSON.stringify(draft)))
      status.textContent = "本机空间不足，审核进度只在本窗口保留。";
  }
  async function loading(fn) {
    if (busy) return;
    busy = true; close.disabled = true; body.inert = true; status.textContent = "";
    try {await fn();} catch (error) {status.textContent = error.message;}
    finally {busy = false; close.disabled = false; body.inert = false;}
  }
  const choices = [
    ...projects.filter(p => p.pages > 0).map(p => ({kind:"project", id:p.id, title:p.title})),
    ...queue.items.map(i => ({kind:"queue", id:i.id, title:i.label}))
  ];
  const titleCounts = new Map();
  for (const c of choices) titleCounts.set(c.title,(titleCounts.get(c.title)||0)+1);
  const nameOf = c => titleCounts.get(c.title)>1 ? c.title+" · "+c.id.slice(-6) : c.title;
  function order() {
    draft.review = false;
    body.replaceChildren();
    const label = node("label", "选择作品或队列任务"), select = node("select");
    for (const [kind, name] of [["project","已有作品"],["queue","队列任务"]]) {
      const group = node("optgroup"); group.label = name;
      choices.forEach((c,i) => {
        if (c.kind !== kind || draft.sources.some(s => s.kind === kind && s.id === c.id)) return;
        const opt = node("option", nameOf(c)); opt.value = i; group.append(opt);
      });
      if (group.children.length) select.append(group);
    }
    label.append(select);
    const add = button("添加到融合顺序", () => loading(async () => {
      const c = choices[Number(select.value)];
      const src = await act("fusion_source", {kind:c.kind, source_id:c.id});
      if (!src.panels?.length) throw Error("这项任务还没有画格");
      if (src.panels.length + draft.sources.reduce((n,s)=>n+s.panels.length,0) > 1000) throw Error("一次融合最多 1000 格");
      draft.sources.push({...c,...src}); draft.cursor = 0; persist(); order();
    }));
    select.disabled = add.disabled = !select.options.length;
    const list = node("ol", undefined, "fusion-order");
    draft.sources.forEach((src,i) => {
      const row = node("li"), actions = node("div", undefined, "fusion-actions");
      for (const [text,delta] of [["上移",-1],["下移",1]]) {
        const move = button(text, () => {
          const target = i + delta;
          [draft.sources[i],draft.sources[target]] = [draft.sources[target],draft.sources[i]];
          draft.cursor = 0; persist(); order();
          body.querySelectorAll(".fusion-order li")[target].focus();
        });
        move.disabled = i+delta < 0 || i+delta >= draft.sources.length;
        move.setAttribute("aria-label",text+" "+nameOf(src)); actions.append(move);
      }
      actions.append(button("移除", () => {draft.sources.splice(i,1);draft.cursor=0;persist();order();}));
      row.tabIndex = -1; row.append(node("strong",nameOf(src)),node("small",src.panels.length+" 格"),actions); list.append(row);
    });
    const start = button("逐格审核", () => {draft.review=true;persist();review(true);}, "primary");
    start.disabled = draft.sources.length < 2;
    body.append(label,add,node("h3","融合顺序"),list);
    if (draft.sources.length < 2) body.append(node("p","选满两个来源即可审核，排在上方的作品先画。"));
    body.append(start);
  }
  function review(focus) {
    const rows = draft.sources.flatMap(src=>src.panels.map(panel=>({src,panel})));
    draft.cursor = Math.max(0,Math.min(draft.cursor,rows.length-1));
    const {src,panel} = rows[draft.cursor];
    body.replaceChildren();
    const title = node("h3",nameOf(src)+" · "+panel.panel_id); title.tabIndex=-1;
    const counter = node("p"); counter.setAttribute("role","status");
    function nextPending() {
      const pending = r=>!r.panel.approved && !r.panel.skipped;
      const next = rows.findIndex((r,i)=>i>draft.cursor && pending(r));
      const first = next < 0 ? rows.findIndex(pending) : next;
      if(first>=0) draft.cursor=first;
      persist();review(true);
    }
    const ok = button("通过并继续", () => {
      if (!positive.value.trim()) {positive.setCustomValidity("请先填写正面提示词");positive.reportValidity();return;}
      panel.approved=true; panel.skipped=false; nextPending();
    }, "primary");
    const skip = button(panel.skipped?"恢复此格":"跳过此格", () => {
      panel.skipped=!panel.skipped;panel.approved=false;
      if(panel.skipped) nextPending(); else {persist();review(true);}
    });
    const save = button("保存融合任务并设置出图", () => loading(async () => {
      persist();
      const result = await act("save_prompt_fusion",{sources:draft.sources,import_key:draft.key});
      saved = true;
      try {localStorage.removeItem(storageKey);} catch {}
      dlg.close(); if(queueDialog?.open) queueDialog.close();
      await window.showStudioHome();
      try {
        const data=await api("/api/prompt-import?id="+encodeURIComponent(result.project));
        await openImportedSetup(result.project,result.bundle,data.bundle.panels);
      } catch(error) {notify("融合任务已保存，可从首页继续。"+error.message,true);}
    }), "primary");
    function update() {
      const approved=rows.filter(r=>r.panel.approved).length, skipped=rows.filter(r=>r.panel.skipped).length;
      const done=approved+skipped===rows.length;
      counter.textContent="第 "+(draft.cursor+1)+" / "+rows.length+" 格 · 本格"+(panel.skipped?"已跳过":panel.approved?"已通过":"待审核")
        +" · 共通过 "+approved+" 格，跳过 "+skipped+" 格";
      save.hidden=!done || !approved;ok.hidden=panel.approved || panel.skipped;
      skip.textContent=panel.skipped?"恢复此格":"跳过此格";
    }
    function field(key,text) {
      const label=node("label",text), input=node("textarea");
      input.value=panel[key];input.rows=key==="positive"?5:2;input.maxLength=20000;
      input.oninput=()=>{panel[key]=input.value;panel.approved=false;panel.skipped=false;input.setCustomValidity("");update();clearTimeout(timer);timer=setTimeout(persist,200);};
      label.append(input);body.append(label);return input;
    }
    body.append(button("返回调整顺序",()=>{order();persist();}),title,counter);
    if(panel.summary) body.append(node("p",panel.summary,"fusion-summary"));
    const positive=field("positive","正面提示词");field("negative","负面提示词");
    const nav=node("div",undefined,"fusion-actions");
    for (const [text,delta] of [["上一格",-1],["下一格",1]]) {
      const b=button(text,()=>{draft.cursor+=delta;persist();review(true);});
      b.disabled=draft.cursor+delta<0 || draft.cursor+delta>=rows.length;nav.append(b);
    }
    nav.append(skip,ok,save);body.append(nav);update();if(focus) title.focus();
  }
  dlg.oncancel=e=>{if(busy)e.preventDefault();};
  dlg.onclose=()=>{persist();dlg.remove();};
  document.body.append(dlg);if(draft.review && draft.sources.length>=2)review(false);else order();
  dlg.showModal();
}
