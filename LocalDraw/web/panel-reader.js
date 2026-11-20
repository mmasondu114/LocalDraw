"use strict";

window.renderPanelReader = async function(root, ctx) {
  const {pid, snap, act, api, node, button, notify} = ctx;
  const project = snap.project;
  const panels = Object.values(project.pages).sort((a,b) => a.id.localeCompare(b.id)).flatMap(page => page.panels);
  const view = node("section", undefined, "panel-reader");
  view.tabIndex = -1;
  view.setAttribute("aria-label", "逐格绘图与选图");
  root.replaceChildren(view);
  if (!panels.length) {
    view.append(node("p", "先在分镜步骤确认一页分镜，再来填写提示词和画图。"));
    return;
  }
  const request = {project:pid, base_hash:snap.hash, force:true};
  const drawingUrl = "/api/images?id=" + encodeURIComponent(pid);
  const [drawing, problems, preview] = await Promise.all([
    api(drawingUrl), api("/api/problems?id=" + encodeURIComponent(pid)),
    act("preview_drawing", {...request, targets:panels.map(p => p.id)})
  ]);
  if (!view.isConnected) return;
  let images = drawing.images;
  const rows = new Map(preview.panels.map(row => [row.panel_id, row]));
  const marked = new Map(problems.items.map(row => [row.panel_id, row]));
  const drafts = workspaceState.panelPromptDrafts ||= {};
  let index = Math.max(0, panels.findIndex(p => p.id === workspaceState.reviewPanel));
  let group = 0, choosing = Promise.resolve();
  const flush = () => choosing.catch(() => {});
  const header = node("div", undefined, "reader-heading");
  const title = node("h2"), summary = button("问题汇总", () => window.openProblemPanels());
  title.setAttribute("aria-live", "polite");
  const advanced = button("高级绘图工具", () => ctx.showAdvanced());
  const {upload} = promptFileImport(pid, imported => {
    if (!view.isConnected) return;
    for (const row of imported) {rows.set(row.panel_id, row); delete drafts[row.panel_id];}
    show();
  });
  header.append(title, button("导出分镜", () => window.exportStoryboardPrompts(pid, api, notify)),
    button("导入提示词", () => {upload.value = ""; upload.click();}), upload, summary, advanced);
  const previous = button("← 上一页", () => move(-1)), next = button("下一页 →", () => move(1));
  const figure = node("div", undefined, "reader-figure");
  const strip = node("div", undefined, "reader-strip");
  const canvas = node("div", undefined, "reader-canvas");
  canvas.append(previous, figure, next);
  const info = node("p"), story = node("details");
  const form = node("div", undefined, "reader-prompts");
  function field(label) {
    const wrap = node("label", label), input = node("textarea");
    input.rows = 3; input.maxLength = 20000;
    wrap.append(input); form.append(wrap);
    return input;
  }
  const positive = field("正向提示词"), negative = field("负向提示词");
  negative.placeholder = "可留空";
  function remember() {
    const row = rows.get(panels[index].id);
    drafts[row.panel_id] = {hash:row.panel_hash, source:[row.positive,row.negative],
      positive:positive.value, negative:negative.value};
    syncDraw();
  }
  positive.oninput = negative.oninput = remember;
  const draw = button("画这一格", async () => {
    const row = rows.get(panels[index].id);
    const values = {...row, positive:positive.value, negative:negative.value};
    await act("draw", {...request, target:row.panel_id, candidate_count:Number(count.value),
      confirm_prompts:true, prompt_rows:[values]});
    const draft = drafts[row.panel_id];
    if (draft && draft.positive === values.positive && draft.negative === values.negative) delete drafts[row.panel_id];
    else if (draft) draft.source = [values.positive, values.negative];
    Object.assign(row, values);
    if (view.isConnected) saveWorkspace();
    notify(row.panel_id + " 已开始绘制。");
  }, "primary");
  draw.dataset.startJob = "true";
  const countLabel = node("label", "候选张数"), count = node("select");
  for (let n = 1; n <= 16; n++) count.append(node("option", String(n)));
  count.value = String(workspaceState.reviewCount || 8);
  count.onchange = () => {workspaceState.reviewCount = Number(count.value);};
  countLabel.append(count);
  const mark = button("标记有问题 ↓", () => openNote());
  const actions = node("div", undefined, "reader-actions");
  actions.append(countLabel, draw, mark);
  view.append(header, info, canvas, strip, node("small", "1–8 选图　·　← → 翻页　·　Enter 下一页　·　↓ 写问题　·　Esc 结束提示词输入"), form, actions, story);
  form.onkeydown = event => {
    if (event.key === "Escape" && !event.isComposing && event.keyCode !== 229) {
      event.preventDefault(); view.focus({preventScroll:true});
    }
  };

  function syncDraw() {
    draw.dataset.unavailable = String(!positive.value.trim());
    draw.disabled = draw.dataset.unavailable === "true" || !!window.activeStudioJob || draw.hasAttribute("aria-busy");
  }
  function candidates(panel) {
    return images.batches.filter(b => b.panel_id === panel.id && b.panel_hash === rows.get(panel.id).panel_hash)
      .reverse().flatMap(batch => batch.images);
  }
  function imageUrl(image) {
    const [bundle, file] = image.file.split("/");
    return "/api/image?" + new URLSearchParams({id:pid, bundle, file});
  }
  function renderImages() {
    const panel = panels[index], items = candidates(panel);
    const selected = images.selections[panel.id];
    const chosen = selected?.panel_hash === rows.get(panel.id).panel_hash ? selected.image : null;
    const shown = chosen || items[group * 8];
    figure.replaceChildren(); strip.replaceChildren();
    if (shown) {
      const img = node("img");
      img.src = imageUrl(shown); img.alt = panel.id + (chosen ? " 已选图片" : " 候选预览");
      figure.append(img);
    } else figure.append(node("p", "还没有图片。填写下方提示词后，点击「画这一格」。"));
    items.slice(group * 8, group * 8 + 8).forEach((image, slot) => {
      const selectedNow = image.id === chosen?.id;
      const pick = button("", async () => {
        choosing = choosing.catch(() => {}).then(async () => {
          const result = await act("select_image", {project:pid, target:panel.id, image:image.id,
            allow_rejected:image.review?.verdict === "reject", allow_unreviewed:true});
          images.selections[panel.id] = result.selections[panel.id];
          if (!view.isConnected) return;
          ctx.clearFinishedCache();
          if (panels[index].id === panel.id) renderImages();
        });
        await choosing;
      });
      const thumb = node("img"); thumb.alt = ""; thumb.src = imageUrl(image);
      const verdict = image.review?.verdict === "reject" ? " · 审核未通过" : "";
      pick.dataset.slot = String(slot + 1);
      pick.setAttribute("aria-pressed", String(selectedNow));
      pick.setAttribute("aria-label", "选择候选图 " + (group * 8 + slot + 1) + verdict);
      pick.append(thumb, node("span", (selectedNow ? "✓ " : "") + (slot + 1) + verdict)); strip.append(pick);
    });
    if (items.length > 8) {
      const groups = Math.ceil(items.length / 8);
      strip.append(button(`候选组 ${group + 1}/${groups} · 切换`, () => {group = (group + 1) % groups; renderImages();}));
    }
    draw.textContent = items.length ? "重画这一格" : "画这一格";
  }
  function show() {
    const panel = panels[index], row = rows.get(panel.id), draft = drafts[panel.id];
    workspaceState.reviewPanel = panel.id;
    title.textContent = `第 ${index + 1} / ${panels.length} 格 · ${panel.id}`;
    info.textContent = panel.main_instant || panel.purpose;
    const valid = draft?.hash === row.panel_hash && draft.source?.[0] === row.positive && draft.source?.[1] === row.negative;
    positive.value = valid ? draft.positive : row.positive || ("first-person POV, " + (panel.drawing_description || panel.main_instant || "")).trimEnd();
    negative.value = valid ? draft.negative : row.negative;
    previous.dataset.unavailable = String(index === 0); previous.disabled = index === 0;
    next.textContent = index === panels.length - 1 ? "问题汇总 →" : "下一页 →";
    mark.textContent = marked.has(panel.id) ? "已标记 · 编辑问题 ↓" : "标记有问题 ↓";
    summary.textContent = "问题汇总 · " + marked.size;
    story.replaceChildren(node("summary", "查看分镜与对白"), window.panelStoryPreview(project, panel.id, node));
    renderImages(); syncDraw(); saveWorkspace();
  }
  async function move(step) {
    if (index + step < 0) return;
    if (index + step >= panels.length) {await window.openProblemPanels(); return;}
    index += step; group = 0; show(); view.focus({preventScroll:true});
    root.scrollTop = 0;
  }
  async function openNote() {
    const panel = panels[index];
    await flush();
    if (!view.isConnected || panels[index] !== panel || document.querySelector("dialog[open]")) return;
    const image = images.selections[panel.id];
    const reference = image?.panel_hash === rows.get(panel.id).panel_hash ? image.image : candidates(panel)[group * 8];
    if (!marked.has(panel.id)) {
      const result = await act("save_problem", {project:pid, panel_id:panel.id, ...(reference && {image_id:reference.id})});
      marked.set(panel.id, result.items.find(item => item.panel_id === panel.id));
      if (!view.isConnected || panels[index] !== panel) return;
      mark.textContent = "已标记 · 编辑问题 ↓";
      summary.textContent = "问题汇总 · " + marked.size;
    }
    const dialog = node("dialog", undefined, "reader-note");
    dialog.setAttribute("aria-label", panel.id + " 的问题");
    const label = node("label", "这格有什么问题？"), note = node("textarea");
    note.rows = 4; note.maxLength = 4000; note.value = marked.get(panel.id)?.note || "";
    label.append(note);
    const save = button("保存问题并继续", async () => {
      await act("save_problem", {project:pid, panel_id:panel.id, note:note.value, ...(reference && {image_id:reference.id})});
      marked.set(panel.id, {note:note.value});
      dialog.close(); await move(1);
    }, "primary");
    note.onkeydown = event => {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
        event.preventDefault(); if (!event.repeat) save.click();
      }
    };
    dialog.append(node("h2", panel.id + " · 问题说明"), label, node("small", "Enter 保存并继续　·　Shift + Enter 换行"),
      button("返回审图", () => dialog.close()), save);
    dialog.onclose = () => {dialog.remove(); if (view.isConnected) view.focus({preventScroll:true});};
    view.append(dialog); dialog.showModal(); note.focus();
  }
  function keydown(event) {
    if (!view.isConnected || document.body.classList.contains("at-home") || document.querySelector("dialog[open]") ||
        event.defaultPrevented || event.isComposing || event.keyCode === 229 || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey ||
        event.target.closest?.("input, textarea, select, [contenteditable=true]")) return;
    if (event.target !== document.body && !view.contains(event.target)) return;
    if (event.key === "Enter" && event.target.closest?.("button,summary,a")) return;
    const control = /^[1-8]$/.test(event.key) ? strip.querySelector(`[data-slot="${event.key}"]`) :
      ({ArrowDown:mark, ArrowLeft:previous, ArrowRight:next, Enter:next})[event.key];
    if (control) {event.preventDefault(); if (!event.repeat) control.click();}
  }
  async function refresh() {
    const latest = await api(drawingUrl);
    if (!view.isConnected) return;
    images = latest.images; renderImages();
  }
  show(); view.focus({preventScroll:true});
  return {keydown, refresh, flush};
};
