"use strict";
async function startPromptDraw(request) {
  if (!legacyPromptRounds.size) {
    try { return await act("draw_quick_sample", request); }
    catch (error) { if (error.message !== "未知操作") throw error; }
  }
  // Older running servers create a separate temporary project for each draw.
  const {project} = await act("create_quick_sample", {...request, project:null});
  let snapshot;
  try {
    snapshot = await api("/api/project?id=" + encodeURIComponent(project));
    const {job} = await act("prompt_batch", {project, base_hash:snapshot.hash});
    if (legacyPromptRounds.has(promptSessionId)) legacyPromptRounds.get(promptSessionId).batches=null;
    legacyPromptRounds.set(project, {snapshot, batches:null});
    return {job, project, snapshot};
  } catch (error) {
    // A lost response can follow a successful submission. Keep its images available.
    if (snapshot && error.message.startsWith("无法连接")) {
      legacyPromptRounds.set(project, {snapshot, batches:null});
      promptSessionId = project;
    } else {
      await act("release_prompt", {project});
    }
    throw error;
  }
}
window.renderPromptStudio = async function(root, project) {
  const renderToken = root.dataset.renderToken;
  const [data, initialCharacters] = await Promise.all([
    api("/api/drawing-options"), api("/api/characters?compact=1"),
  ]);
  if (root.dataset.renderToken !== renderToken) return;
  const clothingPolicy = createClothingPolicy(initialCharacters.clothing_policy);
  const board=!!project?.prompt_board;
  let draftKey="studio-prompt-draft-"+(board || (project?.quick_sample&&!project.transient&&pid&&!pid.startsWith("tmp-"))?pid:"new");
  window.promptStudioDrafts ||= new Map();
  let draft=window.promptStudioDrafts.get(draftKey);
  try{const stored=JSON.parse(localStorage.getItem(draftKey)||"null");if(stored?.version===1&&(stored.updated||0)>(draft?.updated||0))draft=stored;}catch{}
  if(draft?.version!==1 || !draft.input || typeof draft.input!=="object")draft=null;
  const saved = draft?.input || (project?.quick_sample ? project.prompt_input : {}) || {};
  const isDiffusion = name => !!data.diffusion_models?.[name];
  let animaActive = isDiffusion(saved.checkpoint || data.preloaded_checkpoint || data.comfy.configured);
  let thisProject = board || project?.quick_sample ? pid : null;
  const panelChoice=node("select"), panelHint=node("small"), panelLabel=node("label","当前画格");
  panelChoice.setAttribute("aria-label","当前画格");
  const panels=board ? Object.values(project.pages).flatMap(page=>page.panels) : [];
  for(const panel of panels)panelChoice.add(new Option(panel.id+" · "+panel.aspect_ratio,panel.id));
  if(panels.some(p=>p.id===saved.board_target))panelChoice.value=saved.board_target;
  let syncPreview;
  function describePanel(){
    const selected=panels[panelChoice.selectedIndex];
    panelHint.textContent=selected?.drawing_description || "提示词编号在本作品的分镜中不存在。";
    syncPreview?.();
  }
  panelLabel.append(panelChoice,panelHint);describePanel();
  root.className = "prompt-studio";
  root.replaceChildren(node("h2", board ? project.brief.title : "直接输入提示词画图"),
    node("p", "输入画面描述，选择角色并填写外观。可先整理 / 转换英文提示词，核对后出图；多人可指定位置并分区绘制外观。"));
  const modelDescription = root.children[1];
  let jsonEditor;
  const positive = labeledInput(root, "正向提示词（想画什么）", true, saved.text || "");
  positive.rows = 12; positive.dataset.editKey = "prompt-positive";
  positive.placeholder = "例如：1girl, red hair, black jacket, city street, clean lineart";
  const negative = labeledInput(root, "负面提示词（不想出现，可留空）", true, saved.negative || "");
  negative.rows = 2; negative.dataset.editKey = "prompt-negative";
  function addCopy(input, name) {
    const label = input.parentElement, caption = node("span", undefined, "prompt-label-tools");
    const text = label.firstChild; caption.append(text);
    const copy = button("复制", async () => {
      await navigator.clipboard.writeText(input.value);
      notify(name + "已复制");
    }, "small");
    copy.setAttribute("aria-label", "复制" + name);
    const tools = node("span", undefined, "prompt-clipboard-tools");
    tools.append(copy);
    if (input === positive) {
      const paste = button("粘贴", async () => {
        const before = input.value, start = input.selectionStart, end = input.selectionEnd;
        let text;
        try {
          text = await navigator.clipboard.readText();
        } catch {
          throw Error("无法读取剪贴板，请允许剪贴板访问，或在正向提示词框内按 Ctrl+V 粘贴。");
        }
        if (!text) return notify("剪贴板中没有可粘贴的文字。", true);
        if (input.value !== before) throw Error("提示词已发生变化，请重新点击粘贴。");
        if(board && useBoardPaste(text))return;
        input.setRangeText(text, start, end, "end");
        input.dispatchEvent(new Event("input", {bubbles:true}));
        input.focus();
        notify("已粘贴到正向提示词。");
      }, "small");
      paste.setAttribute("aria-label", "粘贴到正向提示词");
      paste.title = "粘贴到光标位置；选中文字时替换选中内容";
      tools.append(paste);
    }
    caption.append(tools); label.prepend(caption);
  }
  addCopy(positive, "正向提示词"); addCopy(negative, "负面提示词");
  function useBoardPaste(text){
    const row=pastedBoardPrompt(text);if(!row)return false;
    if(!panels.some(p=>p.id===row.id))throw Error("分镜中没有 "+row.id+"，请核对编号。");
    panelChoice.value=row.id;describePanel();
    positive.value=row.positive;if(row.negative!==undefined)negative.value=row.negative;
    jsonEditor.setId(row.id);positive.dispatchEvent(new Event("input",{bubbles:true}));window.savePromptDraft?.();
    return true;
  }
  if(board)positive.addEventListener("paste",event=>{
    try{if(useBoardPaste(event.clipboardData.getData("text/plain")))event.preventDefault();}
    catch(error){event.preventDefault();notify(error.message,true);}
  });
  const characterBox = node("section", undefined, "candidate prompt-characters");
  characterBox.append(node("h3", "已选角色"));
  characterBox.title="未提到衣着时才补入角色默认服装。";
  const characterList = node("div", undefined, "prompt-character-list");
  const noCharacters=node("small","未选择角色"), noLoras=node("small","未选择 LoRA");
  const languageNotice = node("p", undefined, "warning");
  const bindingStatus = node("p"); bindingStatus.dataset.characterBinding = "true";
  const preview = labeledInput(characterBox, "最终正向提示词预览", true);
  preview.readOnly = true; preview.rows = 5; delete preview.dataset.editKey;
  let characterRows = [], loraPicker = null;
  const chatCard = /(?:^|\n)\s*(?:#{1,6}\s*)?(?:\*\*)?(?:personality|speech\s+style|interaction\s+guidelines|background|relationships|system\s+prompt|性格(?:与行为习惯)?|说话方式(?:与台词样例)?|互动(?:规则|指南)|人物关系|背景(?:设定)?|系统提示词)\s*(?:\*\*)?\s*[:：]/i;
  function selectCharacterLoras(id, selected) {
    if (!loraPicker) return;
    const owned = loraPicker.rows.filter(r => r.owner.value === id);
    if (selected) {
      const available = owned.filter(r => !r.check.disabled);
      if (!available.some(r => r.check.checked)) {
        const preferred = available.find(r => data.lora_labels?.[r.file]?.preferred) || available[0];
        if (preferred) {preferred.check.checked = true; preferred.item.selected = true;}
      }
    } else {
      for (const row of owned) {row.check.checked = false; row.item.selected = false;}
    }
    loraPicker.refresh();
  }
  function refreshCharacters() {
    jsonEditor?.refresh();
    const selected = characterRows.filter(r => r.check.checked);
    for (const row of characterRows) {
      if (row.check.checked) row.mount().row.hidden = false;
      else if (row.mounted) row.row.hidden = true;
    }
    noCharacters.hidden=selected.length>0;
    noLoras.hidden=!!loraPicker?.rows.some(r=>r.check.checked && !r.check.disabled);
    bindingStatus.textContent = selected.length ? "本次使用：" + selected.map(r =>
      r.card.name + (r.prompt.value.trim() ? "（外观文字）" : "")).join("；") : "尚未选择角色。";
    const cards = selected.map(r => r.card);
    for (const row of selected) row.effectivePrompt = clothingPolicy.resolve(positive.value, row.prompt.value, row.card, cards);
    const combinedText = [positive.value, ...selected.map(r => r.effectivePrompt).filter(v => v.trim())].join("\n\n");
    preview.value = combinedText;
    const contaminated = selected.filter(r => chatCard.test(r.prompt.value));
    languageNotice.hidden = !contaminated.length && !/[\u3400-\u9fff]/u.test(combinedText + negative.value);
    languageNotice.textContent = contaminated.length
      ? contaminated.map(r=>r.card.name).join("、") + "的外观框含性格、对白等聊天设定，会被一起送进绘图模型。请先整理 / 转换英文提示词，再核对服装和人数。"
      : "当前含中文。建议先整理 / 转换英文提示词并核对，尤其是角色外观和当前场景的服装。";
  }
  async function loadPromptCharacters(initial) {
    const previous = new Map(characterRows.map(r => [r.card.name, {
      checked:r.check.checked, prompt:r.promptLoaded ? r.prompt.value : null,
    }]));
    const result = initial?.characters ? initial : await api("/api/characters?compact=1");
    if (!characterBox.isConnected) return;
    characterRows = []; characterList.replaceChildren();
    const savedByName = new Map((saved.characters || []).map(c => [c.name, c]));
    for (const card of result.characters) {
      const stored = savedByName.get(card.name);
      const prior = previous.get(card.name);
      const state = {
        checked: prior ? prior.checked : !!stored && stored.selected !== false,
        defaultPrompt: null,
        promptText: prior?.prompt ?? stored?.source_prompt ?? stored?.prompt ?? null,
      };
      function computeDefaultPrompt() {
        if (state.defaultPrompt !== null) return state.defaultPrompt;
        const visual = ["identity","face","hair","eyes","body","accessories","palette","marks"]
          .map(k => card.profile[k]?.trim()).filter(v => clothingPolicy.known(v));
        const seenVisual = new Set();
        const idParts = clothingPolicy.parts(card.profile.identity || "");
        state.defaultPrompt = visual.flatMap(v => clothingPolicy.parts(v)).filter(v => {
          const key = v.toLowerCase();
          if (seenVisual.has(key)) return false;
          seenVisual.add(key);
          return idParts.includes(v) || !clothingPolicy.has(v);
        }).join(", ");
        return state.defaultPrompt;
      }
      function currentPrompt() {
        if (state.promptText === null) return computeDefaultPrompt();
        const def = computeDefaultPrompt();
        if ((card.source?.drawing_prompt_cleanup?.previous_defaults || []).includes(state.promptText))
          state.promptText = def;
        return state.promptText;
      }
      let mounted = null;
      function mount() {
        if (mounted) return mounted;
        const row = node("div", undefined, "candidate"), label = node("label"), check = node("input");
        check.type = "checkbox"; check.checked = state.checked;
        check.dataset.editKey = "prompt-character-use-" + card.id;
        label.append(check, document.createTextNode(card.name + (card.image_id ? " · 有参考图" : " · 文字角色")));
        const details = node("div", undefined, "prompt-character-details");
        if (card.image_id) {
          const img = node("img"); img.src = "/api/character-image?id=" + encodeURIComponent(card.image_id);
          img.loading = "lazy"; img.decoding = "async";
          img.alt = card.name + "参考图"; img.className = "prompt-character-image"; details.append(img);
        }
        const prompt = labeledInput(details, card.name + " · 外观提示词", true, currentPrompt());
        if (clothingPolicy.known(card.profile.clothing)) details.append(node("small",
          "默认服装（提示词未指定时使用）：" + card.profile.clothing));
        if (stored && stored.revision !== card.revision) details.append(node("p",
          "角色库资料已更新，本次使用最新角色资料；已保留上次的外观提示词，请核对。"));
        prompt.rows = 3; prompt.dataset.editKey = "prompt-character-text-" + card.id;
        const resetPrompt = button("重置外观", () => {
          prompt.value = computeDefaultPrompt();
          state.promptText = prompt.value;
          prompt.dispatchEvent(new Event("input", {bubbles:true}));
        });
        resetPrompt.title = "重置为角色外观资料";
        const detailTools = node("div", undefined, "character-detail-tools");
        detailTools.append(resetPrompt); details.append(detailTools);
        check.addEventListener("change", () => {
          state.checked = check.checked;
          selectCharacterLoras(card.id, check.checked);
          refreshCharacters();
        });
        prompt.addEventListener("input", () => { state.promptText = prompt.value; refreshCharacters(); });
        const appearance = node("details");
        appearance.append(node("summary", "外观文字"), details);
        row.append(label, appearance); characterList.append(row);
        mounted = {row, check, prompt};
        return mounted;
      }
      const entry = {
        card,
        get mounted() { return !!mounted; },
        get promptLoaded() { return mounted !== null || state.promptText !== null; },
        mount,
        get row() { return mount().row; },
        get defaultPrompt() { return computeDefaultPrompt(); },
        check: {
          get checked() { return mounted ? mounted.check.checked : state.checked; },
          set checked(v) { state.checked = !!v; if (mounted) mounted.check.checked = state.checked; },
          click() { mount().check.click(); },
          focus() { mount().check.focus(); },
        },
        prompt: {
          get value() { return mounted ? mounted.prompt.value : currentPrompt(); },
          set value(v) { state.promptText = v; if (mounted) mounted.prompt.value = v; },
          dispatchEvent(ev) { if (mounted) mounted.prompt.dispatchEvent(ev); },
        },
      };
      if (state.checked) mount();
      characterRows.push(entry);
    }
    loraPicker?.updateCharacters(characterRows.map(r=>r.card));
    refreshCharacters();
  }
  characterBox.insertBefore(characterList, preview.parentElement);
  const characterActions = node("div", undefined, "compact-actions");
  characterActions.append(button("管理角色", () =>
    window.openCharacterLibrary(() => loadPromptCharacters().catch(e => notify(e.message, true)))),
    button("刷新角色列表", loadPromptCharacters));
  characterBox.append(bindingStatus, characterActions);
  positive.addEventListener("input", refreshCharacters);
  negative.addEventListener("input", refreshCharacters);
  root.append(characterBox);
  await loadPromptCharacters(initialCharacters);
  if (root.dataset.renderToken !== renderToken) return;
  const conversionStatus = node("p");
  let beforeConversion = null;
  const undoConversion = button("恢复转换前的提示词", () => {
    if (!beforeConversion) return;
    positive.value = beforeConversion.positive; negative.value = beforeConversion.negative;
    for (const row of characterRows) if (beforeConversion.characters.has(row.card.id))
      row.prompt.value = beforeConversion.characters.get(row.card.id);
    beforeConversion = null; undoConversion.hidden = true;
    refreshCharacters(); saveWorkspace(); conversionStatus.textContent = "已恢复转换前的内容。";
  });
  undoConversion.hidden = true;
  const convert = button("整理 / 转换英文提示词 · Flash Next", async () => {
    if(!jsonEditor.valid()) throw Error("请先修正本格 JSON。");
    const selected = characterRows.filter(r => r.check.checked);
    if (!positive.value.trim()) throw Error("请先填写正向提示词");
    if (!data.prompt_translate) throw Error("后台尚未更新，暂不能转换提示词。当前输入已保留。");
    const original = {positive:positive.value, negative:negative.value,
      characters:new Map(selected.map(r=>[r.card.id,r.prompt.value]))};
    const controls = [jsonEditor.group, positive, negative, start, clearInputs, undoConversion, ...characterBox.querySelectorAll("input, textarea, button")];
    const disabled = controls.map(c=>c.disabled);
    controls.forEach(c=>c.disabled=true); convert.disabled=true;
    try {
      await preserve();
      const submitted = await act("prompt_translate", {text:positive.value, negative:negative.value,
        characters:selected.map(r=>({name:r.card.name, revision:r.card.revision, prompt:r.prompt.value}))});
      const result = await waitCreativeJob(submitted.job, conversionStatus);
      if (!convert.isConnected) return;
      positive.value = result.positive; negative.value = result.negative;
      for (const row of selected) row.prompt.value = result.characters.find(c=>c.name===row.card.name).prompt;
      beforeConversion = original; undoConversion.hidden=false;
      refreshCharacters(); saveWorkspace();
      conversionStatus.textContent = "英文绘图内容已填回，聊天设定已移除，场景指定服装优先。原文可恢复，请核对后开始画图。语言模型已卸载。";
    } finally {
      controls.forEach((c,i)=>c.disabled=disabled[i]); convert.disabled=false;
      refreshCharacters();
    }
  });
  root.append(languageNotice, convert, undoConversion, conversionStatus);
  const fields = node("div", undefined, "sample-fields");
  function field(name, key) {
    const label = node("label", name), input = node("select");
    input.setAttribute("aria-label", name); input.dataset.editKey = "prompt-" + key;
    label.append(input); fields.append(label); return input;
  }
  const model = field("绘图模型", "model");
  for (const name of data.comfy.checkpoints) {
    const opt = node("option", data.diffusion_models?.[name]?.label || name); opt.value = name; model.append(opt);
  }
  const selectableModels = [...model.options].filter(o => !o.disabled);
  model.value = [saved.checkpoint, data.preloaded_checkpoint, data.comfy.configured]
    .find(name => selectableModels.some(o => o.value === name))
    || selectableModels[0]?.value || "";
  if(draft&&saved.checkpoint){
    if(![...model.options].some(o=>o.value===saved.checkpoint)){
      const missing=node("option","上次使用的模型 · 当前不可用");missing.value=saved.checkpoint;missing.disabled=true;model.append(missing);
    }
    model.value=saved.checkpoint;
  }
  const count = field("生成张数", "count");
  for (let n=1;n<=16;n++) {const o=node("option", n+" 张");o.value=String(n);count.append(o);}
  count.value = String(saved.count || 1);
  const seedLabel = node("label", "种子（留空随机）"), seed = node("input");
  seed.type = "number"; seed.min = "0"; seed.max = "9007199254740991"; seed.step = "1";
  seed.placeholder = "随机"; seed.setAttribute("aria-label", "种子");
  seed.dataset.editKey = "prompt-seed";
  seed.value = saved.seed != null && saved.seed !== "" ? String(saved.seed) : "";
  seedLabel.title = "留空则每次随机生成；填写整数后，多张出图会从该种子起按序递增";
  seedLabel.append(seed); fields.append(seedLabel);
  function parseSeed() {
    const raw = seed.value.trim();
    if (!raw) return null;
    if (!/^\d+$/.test(raw)) throw Error("种子须为 0 至 9007199254740991 的整数");
    const value = Number(raw);
    if (!Number.isSafeInteger(value) || value < 0) throw Error("种子须为 0 至 9007199254740991 的整数");
    return value;
  }
  const taskNameLabel=node("label","排队任务名称（可选）"),taskName=node("input");taskName.maxLength=80;taskName.placeholder="例如：城市夜景 · 第二批";taskName.setAttribute("aria-label","排队任务名称");taskNameLabel.append(taskName);taskNameLabel.dataset.queueNameField="true";taskNameLabel.hidden=!window.lastStudioStatus?.queue?.can_rename;
  taskName.value=saved.task_name||"";
  const sizeFields=node("div",undefined,"image-size-fields");fields.append(sizeFields);
  const imageSize = createImageSizeControls(sizeFields, saved, {storyboard:board});
  const loraBox=node("section",undefined,"candidate");
  const matteLabel=node("label"), matte=node("input");
  matte.type="checkbox";
  matte.checked=saved.delta_matte_skin ?? data.comfy.delta_matte_enabled ?? true;
  matte.dataset.editKey="prompt-delta-matte";
  matteLabel.append(matte,document.createTextNode("柔和皮肤 / 去高光（DeltaMix v6）"));
  matteLabel.title="生成时自动附加哑光皮肤与去高光提示词；取消勾选恢复原提示词。";
  const consistencyLabel=node("label"), consistency=node("input");
  consistency.type="checkbox";
  consistency.checked=saved.consistency_lora ?? data.comfy.consistency_enabled ?? false;
  consistency.dataset.editKey="prompt-consistency-lora";
  consistencyLabel.append(consistency,document.createTextNode("自动加载一致性 LoRA"));
  const consistencyNote=node("small","此开关只加载 Anima Edit v2，不会自动启用角色参考图。当前提示词出图使用文字与 LoRA；没有图像参考时不锁定具体角色外观。");
  loraBox.append(node("h3","已选 LoRA · 权重"));
  loraBox.title = "选中角色时自动勾选对应 LoRA，可手动取消；刷新页面或选择其他角色不会重新勾选。全图风格手动勾选。每张最多 5 个，强度 0–2。";
  const savedLoraByFile = new Map((saved.loras || []).map(l => [resolveLoraFile(l.file, data), l]));
  const items=(data.comfy.loras||[]).map(file=>{
    const label=data.lora_labels?.[file]||{},stored=savedLoraByFile.get(file);
    return {file,name:label.name||stored?.label||file.split("/").pop().replace(/\.safetensors$/i,""),
      ...("character_id" in label?{character_id:label.character_id}:"character_id" in (stored||{})?{character_id:stored.character_id}:{}),
      character:label.character||stored?.character||"",selected:!!stored,weight:stored?.weight??window.defaultLoraWeight(file,data,0.7),
      trigger_words:label.trigger_words||"",confirmed:!!label.confirmed};
  });
  loraPicker=createLoraPicker(loraBox,data,items,characterRows.map(r=>r.card),()=>{refreshCharacters();},"prompt-lora-",true,(row,details)=>{
    details.append(button("添加触发词",()=>{
      const words=row.item.trigger_words?.trim();if(!words)throw Error("请先在编辑中填写触发词");
      const character=characterRows.find(r=>r.card.id===row.owner.value);
      if(row.owner.value&&!character?.check.checked)throw Error("请先勾选对应角色");
      const input=character?character.prompt:positive;
      if(!input.value.toLowerCase().includes(words.toLowerCase()))input.value+=(input.value.trim()?", ":"")+words;
      input.dispatchEvent(new Event("input",{bubbles:true}));refreshCharacters();
    }));
  });
  const loraRows=loraPicker.rows;
  const refreshLoras=()=>loraPicker.refresh();
  function modelCompatibility() {
    animaActive = isDiffusion(model.value);
    const krea = data.diffusion_models?.[model.value]?.family === "krea2";
    const moss = data.diffusion_models?.[model.value]?.family === "moss_image";
    imageSize.setAlignment(moss?16:8);
    consistency.disabled = model.value !== "animaDeltaMix38BQwen0_deltaMixV60.safetensors";
    consistencyLabel.hidden = consistencyNote.hidden = consistency.disabled;
    matte.disabled = consistency.disabled;
    matteLabel.hidden = matte.disabled;
    negative.parentElement.title = krea ? "填写后启用实验性负面引导（CFG 1.2），留空使用 Turbo 默认模式。" : "";
    modelDescription.textContent = "输入画面描述，选择角色与当前模型兼容的 LoRA。人物位置和互动请写入提示词。";
    if (krea) modelDescription.textContent = "Krea2 Turbo · 默认 8 步。填写负面词后启用实验性 CFG 1.2，LoRA 仅使用 Krea2 架构。";
    if (moss) modelDescription.textContent = "MOSS-Image SFT · BF16 高精度，默认 25 步。支持正负提示词和画面文字描述，LoRA 仅使用 MOSS-Image 架构。";
    loraPicker.setCheckpoint(model.value);
    refreshCharacters();refreshLoras();
  }
  model.addEventListener("change", modelCompatibility);
  modelCompatibility();
  window.refreshPromptControls=()=>{refreshCharacters();refreshLoras();};
  async function submitPrompt(queued=false) {
    if(!jsonEditor.valid()) throw Error("请先修正本格 JSON。");
    if(queued && window.studioQueueAvailable===false)throw Error("夜间列表需要新版后台。当前生成结束后更新工作室即可使用，已填内容保留。");
    if (!positive.value.trim()) throw Error("请先填写正向提示词");
    if (!model.value || model.selectedOptions[0]?.disabled) throw Error("请选择已下载完整的模型");
    const loras=loraRows.filter(r=>r.check.checked && !r.check.disabled).map(r=>({file:r.file,weight:Number(r.weight.value),character_id:r.owner.value,character:r.item.character}));
    if (loras.length>5) throw Error("最多同时选择 5 个 LoRA，请减少勾选");
    if (loras.some(l=>!Number.isFinite(l.weight)||l.weight<0||l.weight>2)) throw Error("LoRA 强度须为 0–2");
    const characters = characterRows.filter(r=>r.check.checked).map(r=>({
      name:r.card.name, revision:r.card.revision, prompt:r.prompt.value,
    }));
    const contaminated = characters.filter(c=>chatCard.test(c.prompt));
    if (contaminated.length) throw Error(contaminated.map(c=>c.name).join("、") + "的外观提示词含聊天设定，请先整理 / 转换英文提示词");
    if (characters.length && !data.prompt_characters) throw Error("请更新工作室后台后使用角色选择。当前输入已保留。");
    await loraPicker.flush();
    await preserve();
    if(board && !panelChoice.value)throw Error("请核对提示词的页格编号。");
    const seedValue = parseSeed();
    const request = {text:positive.value, negative:negative.value,task_name:queued?taskName.value:undefined,
      ...(board?{target:panelChoice.value,project:thisProject}:queued?{}:{project:promptSessionId}),
      checkpoint:model.value, count:Number(count.value), ...imageSize.read(), loras,
      characters,
      ...(seedValue !== null ? {seed:seedValue} : {}),
      consistency_lora:consistency.checked && !consistency.disabled,
      delta_matte_skin:matte.checked};
    const created = board ? await act(queued?"queue_board_panel":"draw_board_panel", request)
      : queued ? await act("queue_prompt", request) : await startPromptDraw(request);
    if(queued){
      taskName.value="";
      saveWorkspace();
      queuedReceipt.hidden=false;queuedReceiptText.textContent=`已加入：${count.value} 张 · ${positive.value.trim().slice(0,32)}。修改上方内容可继续添加。`;
      notify("已加入任务列表，结果会自动保存。可以修改提示词继续添加下一项。");await refreshStatus();await window.afterQueuedTask?.(created);return;
    }
    if(!board){
      promptSessionId = created.project;
      if (!positive.isConnected || root.dataset.renderToken !== renderToken || stage !== "prompt") return;
      const switching = thisProject !== promptSessionId;
      pid = thisProject = promptSessionId;
      snap = created.snapshot;
      project = snap.project;
      newMode = false;
      if (switching) {
        workspaceState = {stage:"prompt", target:"", views:{}};
        $("projects").value = "";
        renderStageHeader(project);
        draftKey = "studio-prompt-draft-new";
        draftStored = false;
        window.savePromptDraft();
        sessionHint.textContent = sessionDescription;
        await refresh();
      }
    }
    notify("已开始出图，进度在上方显示；完成后图片会出现在右侧。");
  }
  const start = button("开始画图",()=>submitPrompt(),"primary");
  const enqueue = button("加入任务列表 · 稍后绘制",()=>submitPrompt(true));
  start.dataset.startJob = "true";
  start.dataset.unavailable = String(!model.value);
  start.disabled = !model.value || !!window.activeStudioJob;
  root.append(fields, consistencyLabel, consistencyNote, loraBox, node("small", "一次 1–16 张。LoRA 不自动改写原提示词；需要触发词请填写在对应角色的外观提示词中。不自动审核，中文不会自动翻译。"), start);
  const galleryPane = node("section", undefined, "prompt-gallery workspace-pane"); root.append(galleryPane);
  const gallery = node("div", undefined, "board-preview-body");
  let activeImage = 0;
  let seenImages=0;
  let previewPanel, result;
  let galleryRevision = 0;
  let secondPassState = null; // {baseId, owner, viewId, pendingCount, pendingPrevIds}
  const galleryFooter = node("div", undefined, "board-panel-navigation gallery-footer");
  galleryFooter.setAttribute("aria-label", board ? "切换画格与二采" : "二采操作");
  const openSecondPassBtn = button("二采", () => {
    const target = currentGalleryTarget();
    if (!target) throw Error("还没有可二采的图片");
    enterSecondPass(target.base, target.owner);
  }, "small primary second-pass-trigger");
  openSecondPassBtn.hidden = true;
  if(board){
    function stepButton(label,step){
      const control=node("button",label,"small");
      control.type="button";
      control.onclick=()=>{
        panelChoice.selectedIndex+=step;
        panelChoice.dispatchEvent(new Event("change",{bubbles:true}));
      };
      return control;
    }
    const back=stepButton("上一格",-1), forward=stepButton("下一格",1);
    galleryFooter.append(back,forward,openSecondPassBtn);
    syncPreview=()=>{
      const index=panelChoice.selectedIndex;
      back.disabled=index<=0;
      forward.disabled=index>=panels.length-1;
      if(secondPassState) exitSecondPass();
      if(result && previewPanel!==panelChoice.value)renderGallery();
    };
  } else {
    galleryFooter.append(openSecondPassBtn);
  }
  galleryPane.append(gallery, galleryFooter);
  let lastRenderedImages = [];
  function currentGalleryTarget() {
    if (!lastRenderedImages.length) return null;
    const entry = lastRenderedImages[Math.min(activeImage, lastRenderedImages.length - 1)];
    return entry ? {base: entry.base, owner: entry.base.batch.projectId || thisProject} : null;
  }
  const refresh = async () => {
    if (!gallery.isConnected) return;
    const revision = ++galleryRevision;
    let latest=null;
    if (legacyPromptRounds.has(thisProject)) {
      const groups = await Promise.all([...legacyPromptRounds].map(async ([id, round]) => {
        if (id === thisProject || !round.batches) {
          const {images} = await api("/api/images?id=" + encodeURIComponent(id));
          if (revision !== galleryRevision) return [];
          round.batches = images.batches.map(batch => ({...batch, projectId:id}));
          round.second_pass_applied = images.second_pass_applied || {};
        }
        return round.batches;
      }));
      const mergedApplied = Object.assign({}, ...[...legacyPromptRounds.values()].map(r => r.second_pass_applied || {}));
      latest = {images:{batches:groups.flat(), selections:{}, second_pass_applied:mergedApplied}};
    } else if (thisProject) latest = await api("/api/images?id=" + encodeURIComponent(thisProject));
    if (!gallery.isConnected || revision !== galleryRevision) return;
    result=latest;
    renderGallery();
  };
  function renderGallery(){
    const panelId=panelChoice.value;
    gallery.replaceChildren(node("h2", secondPassState ? "二次采样预览" : (board && panelId ? "图片预览 · "+panelId : "图片预览")));
    let images = [];
    lastRenderedImages = [];
    const copyTools = node("div", undefined, "gallery-copy-tools");
    const copyCurrent = button("复制当前图片", () => copyImages(false), "small");
    const copyAll = button("复制所有图片", () => copyImages(true), "small");
    const clearImages = button("一键清除所有图片", async () => {
      if (window.activeStudioJob) throw Error("请等待当前任务结束，或取消任务后再清除图片");
      ++galleryRevision;
      clearImages.dataset.unavailable = "true";
      copyCurrent.disabled = copyAll.disabled = true;
      try {
        let cleared=0;
        const owners=legacyPromptRounds.has(thisProject) ? [...legacyPromptRounds.keys()] : [thisProject];
        for (const owner of owners) {
          const result=await api("/api/action", {action:"clear_prompt_images", project:owner});
          cleared+=result.cleared;
          if (legacyPromptRounds.has(owner)) legacyPromptRounds.get(owner).batches=null;
          if (owner !== thisProject) {
            await act("release_prompt", {project:owner});
            legacyPromptRounds.delete(owner);
          }
        }
        if (secondPassState) exitSecondPass(true);
        activeImage = 0;
        await refresh();
        notify(`已清除 ${cleared} 张图片，可以继续生成新图片。`);
      } catch (error) {
        await refresh();
        throw error;
      }
    }, "small danger");
    clearImages.title = "清除本次全部生成图片，保留提示词、角色、LoRA 和出图设置";
    clearImages.dataset.startJob = "true";
    clearImages.dataset.unavailable = "true";
    clearImages.disabled = true;
    const multipleRounds = legacyPromptRounds.has(thisProject) && legacyPromptRounds.size > 1;
    const copyAllLabel=board ? "复制本格全部图片" : multipleRounds ? "复制本轮图片" : "复制所有图片";
    copyAll.textContent=copyAllLabel;
    copyAll.setAttribute("aria-label", copyAllLabel);
    copyAll.title = copyAllLabel+"，可粘贴到文件夹或支持多图粘贴的软件";
    copyCurrent.disabled = copyAll.disabled = true;
    copyTools.append(copyCurrent, copyAll);if(!board && !secondPassState)copyTools.append(clearImages);
    if(board && !secondPassState)copyTools.append(button("整图修改",async()=>{await preserve();stage="finished";target="";await renderStage();}));
    gallery.append(copyTools);
    async function copyImages(all) {
      if (!images.length) throw Error("还没有可复制的图片");
      const current = images[activeImage];
      const selected = all ? images.filter(image => image.batch.projectId === current.batch.projectId) : [current];
      copyCurrent.disabled = copyAll.disabled = true;
      try {
        const copied = await api("/api/action", {action:"copy_prompt_images", project:current.batch.projectId || thisProject,
          images:selected.map(image => image.id)});
        notify(all ? `已复制 ${copied.copied} 张原图，可粘贴到文件夹或支持多图粘贴的软件。` : "已复制当前图片，可直接粘贴。");
      } finally {
        copyCurrent.disabled = copyAll.disabled = !images.length;
      }
    }
    if (!thisProject) {
      openSecondPassBtn.hidden = true;
      const empty=node("div",undefined,"gallery-empty");
      empty.append(node("strong","你的第一张图，从一句描述开始"),node("p","写清楚：谁在什么地方，正在做什么。\n选好模型与张数，再点击开始画图。"));
      const examples=node("div",undefined,"prompt-examples");examples.append(node("small","没有灵感？试一个简单示例"));
      for(const [label,text] of [["宁静的风景","A quiet lakeside at sunset, distant mountains, golden reflections on the water, wide landscape, watercolor illustration"],
        ["单人肖像","One adult traveler with short brown hair, wearing a blue coat, standing by a train window, soft afternoon light, waist-up portrait, anime illustration"],
        ["双人互动","Two adults sitting across a small cafe table, the person on the left passes a letter to the person on the right, medium shot, warm indoor lighting, anime illustration"]]){
        examples.append(button(label,()=>{
          if(positive.value.trim()){notify("先清空正向提示词，再选择示例；清空后可以撤销。");return;}
          positive.value=text;refreshCharacters();saveWorkspace();positive.focus();notify("示例已填入，可以自由修改；还没有开始生成。");
        }));
      }
      empty.append(examples,node("small","立即出图为临时预览；加入列表会保存结果。"));gallery.append(empty);return;
    }
    const allBatches = board ? result.images.batches.filter(b=>b.panel_id===panelId) : result.images.batches;
    const appliedMap = result.images.second_pass_applied || {};
    const secondPassByBase = new Map();
    const allImageById = new Map();
    for (const b of allBatches) {
      for (const im of b.images) {
        const wrapped = {...im, batch: b};
        allImageById.set(im.id, wrapped);
        if (b.kind === "second_pass" && b.source_image_id) {
          if (!secondPassByBase.has(b.source_image_id)) secondPassByBase.set(b.source_image_id, []);
          secondPassByBase.get(b.source_image_id).push(wrapped);
        }
      }
    }
    const baseImages = allBatches
      .filter(b => b.kind !== "second_pass")
      .flatMap(b => b.images.map(image => ({...image, batch: b})));
    const imageUrl = image => {
      const [bundle, file]=image.file.split("/");
      return `/api/image?id=${encodeURIComponent(image.batch.projectId || thisProject)}&bundle=${encodeURIComponent(bundle)}&file=${encodeURIComponent(file)}`;
    };

    // --- Second-pass preview mode (right pane stays in place) ---
    if (secondPassState) {
      openSecondPassBtn.hidden = true;
      const base = allImageById.get(secondPassState.baseId);
      if (!base) { exitSecondPass(true); renderGallery(); return; }
      const candidates = secondPassByBase.get(base.id) || [];
      const appliedId = appliedMap[base.id] || null;
      if (secondPassState.pendingPrevIds) {
        const fresh = candidates.filter(c => !secondPassState.pendingPrevIds.has(c.id));
        if (fresh.length) {
          secondPassState.viewId = appliedId && fresh.some(c => c.id === appliedId) ? appliedId : fresh[0].id;
          const expected = secondPassState.pendingCount || 1;
          secondPassState.pendingPrevIds = null;
          secondPassState.pendingCount = 0;
          if (expected === 1 && appliedId) {
            notify("二采完成，已自动应用；可点击「取消应用」恢复原图。");
          } else if (fresh.length > 1) {
            notify(`已生成 ${fresh.length} 张二采候选，请在下方面板挑选并点击「应用这张二采图」。`);
          }
        }
      }
      const spList = [base, ...candidates];
      images = spList;
      let spIdx = spList.findIndex(x => x.id === secondPassState.viewId);
      if (spIdx < 0) spIdx = appliedId ? Math.max(0, spList.findIndex(x => x.id === appliedId)) : 0;
      activeImage = spIdx;
      copyCurrent.disabled = copyAll.disabled = false;
      copyAll.textContent = `复制全部（${spList.length}）`;
      const viewer = node("div", undefined, "gallery-viewer");
      const thumbs = node("div", undefined, "gallery-thumbnails second-pass-thumbs");
      const spOwner = base.batch.projectId || thisProject;
      function renderSpView() {
        const current = spList[activeImage];
        secondPassState.viewId = current.id;
        const isBase = current.id === base.id;
        const isApplied = !isBase && appliedId === current.id;
        const card = node("article", undefined, "gallery-image");
        const url = imageUrl(current);
        const link = node("a"), img = node("img");
        img.src = url; img.alt = isBase ? "一采原图" : "二采图 " + current.id;
        link.href = url; link.target = "_blank"; link.append(img);
        const open = node("a", "下载原图"); open.href = url; open.download = (isBase ? "原图-" : "二采-") + current.id + ".png";
        const tools = node("div", undefined, "compact-actions gallery-image-actions");
        tools.append(open);
        if (isBase) {
          tools.append(node("span", appliedId ? "一采原图（当前已应用二采）" : "一采原图（当前使用中）", "sp-badge"));
          if (appliedId) {
            tools.append(button("取消应用，恢复原图", async () => {
              await api("/api/action", {action:"unapply_second_pass", project:spOwner, source_image_id:base.id});
              if (legacyPromptRounds.has(spOwner)) legacyPromptRounds.get(spOwner).batches = null;
              await refresh();
              notify("已取消应用二采，恢复为一采原图。");
            }, "small"));
          }
        } else if (isApplied) {
          tools.append(node("span", "已应用", "sp-badge sp-badge-active"));
          tools.append(button("取消应用", async () => {
            await api("/api/action", {action:"unapply_second_pass", project:spOwner, source_image_id:base.id});
            if (legacyPromptRounds.has(spOwner)) legacyPromptRounds.get(spOwner).batches = null;
            await refresh();
            notify("已取消应用，恢复为一采原图。");
          }, "small"));
        } else {
          tools.append(button("应用这张二采图", async () => {
            await api("/api/action", {action:"apply_second_pass", project:spOwner, source_image_id:base.id, image_id:current.id});
            if (legacyPromptRounds.has(spOwner)) legacyPromptRounds.get(spOwner).batches = null;
            await refresh();
            notify("已应用这张二采图。");
          }, "primary"));
        }
        const info = node("details", undefined, "gallery-info");
        const spMeta = current.batch.second_pass;
        const modeLabel = spMeta?.mode === "tiled" ? `分块二采（${spMeta.tiles || ""}块）` : "整图二采";
        const postLabel = spMeta?.post_upscale > 1 ? ` · 后置超分 ${spMeta.post_upscale}×` : "";
        const metaLine = spMeta
          ? `${modeLabel} · 模型：${current.batch.checkpoint} · 缩放：${spMeta.scale}×${postLabel} · 重绘：${spMeta.denoise} · 步数：${spMeta.steps} · 种子：${current.seed}` + (current.width ? ` · ${current.width}×${current.height}` : "")
          : `一采原图 · 模型：${current.batch.checkpoint} · 种子：${current.seed}`;
        info.append(node("summary", isBase ? "原图详情" : "二采详情"), node("p", metaLine));
        if (current.batch.loras?.length) info.append(node("p", "LoRA：" + current.batch.loras.map(l => `${l.label || l.file} × ${l.weight}`).join("、")));
        card.append(link, tools, info);
        viewer.replaceChildren(card);
        thumbs.replaceChildren();
        spList.forEach((item, idx) => {
          const itemIsBase = item.id === base.id;
          const itemApplied = !itemIsBase && appliedId === item.id;
          const thumb = button("", () => { activeImage = idx; renderSpView(); }, "gallery-thumb" + (itemApplied ? " sp-thumb-applied" : ""));
          const caption = itemIsBase ? "原图" : (itemApplied ? "已应用" : `二采 ${idx}`);
          thumb.setAttribute("aria-label", caption);
          thumb.setAttribute("aria-pressed", String(activeImage === idx));
          const im = node("img"); im.src = imageUrl(item); im.alt = caption;
          thumb.append(im, node("span", caption, "sp-thumb-tag"));
          thumbs.append(thumb);
        });
      }
      gallery.append(viewer);
      if (spList.length > 1) gallery.append(thumbs);
      renderSpView();
      return;
    }

    // --- Normal gallery mode ---
    const entries = baseImages.map(base => {
      const appliedId = appliedMap[base.id];
      const applied = appliedId ? allImageById.get(appliedId) : null;
      return {base, display: applied || base, applied, candidates: secondPassByBase.get(base.id) || []};
    });
    lastRenderedImages = entries;
    images = entries.map(entry => entry.display);
    if((board && previewPanel!==panelId) || entries.length>seenImages)activeImage=Math.max(0,entries.length-1);
    previewPanel=panelId;
    seenImages=entries.length;
    if (!entries.length) {
      openSecondPassBtn.hidden = true;
      gallery.append(node("div", board ? (panelId ? "本格还没有候选图" : "请选择画格") : window.activeStudioJob
        ? "等待图片生成…\n生成进度显示在顶部"
        : project?.transient===false?"这项排队作品还没有图片\n打开「任务列表」查看等待或执行状态":"暂无生成图片\n点击“开始画图”生成新图片", "gallery-empty"));
      return;
    }
    openSecondPassBtn.hidden = false;
    clearImages.dataset.unavailable = "false";
    clearImages.disabled = !!window.activeStudioJob;
    copyCurrent.disabled = copyAll.disabled = false;
    copyAll.textContent = `${copyAllLabel}（${images.length}）`;
    activeImage = Math.min(activeImage, entries.length - 1);
    const viewer = node("div", undefined, "gallery-viewer");
    const navigation = node("div", undefined, "gallery-navigation");
    const imageNumber = node("small");
    const previous = button("‹ 上一张", () => {activeImage--; showImage();}, "small");
    const next = button("下一张 ›", () => {activeImage++; showImage();}, "small");
    navigation.append(previous, imageNumber, next);
    const thumbnails = node("div", undefined, "gallery-thumbnails");
    function showImage() {
      const entry = entries[activeImage];
      const image = entry.display;
      const owner = entry.base.batch.projectId || thisProject;
      if (multipleRounds) copyAll.textContent = `${copyAllLabel}（${images.filter(i=>i.batch.projectId===owner).length}）`;
      const card=node("article",undefined,"gallery-image");
      const url = imageUrl(image);
      const link=node("a"), img=node("img");img.src=url;img.alt="提示词出图 "+image.id;
      link.href=url;link.target="_blank";link.append(img);
      const open=node("a","下载原图");open.href=url;open.download=(entry.applied?"二采-":"提示词出图-")+image.id+".png";
      const info = node("details", undefined, "gallery-info");
      const spMeta = image.batch.second_pass;
      const modeTag = spMeta?.mode === "tiled" ? `分块二采（${spMeta.tiles || ""}块）` : "整图二采";
      const postTag = spMeta?.post_upscale > 1 ? ` · 后置超分 ${spMeta.post_upscale}×` : "";
      info.append(node("summary", "生成详情"), node("p", spMeta
        ? `${modeTag} · 模型：${image.batch.checkpoint} · 缩放：${spMeta.scale}×${postTag} · 重绘：${spMeta.denoise} · 步数：${spMeta.steps} · 种子：${image.seed}` + (image.width ? ` · ${image.width}×${image.height}` : "")
        : `模型：${image.batch.checkpoint} · 种子：${image.seed}`));
      const tools=node("div",undefined,"compact-actions gallery-image-actions");
      tools.append(open);
      if (entry.applied) {
        tools.append(node("span", "已应用二采", "sp-badge sp-badge-active"));
        tools.append(button("取消应用", async () => {
          await api("/api/action", {action:"unapply_second_pass", project:owner, source_image_id:entry.base.id});
          if (legacyPromptRounds.has(owner)) legacyPromptRounds.get(owner).batches = null;
          await refresh();
          notify("已取消应用二采，恢复为一采原图。");
        }, "small"));
        const origLink = node("a", "查看一采原图");
        origLink.href = imageUrl(entry.base); origLink.target = "_blank";
        info.append(origLink);
      } else if (entry.candidates.length) {
        tools.append(node("span", `${entry.candidates.length} 张二采候选待选`, "sp-badge"));
      }
      card.append(link,tools);
      if(board){
        const key=entry.base.batch.panel_id, selected=result.images.selections[key]?.image.id===image.id;
        if(selected){open.download=key+".png";open.textContent="下载 "+key+" 原图";}
        const adopt=button(selected?"已采用 · "+key:"采用到 "+key,async()=>{
          await api("/api/action",{action:"select_image",project:thisProject,target:key,image:image.id,compact:true});
          await refresh();
        },"primary");
        adopt.disabled=selected;tools.append(adopt);
        if(selected)tools.append(button("取消采用",async()=>{
          await api("/api/action",{action:"unselect_board_image",project:thisProject,target:key,image:image.id});await refresh();
        }));
      }
      card.append(info);
      if (image.review?.verdict && image.review.verdict !== "unreviewed") {
        const verdict = {pass:"通过",reject:"未通过",uncertain:"无法确认",pending:"核对中"}[image.review.verdict] || "待核对";
        card.append(node("p", "AI 核对：" + verdict + "（供参考）",
          ["reject","uncertain"].includes(image.review.verdict) ? "warning" : ""));
        if (image.review.reason) info.append(node("p", image.review.reason));
      }
      if(image.batch.loras?.length) info.append(node("p","LoRA："+image.batch.loras.map(l=>`${l.label||l.file} × ${l.weight}`).join("、")));
      if(image.batch.consistency) info.append(node("p",image.batch.consistency.enabled
        ? `一致性：Anima Edit v2 × ${image.batch.consistency.weight} · ${image.batch.consistency.reference ? image.batch.consistency.character+"原图" : "无图像参考（"+image.batch.consistency.reference_reason+"）"}`
        : `一致性未启用：${image.batch.consistency.reason}`));
      viewer.replaceChildren(card);
      imageNumber.textContent = `${activeImage + 1} / ${entries.length} 张`;
      previous.disabled = activeImage === 0; next.disabled = activeImage === entries.length - 1;
      previous.dataset.unavailable=String(previous.disabled);next.dataset.unavailable=String(next.disabled);
      const first = Math.floor(activeImage / 8) * 8;
      thumbnails.replaceChildren();
      entries.slice(first, first + 8).forEach((item, offset) => {
        const thumb = button("", () => {activeImage = first + offset; showImage();}, "gallery-thumb" + (item.applied ? " sp-thumb-applied" : ""));
        thumb.setAttribute("aria-label", "查看第 " + (first + offset + 1) + " 张图片");
        thumb.setAttribute("aria-pressed", String(activeImage === first + offset));
        const img = node("img"); img.src = imageUrl(item.display); img.alt = "第 " + (first + offset + 1) + " 张";
        thumb.append(img);
        if (item.applied) thumb.append(node("span", "二采", "sp-thumb-tag"));
        thumbnails.append(thumb);
      });
    }
    gallery.append(viewer);
    if(entries.length>1)gallery.append(navigation,thumbnails);
    showImage();
  }
  const inputPane = node("section", undefined, "prompt-input workspace-pane");
  const introduction = root.children[1];
  inputPane.append(root.firstElementChild, introduction, positive.parentElement, negative.parentElement, languageNotice);
  jsonEditor=promptJsonEditor(positive,negative,board?panelChoice.value:"P001-K01",{...saved.json_editor},()=>{
    if(board && jsonEditor.state.enabled && jsonEditor.valid()){panelChoice.value=boardPanelId(jsonEditor.state.id || panelChoice.value);describePanel();}
    refreshCharacters();window.savePromptDraft?.();
  },false,board,wardrobeText=>{
    const selected=characterRows.filter(row=>row.check.checked);
    const cards=selected.map(row=>row.card);
    return selected.map(row=>({name:row.card.name,appearance:wardrobeText
      ? clothingPolicy.resolve(wardrobeText,row.prompt.value,row.card,cards) : ""}));
  });
  if(board){
    if(jsonEditor.state.enabled && jsonEditor.state.id)panelChoice.value=boardPanelId(jsonEditor.state.id);
    describePanel();
    inputPane.insertBefore(panelLabel,jsonEditor.group);
    inputPane.insertBefore(button("复制本格给 Qwen", async () => {
      const target = panelChoice.value;
      const context = await api("/api/prompt-exchange?" + new URLSearchParams({id: pid, panel: target}));
      const instruction = "为 current_panel 写独立的 Anima 英文提示词。按当前动作和入格、格末状态写出可见衣着及道具，遵守故事和排除项。只返回包含 panel_id、positive、negative 的 JSON。";
      await navigator.clipboard.writeText(instruction + "\n" + JSON.stringify(context));
      notify(target + " 的分镜和状态已复制");
    }, "small"), jsonEditor.group);
    panelChoice.onchange=()=>{describePanel();jsonEditor.setId(panelChoice.value);window.savePromptDraft?.();};
  }
  const conversionTools = node("div", undefined, "compact-actions");
  let clearedInputs=null;
  const undoClear=button("撤销清空",()=>{
    if(!clearedInputs)return;
    positive.value=clearedInputs.positive;negative.value=clearedInputs.negative;
    characterRows.forEach(r=>{r.check.checked=clearedInputs.characters.get(r.card.name)===true;});
    loraRows.forEach(r=>{r.check.checked=!r.check.disabled&&clearedInputs.loras.get(r.file)===true;r.item.selected=r.check.checked;});
    clearedInputs=null;undoClear.hidden=true;refreshCharacters();refreshLoras();saveWorkspace();notify("已恢复刚才的提示词、角色与 LoRA 选择。");
  });undoClear.hidden=true;
  const clearInputs = button("一键清除角色、LoRA 和正负提示词", () => {
    clearedInputs={positive:positive.value,negative:negative.value,characters:new Map(characterRows.map(r=>[r.card.name,r.check.checked])),loras:new Map(loraRows.map(r=>[r.file,r.check.checked]))};undoClear.hidden=false;
    positive.value = ""; negative.value = "";
    for(const row of characterRows) row.check.checked = false;
    for(const row of loraRows) {row.check.checked = false; row.item.selected = false;}
    beforeConversion = null; undoConversion.hidden = true; conversionStatus.textContent = "";
    refreshCharacters(); refreshLoras(); saveWorkspace(); positive.focus();
    notify("已清除正负提示词，并取消本次角色和 LoRA 选择。");
  });
  clearInputs.title = "清空正向、负面提示词，并取消本次全部角色和 LoRA 勾选";
  conversionTools.append(convert, undoConversion);
  const topActions = node("div", undefined, "prompt-top-actions");
  const sessionDescription="本次打开应用期间，连续出图会累加在右侧图库。";
  const sessionHint=node("small", board?"粘贴带页格编号的提示词可自动定位。候选图保存在此作品中。":project?.quick_sample && !project.transient ? "已保存的排队作品 · 图片可随时回来查看" : sessionDescription);
  topActions.append(clearInputs,undoClear,sessionHint);
  const draftStatus=node("small",draft?.persisted===false?"已恢复本页暂存；浏览器存储不可用，关闭或刷新前请复制提示词。":draft?"已恢复上次输入 · 草稿保存在当前浏览器":"提示词和设置会自动保存在当前浏览器","prompt-draft-status");
  draftStatus.classList.toggle("warning",draft?.persisted===false);
  draftStatus.setAttribute("role","status");topActions.append(draftStatus);
  inputPane.prepend(topActions);
  inputPane.append(conversionTools, conversionStatus, fields, matteLabel, consistencyLabel, consistencyNote);
  const finalPreview = node("details", undefined, "prompt-preview");
  finalPreview.open = false;
  finalPreview.append(node("summary", "最终提示词预览 / 出图说明"), preview.parentElement,
    node("small", "一次 1–16 张。LoRA 触发词需填入提示词，不自动审核或翻译。"));
  inputPane.append(finalPreview,taskNameLabel);
  const formScroll=node("div",undefined,"prompt-form-scroll");
  formScroll.append(...inputPane.childNodes);
  const submitActions=node("div",undefined,"prompt-submit-actions");submitActions.append(start,enqueue);
  const queuedReceipt=node("div",undefined,"prompt-queue-receipt"),queuedReceiptText=node("small");queuedReceipt.hidden=true;queuedReceipt.setAttribute("role","status");
  queuedReceipt.append(queuedReceiptText,button("查看列表 / 安排开始",()=>window.openStudioQueue()));submitActions.append(queuedReceipt);
  const submitSummary=node("small");submitActions.prepend(submitSummary);
  const updateSubmitSummary=()=>{
    const missingModel=!model.value||!!model.selectedOptions[0]?.disabled,emptyPrompt=!positive.value.trim()||!jsonEditor.valid()||(board&&!panelChoice.value);
    let sizeText,invalidSize=false;try{sizeText=imageSize.summary();}catch(error){sizeText=error.message;invalidSize=true;}
    let seedText="",invalidSeed=false;try{const s=parseSeed();if(s!==null)seedText=" · 种子 "+s;}catch(error){seedText=" · "+error.message;invalidSeed=true;}
    submitSummary.textContent=`${model.selectedOptions[0]?.textContent||"请选择模型"} · ${count.value} 张 · ${sizeText}${seedText}`+(missingModel?" · 请选择已下载完整的模型":emptyPrompt?" · 先写下想画的画面":"");
    start.dataset.unavailable=enqueue.dataset.unavailable=String(missingModel||emptyPrompt||invalidSize||invalidSeed);
    start.disabled=missingModel||emptyPrompt||invalidSize||invalidSeed||!!window.activeStudioJob||start.hasAttribute("aria-busy");
    enqueue.disabled=missingModel||emptyPrompt||invalidSize||invalidSeed||enqueue.hasAttribute("aria-busy");
  };
  for(const field of [model,count,seed,...imageSize.controls])field.addEventListener(field.tagName==="INPUT"?"input":"change",updateSubmitSummary);updateSubmitSummary();
  inputPane.append(formScroll,submitActions);
  start.classList.add("prompt-start");
  const selections=node("section",undefined,"prompt-selection workspace-pane");
  selections.setAttribute("aria-label","本次出图选择");
  loraBox.classList.add("prompt-loras");
  characterBox.querySelector("h3").after(noCharacters);
  loraBox.querySelector("h3").after(noLoras);
  function searchChoices(label, matches, describe){
    const group=node("div",undefined,"choice-search"), input=node("input"), results=node("div");
    input.type="search";input.placeholder=label;input.setAttribute("aria-label",label);
    results.hidden=true;
    function close(){results.hidden=true;results.replaceChildren();}
    function open(){
      const query=input.value.trim(), rows=matches(query);
      results.replaceChildren();results.hidden=false;
      for(const row of (query ? rows : rows.slice(0,5))){
        const option=node("button",describe(row));
        option.type="button";
        if(row.file)option.title=row.file;
        option.onclick=()=>{
          row.check.click();
          input.value="";close();
          (row.weight || row.check).focus();
        };
        results.append(option);
      }
      if(!rows.length)results.append(node("small","没有匹配的可选项"));
      else if(!query && rows.length>5)results.append(node("small","输入名称可搜索全部"));
    }
    input.onfocus=input.onclick=input.oninput=open;
    input.onkeydown=event=>{
      if(event.key==="ArrowDown"){
        event.preventDefault();if(results.hidden)open();
        results.querySelector("button")?.focus();
      }
    };
    group.addEventListener("focusout",event=>{if(!group.contains(event.relatedTarget))close();});
    group.onkeydown=event=>{if(event.key==="Escape"){event.preventDefault();input.focus();close();}};
    group.append(input,results);
    return group;
  }
  const characterSearch=searchChoices("搜索角色",q=>characterRows.filter(r=>!r.check.checked && characterMatchesSearch(r.card,q)),r=>r.card.name);
  const loraSearch=searchChoices("搜索 LoRA",q=>loraPicker.displayRows.filter(r=>!r.check.checked && !r.check.disabled && loraMatchesSearch(r.file,data.lora_inspection?.[r.file],q)),r=>loraChoiceName(r.file));
  const characterSettings=node("details");
  characterSettings.append(node("summary","角色设置与管理"),bindingStatus,characterActions);
  selections.append(characterBox,loraBox,characterSearch,loraSearch,characterSettings);

  // --- Secondary workspace panel for Second Pass (keeps right gallery in place) ---
  const secondPassPane = node("section", undefined, "prompt-second-pass workspace-pane");
  secondPassPane.hidden = true;
  secondPassPane.setAttribute("aria-label", "二次采样设置");

  function spModelDefaults(checkpoint, method="latent") {
    const family = data.diffusion_models?.[checkpoint]?.family;
    const latent = method === "latent";
    if (family === "krea2") return {denoise: latent ? 0.5 : 0.3, steps: 8, directory: "krea2", family: "Krea2", comfortPixels: 4800000, tileSize: 1280, align: 8};
    if (family === "moss_image") return {denoise: latent ? 0.45 : 0.3, steps: latent ? 20 : 16, directory: "moss_image", family: "MOSS-Image", comfortPixels: 2600000, tileSize: 1280, align: 16};
    if (["anima", "anima38", "delta"].includes(family) || checkpoint === "Anima-3.8B-v1.1.safetensors")
      return {denoise: latent ? 0.45 : 0.35, steps: 24, directory: "anime", family: "Anima", comfortPixels: 2400000, tileSize: 1024, align: 8};
    return {denoise: latent ? 0.45 : 0.35, steps: 20, directory: "sdxl", family: "SDXL", comfortPixels: 2000000, tileSize: 1024, align: 8};
  }

  function spCalcSize(origW, origH, scale, def) {
    const a = def.align || 8;
    const tw = Math.max(a, Math.floor(origW * scale / a + 0.5) * a);
    const th = Math.max(a, Math.floor(origH * scale / a + 0.5) * a);
    const tile = def.tileSize || 1024;
    const ov = 192;
    const axisCount = (len, t) => len <= t ? 1 : Math.ceil((len - ov) / Math.max(a, t - ov));
    const nx = axisCount(tw, Math.min(tw, tile));
    const ny = axisCount(th, Math.min(th, tile));
    return {tw, th, tiles: nx * ny, tile};
  }

  function exitSecondPass(silent=false) {
    if (!secondPassState) return;
    secondPassState = null;
    secondPassPane.hidden = true;
    secondPassPane.replaceChildren();
    inputPane.hidden = false;
    selections.hidden = false;
    if (!silent) renderGallery();
  }

  function enterSecondPass(base, owner) {
    const appliedId = result?.images?.second_pass_applied?.[base.id] || null;
    secondPassState = {baseId: base.id, owner, viewId: appliedId || base.id, pendingCount: 0, pendingPrevIds: null};
    inputPane.hidden = true;
    selections.hidden = true;
    secondPassPane.hidden = false;
    secondPassPane.replaceChildren();

    const header = node("div", undefined, "sp-header");
    const backBtn = button("← 返回", () => exitSecondPass(), "small sp-back");
    header.append(backBtn, node("h2", "二次采样"), node("small", "按 Esc 返回"));

    const scroll = node("div", undefined, "sp-scroll");
    const grid = node("div", undefined, "sp-grid");
    const addControl = (labelText, el, title="") => {
      const wrap = node("label", labelText);
      if (title) wrap.title = title;
      wrap.append(el); grid.append(wrap); return el;
    };

    const spModel = addControl("二采模型", node("select"), "可保持原模型，或跨模型二采（如 Anima → Krea2）");
    spModel.dataset.spField = "model";
    const eligibleModels = (data.comfy.checkpoints || []).filter(name => data.diffusion_models?.[name]?.family !== "qwen_edit");
    for (const name of eligibleModels) {
      const opt = node("option", data.diffusion_models?.[name]?.label || name);
      opt.value = name; spModel.append(opt);
    }
    spModel.value = eligibleModels.includes(base.batch.checkpoint) ? base.batch.checkpoint : (eligibleModels[0] || "");

    const spScale = addControl("缩放大小", node("select"), "1.0× 原尺寸重绘；1.4× 对齐白垩光潜空间二采；≥1.5× 可拉高分");
    spScale.dataset.spField = "scale";
    for (const [val, label] of [["1", "1.0×（原尺寸重绘）"], ["1.25", "1.25×"], ["1.4", "1.4×（潜空间推荐）"], ["1.5", "1.5×"], ["2", "2.0×"]]) {
      const opt = node("option", label); opt.value = val; spScale.append(opt);
    }
    let scaleEdited = false;
    spScale.addEventListener("change", () => { scaleEdited = true; });

    const spMethod = addControl("二采方式", node("select"), "细节重绘走潜空间 bicubic（白垩光视频同款，长出新细节）；保真超分走 RealESRGAN 保留原画");
    spMethod.dataset.spField = "method";
    for (const [val, label] of [
      ["latent", "细节重绘（潜空间 bicubic，推荐）"],
      ["upscale", "保真超分（RealESRGAN，保留原画）"],
    ]) {
      const opt = node("option", label); opt.value = val; spMethod.append(opt);
    }
    spMethod.value = "latent";

    const spMode = addControl("分块策略", node("select"), "超出模型舒适区时，分块高清会按 1K 原生甜点区逐块重绘并无缝拼接");
    spMode.dataset.spField = "mode";
    for (const [val, label] of [
      ["auto", "自动（推荐）"],
      ["full", "整图精修（≤2K 零接缝）"],
      ["tiled", "分块高清（2K–4K 不崩图）"],
    ]) {
      const opt = node("option", label); opt.value = val; spMode.append(opt);
    }
    spMode.value = "auto";

    const spDenoise = addControl("重绘幅度", node("input"), "潜空间细节重绘推荐 0.45–0.50；分块高清推荐 0.30–0.38；保真超分推荐 0.25–0.32");
    spDenoise.type = "number"; spDenoise.min = "0.05"; spDenoise.max = "1"; spDenoise.step = "0.05";
    spDenoise.dataset.spField = "denoise";
    let denoiseEdited = false;
    spDenoise.addEventListener("input", () => { denoiseEdited = true; updateSpSummary(); });

    const spSteps = addControl("采样步数", node("input"), "留空使用当前模型的推荐二采步数");
    spSteps.type = "number"; spSteps.min = "1"; spSteps.max = "100"; spSteps.step = "1";
    spSteps.dataset.spField = "steps";

    const spSeed = addControl("种子（留空随机）", node("input"), "留空随机；填写整数后多张按序递增");
    spSeed.type = "number"; spSeed.min = "0"; spSeed.max = "9007199254740991"; spSeed.step = "1";
    spSeed.placeholder = "随机"; spSeed.dataset.spField = "seed";

    const spCount = addControl("生成张数", node("select"), "1 张完成后自动应用；多张完成后由你手动选图应用");
    spCount.dataset.spField = "count";
    for (let n = 1; n <= 8; n++) {
      const opt = node("option", n + " 张" + (n === 1 ? "（自动应用）" : "（手动选图）"));
      opt.value = String(n); spCount.append(opt);
    }
    spCount.value = "1";

    const spPostUpscale = addControl("二采后超分", node("select"), "采样完成后再用 RealESRGAN_x4plus_anime_6B 锐化放大，不改脸型、不长分身");
    spPostUpscale.dataset.spField = "post_upscale";
    for (const [val, label] of [
      ["1", "关闭（默认）"],
      ["1.5", "1.5×（RealESRGAN 超分）"],
      ["2", "2.0×（RealESRGAN 超分）"],
    ]) {
      const opt = node("option", label); opt.value = val; spPostUpscale.append(opt);
    }
    spPostUpscale.value = "1";

    const spSizeHint = node("div", "", "sp-size-hint");
    spSizeHint.dataset.spSizeHint = "true";
    const baseSize = {w: Number(base.width) || 0, h: Number(base.height) || 0};
    if (!baseSize.w || !baseSize.h) {
      const shownImg = gallery.querySelector(".gallery-image img");
      if (shownImg && shownImg.naturalWidth && (!appliedId)) {
        baseSize.w = shownImg.naturalWidth;
        baseSize.h = shownImg.naturalHeight;
      }
    }
    spScale.value = (baseSize.w * baseSize.h >= 2000000) ? "1" : "1.4";
    if (!baseSize.w || !baseSize.h) {
      const probe = new Image();
      const [bundle, file] = base.file.split("/");
      probe.onload = () => {
        if (!secondPassState || secondPassState.baseId !== base.id) return;
        baseSize.w = probe.naturalWidth;
        baseSize.h = probe.naturalHeight;
        if (!scaleEdited && baseSize.w * baseSize.h >= 2000000) spScale.value = "1";
        syncSpModelDefaults();
      };
      probe.src = `/api/image?id=${encodeURIComponent(owner)}&bundle=${encodeURIComponent(bundle)}&file=${encodeURIComponent(file)}`;
    }

    // Second-pass LoRA picker (architecture-isolated, inherits compatible base LoRAs)
    const spLoraBox = node("section", undefined, "candidate sp-loras");
    spLoraBox.append(node("h3", "二采 LoRA"));
    const spSelectedLoras = new Map();
    const spLoraList = node("div", undefined, "sp-lora-selected");
    const spLoraSearchWrap = node("div", undefined, "choice-search");
    const spLoraInput = node("input");
    spLoraInput.type = "search"; spLoraInput.placeholder = "搜索当前模型兼容的 LoRA";
    spLoraInput.setAttribute("aria-label", "搜索二采 LoRA");
    const spLoraResults = node("div"); spLoraResults.hidden = true;
    spLoraSearchWrap.append(spLoraInput, spLoraResults);
    spLoraBox.append(spLoraList, spLoraSearchWrap);

    const isSpLoraCompatible = (file, checkpoint) => {
      const spec = spModelDefaults(checkpoint);
      const info = data.lora_inspection?.[file];
      return file.startsWith(spec.directory + "/") && info?.family === spec.family;
    };
    for (const l of base.batch.loras || []) {
      const key = resolveLoraFile(l.file, data);
      if (isSpLoraCompatible(key, spModel.value)) spSelectedLoras.set(key, Number(l.weight ?? 0.7));
    }

    function renderSpLoras() {
      spLoraList.replaceChildren();
      if (!spSelectedLoras.size) {
        spLoraList.append(node("small", "未选择 LoRA（使用纯底模二采）"));
        return;
      }
      for (const [file, weight] of spSelectedLoras) {
        const row = node("div", undefined, "sp-lora-row");
        const name = node("span", loraChoiceName(file)); name.title = file;
        const wInput = node("input"); wInput.type = "number"; wInput.min = "0"; wInput.max = "2"; wInput.step = "0.05"; wInput.value = String(weight);
        wInput.setAttribute("aria-label", loraChoiceName(file) + " 强度");
        wInput.oninput = () => {
          const val = Number(wInput.value);
          spSelectedLoras.set(file, val);
          window.rememberLoraWeight(file, val);
          updateSpSummary();
        };
        const del = button("移除", () => { spSelectedLoras.delete(file); renderSpLoras(); updateSpSummary(); }, "small");
        row.append(name, wInput, del);
        spLoraList.append(row);
      }
    }
    function closeSpLoraResults() { spLoraResults.hidden = true; spLoraResults.replaceChildren(); }
    function openSpLoraResults() {
      const q = spLoraInput.value.trim();
      const matches = (data.comfy.loras || []).filter(file =>
        !spSelectedLoras.has(file) && isSpLoraCompatible(file, spModel.value) && loraMatchesSearch(file, data.lora_inspection?.[file], q));
      spLoraResults.replaceChildren(); spLoraResults.hidden = false;
      for (const file of (q ? matches.slice(0, 20) : matches.slice(0, 6))) {
        const opt = node("button", loraChoiceName(file)); opt.type = "button"; opt.title = file;
        opt.onclick = () => {
          if (spSelectedLoras.size >= 5) { notify("最多同时选择 5 个 LoRA", true); return; }
          spSelectedLoras.set(file, Number(window.defaultLoraWeight(file, data, 0.7)));
          spLoraInput.value = ""; closeSpLoraResults(); renderSpLoras(); updateSpSummary();
        };
        spLoraResults.append(opt);
      }
      if (!matches.length) spLoraResults.append(node("small", "当前模型下无匹配 LoRA"));
      else if (!q && matches.length > 6) spLoraResults.append(node("small", "输入名称可搜索全部"));
    }
    spLoraInput.onfocus = spLoraInput.onclick = spLoraInput.oninput = openSpLoraResults;
    spLoraSearchWrap.addEventListener("focusout", ev => { if (!spLoraSearchWrap.contains(ev.relatedTarget)) closeSpLoraResults(); });
    spLoraSearchWrap.onkeydown = ev => {
      if (ev.key === "Escape" && !spLoraResults.hidden) {
        ev.preventDefault(); ev.stopPropagation(); closeSpLoraResults(); spLoraInput.focus();
      }
    };

    // Optional collapsible prompt tweak
    const spPrompts = node("details", undefined, "sp-prompts");
    spPrompts.append(node("summary", "微调二采提示词（可选，默认沿用原图）"));
    const spPos = labeledInput(spPrompts, "正向提示词", true, base.positive || positive.value || "");
    spPos.rows = 4; delete spPos.dataset.editKey;
    const spNeg = labeledInput(spPrompts, "负面提示词", true, base.negative || "");
    spNeg.rows = 2; delete spNeg.dataset.editKey;

    const spSummary = node("small", "");
    const spStart = button("开始二采", async () => {
      const rawSeed = spSeed.value.trim();
      let parsedSeed = null;
      if (rawSeed) {
        if (!/^\d+$/.test(rawSeed) || !Number.isSafeInteger(Number(rawSeed))) throw Error("种子须为 0 至 9007199254740991 的整数");
        parsedSeed = Number(rawSeed);
      }
      const rawSteps = spSteps.value.trim();
      let parsedSteps = null;
      if (rawSteps) {
        const n = Number(rawSteps);
        if (!Number.isInteger(n) || n < 1 || n > 100) throw Error("采样步数须为 1–100 的整数");
        parsedSteps = n;
      }
      const dVal = Number(spDenoise.value);
      if (!Number.isFinite(dVal) || dVal < 0.05 || dVal > 1) throw Error("重绘幅度须为 0.05–1.0");
      const lorasPayload = [...spSelectedLoras.entries()].map(([file, weight]) => ({file, weight}));
      if (lorasPayload.some(l => !Number.isFinite(l.weight) || l.weight < 0 || l.weight > 2)) throw Error("LoRA 强度须为 0–2");
      const prevIds = new Set((result?.images?.batches || [])
        .filter(b => b.kind === "second_pass" && b.source_image_id === base.id)
        .flatMap(b => b.images.map(im => im.id)));
      secondPassState.pendingPrevIds = prevIds;
      secondPassState.pendingCount = Number(spCount.value);
      await act("second_pass", {
        project: owner,
        source_image_id: base.id,
        checkpoint: spModel.value,
        scale: Number(spScale.value),
        method: spMethod.value,
        mode: spMode.value,
        post_upscale: Number(spPostUpscale.value),
        denoise: dVal,
        ...(parsedSteps !== null ? {steps: parsedSteps} : {}),
        ...(parsedSeed !== null ? {seed: parsedSeed} : {}),
        count: Number(spCount.value),
        loras: lorasPayload,
        positive: spPos.value,
        negative: spNeg.value,
      });
      notify(Number(spCount.value) === 1
        ? "已开始二次采样，完成后将自动应用。"
        : `已开始二次采样（${spCount.value} 张），完成后请在右侧挑选并应用。`);
    }, "primary sp-start");
    spStart.dataset.startJob = "true";

    function effectiveSpMode(def, sizeInfo) {
      if (!sizeInfo) return spMode.value === "tiled" ? "tiled" : "full";
      const canTile = sizeInfo.tw > sizeInfo.tile || sizeInfo.th > sizeInfo.tile;
      if (spMode.value === "full") return "full";
      if (spMode.value === "tiled") return canTile ? "tiled" : "full";
      return (sizeInfo.tw * sizeInfo.th > def.comfortPixels && canTile) ? "tiled" : "full";
    }
    function syncSpModelDefaults() {
      const def = spModelDefaults(spModel.value, spMethod.value);
      const sizeInfo = baseSize.w && baseSize.h ? spCalcSize(baseSize.w, baseSize.h, Number(spScale.value), def) : null;
      const effMode = effectiveSpMode(def, sizeInfo);
      if (!denoiseEdited) {
        spDenoise.value = String(effMode === "tiled" ? Math.min(def.denoise, 0.3) : def.denoise);
      }
      spSteps.placeholder = `默认 ${def.steps} 步`;
      for (const file of [...spSelectedLoras.keys()]) {
        if (!isSpLoraCompatible(file, spModel.value)) spSelectedLoras.delete(file);
      }
      renderSpLoras();
      updateSpSummary();
    }
    function updateSpSummary() {
      const def = spModelDefaults(spModel.value, spMethod.value);
      const scaleNum = Number(spScale.value);
      const postNum = Number(spPostUpscale.value);
      const sizeInfo = baseSize.w && baseSize.h ? spCalcSize(baseSize.w, baseSize.h, scaleNum, def) : null;
      const effMode = effectiveSpMode(def, sizeInfo);
      let overLimit = false;
      spSizeHint.classList.remove("sp-size-warn");
      if (sizeInfo) {
        const outW = postNum > 1 ? Math.max(2, Math.round(sizeInfo.tw * postNum / 2) * 2) : sizeInfo.tw;
        const outH = postNum > 1 ? Math.max(2, Math.round(sizeInfo.th * postNum / 2) * 2) : sizeInfo.th;
        const postTail = postNum > 1 ? ` → 超分 ${outW}×${outH}` : "";
        overLimit = sizeInfo.tw > 4096 || sizeInfo.th > 4096 || outW > 6144 || outH > 6144;
        const methodText = spMethod.value === "latent" ? "潜空间细节重绘" : "保真超分";
        if (overLimit) {
          spSizeHint.classList.add("sp-size-warn");
          spSizeHint.textContent = `⚠️ ${baseSize.w}×${baseSize.h} → ${sizeInfo.tw}×${sizeInfo.th}${postTail} 超过分辨率上限，请降低倍率`;
        } else if (effMode === "tiled") {
          const highTiledDenoise = Number(spDenoise.value) > 0.35;
          if (highTiledDenoise) spSizeHint.classList.add("sp-size-warn");
          spSizeHint.textContent = highTiledDenoise
            ? `⚠️ ${baseSize.w}×${baseSize.h} → ${sizeInfo.tw}×${sizeInfo.th}${postTail} · 分块高清（约 ${sizeInfo.tiles} 块）重绘 > 0.35 易在背景块长出人物分身，建议压至 0.25–0.32 或改「整图精修」`
            : `尺寸：${baseSize.w}×${baseSize.h} → ${sizeInfo.tw}×${sizeInfo.th}${postTail} · ${methodText} + 分块高清（约 ${sizeInfo.tiles} 块 × ${sizeInfo.tile}px，低重绘防分身）`;
        } else if (sizeInfo.tw * sizeInfo.th > def.comfortPixels) {
          spSizeHint.classList.add("sp-size-warn");
          spSizeHint.textContent = `⚠️ ${baseSize.w}×${baseSize.h} → ${sizeInfo.tw}×${sizeInfo.th}${postTail} 超出 ${def.family} 整图舒适区，易出噪点崩坏；建议改用「分块高清」或降低缩放倍率`;
        } else {
          spSizeHint.textContent = `尺寸：${baseSize.w}×${baseSize.h} → ${sizeInfo.tw}×${sizeInfo.th}${postTail} · ${methodText}（${def.family} 整图舒适区内，零接缝）`;
        }
      } else {
        spSizeHint.textContent = "正在读取原图尺寸…";
      }
      const stepLabel = spSteps.value.trim() ? `${spSteps.value.trim()} 步` : `默认 ${def.steps} 步`;
      const seedLabel = spSeed.value.trim() ? ` · 种子 ${spSeed.value.trim()}` : "";
      const modeText = effMode === "tiled" ? "分块" : "整图";
      const mShort = spMethod.value === "latent" ? "潜空间" : "超分";
      const postSummary = postNum > 1 ? ` · 后置超分 ${postNum}×` : "";
      spSummary.textContent = `${spModel.selectedOptions[0]?.textContent || spModel.value} · ${mShort}${modeText} · ${spScale.value}×${postSummary} · 重绘 ${spDenoise.value} · ${stepLabel} · ${spCount.value} 张${seedLabel}`;
      spStart.disabled = !spModel.value || !spPos.value.trim() || overLimit || !!window.activeStudioJob;
    }
    spModel.addEventListener("change", syncSpModelDefaults);
    spMethod.addEventListener("change", syncSpModelDefaults);
    spScale.addEventListener("change", syncSpModelDefaults);
    spMode.addEventListener("change", syncSpModelDefaults);
    for (const el of [spSteps, spSeed, spCount, spPostUpscale, spPos]) el.addEventListener(el.tagName === "INPUT" || el.tagName === "TEXTAREA" ? "input" : "change", updateSpSummary);
    syncSpModelDefaults();

    scroll.append(grid, spSizeHint, spLoraBox, spPrompts);
    const spFooter = node("div", undefined, "prompt-submit-actions sp-submit");
    spFooter.append(spSummary, spStart);
    secondPassPane.append(header, scroll, spFooter);
    renderGallery();
    backBtn.focus();
  }

  const onSecondPassKeydown = ev => {
    if (!root.isConnected) { window.removeEventListener("keydown", onSecondPassKeydown); return; }
    if (ev.key === "Escape" && secondPassState && !document.querySelector("dialog[open]")) {
      ev.preventDefault();
      exitSecondPass();
    }
  };
  window.addEventListener("keydown", onSecondPassKeydown);

  root.replaceChildren(inputPane,selections,secondPassPane,galleryPane);
  const draftInput=()=>({text:positive.value,negative:negative.value,json_editor:jsonEditor.state,board_target:board?panelChoice.value:undefined,checkpoint:model.value,count:Number(count.value),seed:seed.value.trim(),...imageSize.draft(),
      task_name:taskName.value,consistency_lora:consistency.checked,delta_matte_skin:matte.checked,
      characters:characterRows.filter(r=>r.check.checked||(r.promptLoaded&&r.prompt.value!==r.defaultPrompt)).map(r=>({name:r.card.name,revision:r.card.revision,selected:r.check.checked,prompt:r.prompt.value})),
      loras:loraRows.filter(r=>r.check.checked).map(r=>({file:r.file,weight:Number(r.weight.value),character_id:r.owner.value,character:r.item.character}))});
  // Leaving an untouched tab must not overwrite edits saved in another tab.
  let lastDraftInput=JSON.stringify(draftInput());
  let draftStored=draft?.persisted!==false;
  window.savePromptDraft=()=>{
    if(root.dataset.renderToken!==renderToken||!positive.isConnected)return;
    updateSubmitSummary();
    const input=draftInput();
    const serialized=JSON.stringify(input);if(serialized===lastDraftInput&&draftStored)return;
    lastDraftInput=serialized;
    const record={version:1,updated:Date.now(),input};
    const savedLocally=cacheLocal(draftKey,JSON.stringify(record));
    record.persisted=draftStored=savedLocally;window.promptStudioDrafts.set(draftKey,record);
    const notice=savedLocally?"草稿已保存 · "+new Date(record.updated).toLocaleTimeString("zh-CN",{hour:"2-digit",minute:"2-digit"})+" · 当前浏览器":"草稿暂存在本页；浏览器存储不可用，关闭或刷新前请复制提示词。";
    if(draftStatus.textContent!==notice)draftStatus.textContent=notice;
    draftStatus.classList.toggle("warning",!savedLocally);
  };
  window.refreshPromptResults = refresh;
  await refresh();
};
