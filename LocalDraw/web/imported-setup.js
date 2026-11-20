"use strict";

async function openImportedSetup(project, bundle, rows) {
  const [library, data] = await Promise.all([api("/api/characters"), api("/api/drawing-options")]);
  const model = data.comfy.configured;
  const draftKey = "import-setup-" + project + "-" + bundle;
  let draft = {};
  try { draft = JSON.parse(localStorage.getItem(draftKey)) || {}; } catch {}
  const cast = new Map(), weights = new Map(), counts = new Map();
  const cards = library.characters;
  const files = data.comfy.loras.filter(file => file.startsWith(data.comfy.lora_directory + "/")
    && data.lora_inspection?.[file]?.structurally_compatible);
  for (const card of cards) if (draft.roles?.includes(card.id)) cast.set(card.id, card);
  for (const file of files) if (Object.hasOwn(draft.weights || {}, file)) weights.set(file, draft.weights[file]);
  const page = node("dialog", undefined, "import-setup");
  page.setAttribute("aria-labelledby", "import-setup-title");
  const top = node("div", undefined, "setup-top"), heading = node("h1", "逐格出图设置");
  heading.id = "import-setup-title";
  const close = button("稍后再画", () => page.close());
  top.append(heading, close);
  const body = node("div", undefined, "setup-body");
  const libraries = node("div", undefined, "setup-libraries");
  const footer = node("div", undefined, "setup-footer"), total = node("strong");
  const status = node("p"); status.setAttribute("role", "status");
  const count = node("input");
  count.type = "number"; count.min = "1"; count.max = "16"; count.required = true;
  count.value = draft.count ?? 2;
  const commonNegative = node("textarea"), negativeEdits = new Map();
  commonNegative.rows = 2;
  commonNegative.maxLength = 20000;
  commonNegative.value = draft.common_negative ?? "";
  commonNegative.placeholder = "例如：水印、多余文字";
  commonNegative.oninput = remember;
  let submitting = false, started = false;
  function quantity() {
    return rows.reduce((sum, row) => sum + Number(counts.get(row.panel_id) || count.value), 0);
  }
  function remember() {
    const saved = cacheLocal(draftKey, JSON.stringify({count:count.value, counts:Object.fromEntries(counts),
      common_negative:commonNegative.value, negative_by_panel:Object.fromEntries(negativeEdits),
      roles:[...cast.keys()], weights:Object.fromEntries(weights)}));
    if (!saved) status.textContent = "本机未能保存设置草稿，关闭前请复制编辑过的提示词。";
    const valid = count.checkValidity() && [...counts.values()].every(n => Number.isInteger(Number(n)) && +n >= 1 && +n <= 16);
    total.textContent = valid ? rows.length + " 格 · 共 " + quantity() + " 张" : "每格请输入 1–16 张";
  }
  function picker(title, hint, values, selected, key, name, matches, limit, initial, imageUrl) {
    const box = node("section", undefined, "setup-section"), search = node("input");
    search.type = "search"; search.placeholder = "搜索" + title; search.setAttribute("aria-label", search.placeholder);
    const list = node("div", undefined, imageUrl ? "setup-role-grid" : "setup-lora-list");
    const picks = node("div", undefined, "setup-picks"), nav = node("div", undefined, "setup-nav");
    let offset = 0;
    const length = imageUrl ? 8 : 6;
    const mark = node("small"), prev = button("上一页", () => {offset -= length; render();});
    const next = button("下一页", () => {offset += length; render();});
    nav.append(mark, prev, next);
    function toggle(v) {
      const k = key(v);
      if (selected.has(k)) selected.delete(k);
      else if (selected.size < limit) selected.set(k, initial(v));
      else {status.textContent = title + "最多选择 " + limit + " 个"; return;}
      status.textContent = ""; render(); remember();
    }
    function render() {
      const found = values.filter(v => matches(v, search.value));
      offset = Math.max(0, Math.min(offset, Math.max(0, Math.floor((found.length-1)/length)*length)));
      list.replaceChildren(); picks.replaceChildren();
      for (const v of found.slice(offset, offset + length)) {
        const k = key(v), label = node("label", undefined, "setup-option"), check = node("input");
        check.type = "checkbox"; check.checked = selected.has(k);
        check.disabled = !check.checked && selected.size >= limit;
        check.onchange = () => {
          toggle(v);
          [...list.querySelectorAll("input")].find(el => el.value === k)?.focus({preventScroll:true});
        };
        check.value = k; label.append(check);
        if (imageUrl && imageUrl(v)) {
          const img = node("img"); img.src = imageUrl(v); img.alt = ""; img.loading = "lazy"; label.append(img);
        }
        label.append(node("span", name(v))); label.title = imageUrl ? name(v) : k;
        list.append(label);
      }
      if (!found.length) list.append(node("p", search.value ? "没有匹配项" : "暂无可选" + title));
      for (const [k, val] of selected) {
        const chip = node("div", undefined, "setup-chip");
        const text = imageUrl ? val.name : name(k);
        chip.append(node("span", text));
        if (!imageUrl) {
          const strength = node("input"); strength.type = "number"; strength.min = "0"; strength.max = "2";
          strength.step = "any"; strength.required = true; strength.value = val;
          strength.setAttribute("aria-label", text + " 强度");
          strength.oninput = () => {selected.set(k, strength.value); remember();};
          const weightLabel = node("label", "强度"); weightLabel.append(strength); chip.append(weightLabel);
        }
        const remove = button("×", () => {selected.delete(k); render(); remember();});
        remove.setAttribute("aria-label", "移除" + text); chip.append(remove); picks.append(chip);
      }
      mark.textContent = "已选 " + selected.size + "/" + limit + " · " + (found.length ? Math.floor(offset/length)+1 : 0) + "/" + Math.ceil(found.length/length) + " 页";
      prev.disabled = offset === 0; next.disabled = offset + length >= found.length;
      prev.dataset.unavailable = String(prev.disabled); next.dataset.unavailable = String(next.disabled);
    }
    search.oninput = () => {offset=0; render();};
    box.append(node("h2",title), node("p",hint), search, picks, list, nav);
    libraries.append(box); render();
  }
  picker("角色", "所选角色的外观用于本批每一格，导入原文单独保存。",
    cards, cast, c=>c.id, c=>c.name, characterMatchesSearch, 4, c=>c,
    c=>c.image_id ? "/api/character-image?id="+encodeURIComponent(c.image_id) : "");
  picker("LoRA", "当前模型 · " + (data.diffusion_models?.[model]?.label || model),
    files, weights, f=>f, f=>f.split("/").pop().replace(/\.[^.]+$/, "").replaceAll("_", " · "),
    (f,q)=>loraMatchesSearch(f,data.lora_inspection[f],q),
    5, f=>data.lora_labels?.[f]?.weight ?? 0.7);

  const quantities = node("section",undefined,"setup-section setup-quantities");
  const commonLabel = node("label","通用负面提示词", "setup-negative");
  commonLabel.append(commonNegative);
  const defaultLabel = node("label","默认每格张数"); defaultLabel.append(count);
  const details = node("details"), entries = node("div",undefined,"setup-panels");
  details.append(node("summary","逐格编辑负面提示词和张数"),entries);
  for (const row of rows) {
    const id = row.panel_id, label = row.source || id;
    if (draft.counts?.[id]) counts.set(id, draft.counts[id]);
    const line = node("div"), n = node("input");
    n.type="number"; n.min="1"; n.max="16"; n.placeholder=count.value; n.value=counts.get(id) || "";
    n.setAttribute("aria-label",label+" 生成张数");
    n.oninput=()=>{if(n.value)counts.set(id,n.value);else counts.delete(id);remember();};
    const negative = node("textarea"), negativeLabel = node("label","本格负面提示词","setup-negative");
    negative.rows = 2; negative.maxLength = 20000;
    negative.setAttribute("aria-label",label+" 负面提示词");
    const original = row.negative ?? "";
    if (Object.hasOwn(draft.negative_by_panel || {}, id)) negativeEdits.set(id, draft.negative_by_panel[id]);
    negative.value = negativeEdits.get(id) ?? original;
    negative.oninput = () => {
      if (negative.value === original) negativeEdits.delete(id);
      else negativeEdits.set(id, negative.value);
      remember();
    };
    negativeLabel.append(negative);
    line.append(node("strong",label),node("p",row.positive),n,negativeLabel); entries.append(line);
  }
  count.oninput=()=>{for(const n of entries.querySelectorAll("input"))n.placeholder=count.value;remember();};
  quantities.append(node("h2","逐格设置"),commonLabel,node("p","生成时，通用负面提示词会追加到每一格的负面提示词末尾。"),defaultLabel,details);
  body.append(libraries,quantities);
  const start = button("开始生成", async () => {
    if (submitting) return;
    const invalid = [...page.querySelectorAll("input")].find(input => !input.checkValidity());
    if (invalid) {if(entries.contains(invalid)) details.open=true; invalid.reportValidity(); return;}
    if (window.activeStudioJob) {status.textContent="当前任务正在运行，结束后即可开始。";return;}
    submitting=true; close.disabled=true; body.inert=true; status.textContent="正在提交出图任务…";
    try {
      await act("draw_imported", {project,bundle,targets:rows.map(r=>r.panel_id),checkpoint:model,
        candidate_count:Number(count.value),import_setup:{
          characters:[...cast.values()].map(c=>({name:c.name,revision:c.revision})),
          loras:[...weights].map(([file,weight])=>({file,weight:Number(weight)})),
          common_negative:commonNegative.value, negative_by_panel:Object.fromEntries(negativeEdits),
          counts:Object.fromEntries([...counts].map(([k,v])=>[k,Number(v)]))
        }});
    } catch(error) {status.textContent=error.message;return;}
    finally {submitting=false; close.disabled=false;body.inert=false;}
    started=true; page.close();
    await openProject(project,true,"drawing");
    if(stage!=="drawing"){stage="drawing";target="";await renderStage();}
    window.leaveStudioHome?.(); notify("已开始生成，共 "+quantity()+" 张。");
  },"primary");
  footer.append(total,status,start);
  page.append(top,body,footer); page.oncancel=e=>{if(submitting)e.preventDefault();};
  page.onclose=()=>{page.remove();if(!started && document.body.classList.contains("at-home"))window.showStudioHome().catch(e=>notify(e.message,true));};
  document.body.append(page);page.showModal();remember();
}
