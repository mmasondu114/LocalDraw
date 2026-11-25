"use strict";

function panelJson(id, positive, negative) {
  return {id, visual_prompt:positive, negative};
}

async function copyPromptText(text) {
  try {await navigator.clipboard.writeText(text); return;} catch {}
  const host = document.activeElement?.closest("dialog") || [...document.querySelectorAll("dialog[open]")].at(-1) || document.body;
  const field = node("textarea"), previous = document.activeElement;
  field.className = "clipboard-fallback"; field.value = text; host.append(field);
  try {
    field.select();
    if (!document.execCommand("copy")) throw Error("复制失败，请在 JSON 框中全选并按 Ctrl+C。");
  } finally {field.remove();previous?.focus({preventScroll:true});}
}

function promptJsonEditor(positive, negative, id, state, change, fixedId=false, boardMode=false, selectedCast) {
  const group=node("fieldset",undefined,"prompt-json-editor"), tools=node("div",undefined,"prompt-json-tools");
  group.setAttribute("aria-label","提示词编辑器");
  const modes=node("div",undefined,"prompt-mode-switch"), modeName="prompt-mode-"+(++promptModeSequence);
  modes.setAttribute("role","radiogroup");modes.setAttribute("aria-label","编辑模式");
  const fieldLabel=node("label","本格 JSON"), field=node("textarea");
  const wardrobe=node("section",undefined,"wardrobe-panel");
  let refreshRoleHint;
  state.mode=state.raw!==undefined || state.enabled===true ? "json" : state.mode==="wardrobe" ? "wardrobe" : "text";
  state.enabled=state.mode==="json";
  for(const [value,title] of [["text","正向 / 负面"],["json","JSON"],["wardrobe","Anima 衣装台"]]){
    const label=node("label"), radio=node("input");
    radio.type="radio";radio.name=modeName;radio.value=value;
    radio.onchange=()=>switchMode(value);
    label.append(radio,node("span",title));modes.append(label);
  }
  field.spellcheck=false;field.rows=7;fieldLabel.append(field);
  const status=node("small");status.setAttribute("role","status");
  let lastPositive=positive.value,lastNegative=negative.value;
  const encode=()=>JSON.stringify([panelJson(state.id || id,positive.value,negative.value)],null,2);
  function read() {
    let data;
    try { data=JSON.parse(field.value); } catch {throw Error("JSON 格式有误，请检查引号、逗号或括号。");}
    let row=Array.isArray(data) && data.length===1 ? data[0] : data;
    if(boardMode && row && !Array.isArray(row))row={id:boardPanelId(row.id || row.panel_id),
      visual_prompt:row.visual_prompt ?? row.positive ?? row.prompt,negative:row.negative ?? row.negative_prompt ?? ""};
    if (!row || typeof row.id!=="string" || !/^P\d+-K\d+$/.test(row.id) || (fixedId && row.id!==id))
      throw Error(fixedId ? "请保留本格编号 "+id+"。" : "请填写一格，编号如 P001-K01。");
    if (typeof row.visual_prompt!=="string" || typeof row.negative!=="string")
      throw Error("visual_prompt 和 negative 都要填字符串，负面可填空字符串。");
    if ([row.visual_prompt,row.negative].some(s=>Array.from(s).length>20000)) throw Error("每项提示词最多 20000 字。");
    return row;
  }
  function validate() {
    try {const row=read();field.setCustomValidity("");return row;}
    catch(error) {field.setCustomValidity(error.message);return null;}
  }
  function display() {
    for(const radio of modes.querySelectorAll("input"))radio.checked=radio.value===state.mode;
    positive.parentElement.hidden=negative.parentElement.hidden=state.mode!=="text";
    fieldLabel.hidden=tools.hidden=!state.enabled;
    wardrobe.hidden=state.mode!=="wardrobe";
    if(!wardrobe.hidden && !wardrobe.firstChild){
      const content=node("div");wardrobe.append(content);
      renderWardrobe(content,positive,()=>state.mode==="wardrobe" && !group.disabled,()=>{
        resetText();change(true);
      },selectedCast,refresh=>{refreshRoleHint=refresh;});
    }
    const error=state.enabled && field.validationMessage;
    field.setAttribute("aria-invalid",String(Boolean(error)));reset.hidden=!error;
    status.textContent=error || "";
  }
  function resetText() {
    delete state.raw;
    field.value=encode();field.setCustomValidity("");lastPositive=positive.value;lastNegative=negative.value;
    display();
  }
  const reset=button("还原为上次有效内容",()=>{resetText();change(false);field.focus();});
  const copy=button("复制本格 JSON",async()=>{
    if(!validate()){display();field.reportValidity();return;}
    const text=encode();
    await copyPromptText(text);status.textContent="已复制本格 JSON";
  });
  tools.append(copy,reset);group.append(modes,fieldLabel,tools,status,wardrobe);
  positive.parentElement.before(group);
  field.value=state.raw ?? encode();validate();display();
  field.oninput=()=>{
    const row=validate();
    if(row) {
      if(row.id!==(state.id || id))wardrobe.replaceChildren();
      positive.value=row.visual_prompt;negative.value=row.negative;state.id=row.id;
      lastPositive=positive.value;lastNegative=negative.value;delete state.raw;
    } else {state.raw=field.value;}
    display();change(true);
  };
  function switchMode(value){
    if(state.enabled && !validate()){display();field.focus();field.reportValidity();return;}
    state.mode=value;state.enabled=value==="json";resetText();change(false);
  }
  return {group,state,
    setId:value=>{if(value!==(state.id || id))wardrobe.replaceChildren();state.id=value;resetText();},
    valid:()=>!state.enabled || field.validity.valid,
    refresh:()=>{refreshRoleHint?.();if(positive.value!==lastPositive || negative.value!==lastNegative)resetText();}
  };
}

let promptModeSequence=0, wardrobeCatalog;

function wardrobeButton(positive, canEdit = () => true, afterApply = () => {}, selectedCast) {
  return button("Anima 衣装台", () => {
    if (!canEdit()) return;
    const dialog = node("dialog", undefined, "wardrobe-dialog");
    dialog.setAttribute("aria-label", "Anima 衣装台");
    const heading = node("div", undefined, "wardrobe-heading");
    heading.append(node("h2", "Anima 衣装台"), button("关闭", () => dialog.close()));
    const content=node("div");
    dialog.append(heading, content);
    document.body.append(dialog);
    dialog.onclose = () => dialog.remove();
    dialog.showModal();
    renderWardrobe(content,positive,canEdit,()=>{afterApply();dialog.close();},selectedCast);
  }, "small");
}

async function renderWardrobe(content,positive,canEdit,afterApply,selectedCast=()=>[],onReady) {
    const status = node("p", "正在读取 V4 词库…");
    status.setAttribute("role", "status");
    content.replaceChildren(status);
    try {
      wardrobeCatalog ||= await api("/api/wardrobe");
    } catch (error) {
      status.textContent = error.message.includes("页面不存在")
        ? "当前连接的是旧后台。双击 LocalDraw.exe 同目录中的 LocalDraw-Repair.cmd，看到预览验证成功后重新打开衣装台。"
        : "读取 V4 词库失败：" + error.message;
      content.append(button("重新读取词库",()=>renderWardrobe(content,positive,canEdit,afterApply,selectedCast,onReady)));
      return;
    }
    if (status.parentElement !== content) return;
    const data = wardrobeCatalog;
    const controls = node("div", undefined, "wardrobe-controls");
    function field(label, control) {
      const wrap = node("label", label);
      control.setAttribute("aria-label",label);
      wrap.append(control);
      controls.append(wrap);
      return control;
    }
    function select(label, choices) {
      const input = field(label, node("select"));
      for (const [value, text] of choices) input.add(new Option(text, value));
      return input;
    }
    const scope = select("生成范围", [["outfit","仅服装"],["character","角色与服装"],["scene","完整画面"]]);
    scope.value = "character";
    const style = select("服装风格", data.styles.map(s => [s,s]));
    style.value = "不限";
    const search = field("搜索角色或作品", node("input"));
    search.type = "search";
    search.placeholder = "角色或作品名 · 支持全拼、首字母";
    const role = select("角色", []);
    const roleHint = node("small");
    role.parentElement.append(roleHint);
    let generatedText = "";
    function refreshRoleHint() {
      const followsCast = !role.value && scope.value !== "outfit";
      const cast = followsCast ? selectedCast(generatedText) : [];
      roleHint.textContent = role.value ? "使用此角色的 V4 外观词。" : cast.length
        ? "沿用已选角色：" + cast.map(c=>c.name).join("、") + "。角色卡外观在出图时合入。"
        : "未指定角色，预览只生成衣装或场景词。";
      preview.value = generatedText ? [generatedText,...cast.map(c=>c.appearance).filter(Boolean)].join("\n\n") : "";
    }
    const readings = JSON.parse(document.getElementById("wardrobe-pinyin").textContent);
    const searchKey = text => normalizeLoraSearch(text.normalize("NFC").replace(/[üǖǘǚǜ]/gi,"v"));
    function keysFor(label) {
      const keys = [searchKey(label)];
      if (readings[label]) {
        const syllables = readings[label].split(/\s+/).map(searchKey).filter(Boolean);
        keys.push(syllables.join(""), syllables.map(s => s[0]).join(""));
      }
      return keys;
    }
    const indexed = data.characters.map(r => {
      const names = [...keysFor(r.zh),searchKey(r.name),searchKey(r.tag)];
      return {role:r, names, keys:[...names,...keysFor(r.work)]};
    });
    function filterRoles() {
      const term = searchKey(search.value), previous = role.value;
      const options = document.createDocumentFragment();
      if (!term) options.append(new Option("无", ""));
      const matches = term ? indexed.filter(entry => entry.keys.some(key => key.includes(term))) : indexed;
      for (const {role:r} of matches) {
        options.append(new Option((r.zh || r.name) + " · " + r.work, r.tag));
      }
      role.replaceChildren(options);
      const exact = term && matches.find(entry => entry.names.includes(term));
      if (exact) role.value = exact.role.tag;
      else if (previous && matches.some(entry => entry.role.tag === previous)) role.value = previous;
    }
    filterRoles();
    const level = select("内容分级", Object.entries(data.levels));
    level.value = "sfw";
    const seed = field("提示词种子", node("input"));
    seed.type = "number"; seed.min = "0"; seed.max = "2147483647"; seed.step = "1";
    seed.placeholder = "留空，每次随机";
    const previewLabel = node("label", "英文提示词预览");
    const preview = node("textarea"); preview.rows = 4; preview.readOnly = true;
    previewLabel.append(preview);
    const summary = node("p", undefined, "wardrobe-summary");
    const actions = node("div", undefined, "wardrobe-actions");
    let revision = 0, original;
    function enableApply(enabled) {
      for(const action of [append,replace]){
        action.dataset.unavailable=String(!enabled);
        action.disabled=!enabled;
      }
    }
    function invalidate() {
      revision++;
      generatedText = summary.textContent = "";
      enableApply(false);
      search.parentElement.hidden = role.parentElement.hidden = scope.value === "outfit";
      refreshRoleHint();
      status.textContent = scope.value !== "outfit" && !role.options.length ? "没有匹配的角色，请换个关键词。" : "";
    }
    const generate = button("生成预览", async () => {
      if (!seed.reportValidity()) return;
      if (scope.value !== "outfit" && !role.options.length) { search.focus(); return; }
      invalidate();
      const current = revision;
      original=positive.value;
      append.textContent=original.trim() ? "追加到正向提示词" : "填入正向提示词";
      replace.hidden=!original.trim();
      status.textContent = "正在生成…";
      try {
        const inherit = !role.value && scope.value !== "outfit";
        if (inherit && scope.value==="scene" && !data.studio_cast)
          throw Error("请重启工作室后台，以使用沿用已选角色的完整画面预览。");
        const result = await act("wardrobe_preview", {scope:inherit && !data.studio_cast ? "outfit" : scope.value, style:style.value,
          character:scope.value === "outfit" ? "" : role.value, level:level.value,
          seed:seed.value === "" ? null : seed.valueAsNumber});
        if (!content.isConnected || current !== revision) return;
        generatedText = result.text; refreshRoleHint(); summary.textContent = result.summary;
        status.textContent = "提示词种子 " + result.seed;
        enableApply(true);
      } catch (error) {
        if (current === revision) status.textContent = "生成失败：" + error.message;
      }
    });
    function apply(replacing) {
      if (!positive.isConnected || positive.value !== original || positive.disabled || positive.readOnly || !canEdit()) {
        status.textContent = "当前提示词已变化，请重新生成预览。";
        return;
      }
      const text = replacing || !original.trim() ? generatedText : original.trimEnd() + ", " + generatedText;
      const limit = positive.maxLength >= 0 ? positive.maxLength : 20000;
      if (text.length > limit) { status.textContent = "写入后超过 " + limit + " 字，请先缩短提示词。"; return; }
      positive.value = text;
      positive.dispatchEvent(new Event("input", {bubbles:true}));
      enableApply(false);
      status.textContent = "已写入本格正向提示词";
      afterApply();
    }
    const append = button(positive.value.trim() ? "追加到正向提示词" : "填入正向提示词", () => apply(false), "primary");
    const replace = button("替换正向提示词", () => apply(true));
    replace.hidden = !positive.value.trim();
    actions.append(generate,replace,append);
    controls.addEventListener("input", event => {
      if (event.target === search) filterRoles();
      invalidate();
    });
    content.insertBefore(controls, status);
    content.append(previewLabel, summary, actions);
    invalidate();
    onReady?.(refreshRoleHint);
}
