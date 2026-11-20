"use strict";
window.renderPromptStudio = async function(root, project) {
  const renderToken = root.dataset.renderToken;
  const [data, initialCharacters] = await Promise.all([
    api("/api/drawing-options"), api("/api/characters"),
  ]);
  if (root.dataset.renderToken !== renderToken) return;
  const clothingPolicy = createClothingPolicy(initialCharacters.clothing_policy);
  const draftKey="studio-prompt-draft-"+(project?.quick_sample&&!project.transient&&pid&&!pid.startsWith("tmp-")?pid:"new");
  window.promptStudioDrafts ||= new Map();
  let draft=window.promptStudioDrafts.get(draftKey);
  try{const stored=JSON.parse(localStorage.getItem(draftKey)||"null");if(stored?.version===1&&(stored.updated||0)>(draft?.updated||0))draft=stored;}catch{}
  if(draft?.version!==1 || !draft.input || typeof draft.input!=="object")draft=null;
  const saved = draft?.input || (project?.quick_sample ? project.prompt_input : {}) || {};
  const isDiffusion = name => !!data.diffusion_models?.[name];
  let animaActive = isDiffusion(saved.checkpoint || data.comfy.configured);
  const thisProject = project?.quick_sample ? pid : null;
  root.className = "prompt-studio";
  root.replaceChildren(node("h2", "直接输入提示词画图"),
    node("p", "输入画面描述，选择角色并填写外观。可先整理 / 转换英文提示词，核对后出图；多人可指定位置并分区绘制外观。"));
  const modelDescription = root.children[1];
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
  const characterBox = node("section", undefined, "candidate prompt-characters");
  characterBox.append(node("h3", "角色 · 最多 4 个"));
  characterBox.append(node("small", "衣着由提示词决定；未提到衣着时才补入角色默认服装。可在下方最终预览核对。"));
  const characterList = node("div", undefined, "prompt-character-list"), characterCount = node("p");
  const regionalLabel = node("label"), regional = node("input");
  regional.type = "checkbox"; regional.checked = saved.regional_characters ?? !project;
  regional.dataset.editKey = "prompt-regional-characters";
  regionalLabel.append(regional, document.createTextNode("按人物位置分区绘制"));
  regionalLabel.title = "需为每人指定位置；人物外观分别限制到对应区域。";
  const strengthLabel = node("label", "参考图强度"), strength = node("input");
  strengthLabel.title = "越高越容易带入原图背景和构图，参考图不能保证服装细节完全一致。";
  strength.type = "number"; strength.min = "0"; strength.max = "1"; strength.step = "0.05";
  strength.value = String(saved.reference_weight ?? 0.6);
  strength.dataset.editKey = "prompt-reference-weight";
  strength.setAttribute("aria-label", "参考图强度"); strengthLabel.append(strength);
  const languageNotice = node("p", undefined, "warning");
  const bindingStatus = node("p"); bindingStatus.dataset.characterBinding = "true";
  const preview = labeledInput(characterBox, "最终正向提示词预览", true);
  preview.readOnly = true; preview.rows = 5; delete preview.dataset.editKey;
  let characterRows = [], loraPicker = null;
  const characterSearch = node("input");
  characterSearch.type = "search"; characterSearch.placeholder = "搜索角色：中文名、拼音、英文别名";
  characterSearch.setAttribute("aria-label", "搜索角色");
  function refreshCharacterPage() {
    let matches = 0;
    for (const row of characterRows) {
      row.row.hidden = !characterMatchesSearch(row.card, characterSearch.value);
      if (!row.row.hidden) matches++;
    }
    characterList.querySelector(".character-empty")?.remove();
    if (!matches) characterList.append(node("p", characterRows.length ? "没有匹配的角色" : "还没有角色，可点击“管理角色”添加。", "character-empty"));
  }
  characterSearch.addEventListener("input", refreshCharacterPage);
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
    const selected = characterRows.filter(r => r.check.checked);
    for (const row of characterRows) {
      row.details.hidden = !row.check.checked;
      row.useReference.disabled = animaActive || !row.card.image_id;
      if(animaActive) row.useReference.checked = false;
    }
    characterCount.textContent = `已选 ${selected.length}/4` + (selected.length ? " · " + selected.map(r => r.card.name).join("、") : "");
    regional.disabled = animaActive || selected.length < 2 || selected.some(r=>r.region.value === "null");
    if(animaActive) regional.checked = false;
    const imageCharacters = selected.filter(r => r.card.image_id && r.useReference.checked);
    strength.disabled = !imageCharacters.length;
    bindingStatus.textContent = selected.length ? "本次使用：" + selected.map(r =>
      r.card.name + "（" + [r.prompt.value.trim() ? "外观文字" : "",
        r.card.image_id && r.useReference.checked ? "参考图 × " + strength.value : "仅文字，无参考图"]
        .filter(Boolean).join(" + ") + "）").join("；") : "尚未选择角色。";
    const cards = selected.map(r => r.card);
    for (const row of selected) row.effectivePrompt = clothingPolicy.resolve(positive.value, row.prompt.value, row.card, cards);
    const combinedText = [positive.value, ...selected.map(r => r.effectivePrompt).filter(v => v.trim())].join("\n\n");
    preview.value = regional.checked && !regional.disabled
      ? ["GLOBAL SCENE:\n" + positive.value, ...selected.map(r=>"REGION " + r.card.name + " " + r.region.value + ":\n" + r.effectivePrompt)].join("\n\n")
      : combinedText;
    const contaminated = selected.filter(r => chatCard.test(r.prompt.value));
    languageNotice.hidden = !contaminated.length && !/[\u3400-\u9fff]/u.test(combinedText + negative.value);
    languageNotice.textContent = contaminated.length
      ? contaminated.map(r=>r.card.name).join("、") + "的外观框含性格、对白等聊天设定，会被一起送进绘图模型。请先整理 / 转换英文提示词，再核对服装和人数。"
      : "当前含中文。建议先整理 / 转换英文提示词并核对，尤其是角色外观和当前场景的服装。";
  }
  async function loadPromptCharacters(initial) {
    const previous = new Map(characterRows.map(r => [r.card.name, {
      checked:r.check.checked, prompt:r.prompt.value, region:r.region.value, useReference:r.useReference.checked,
    }]));
    const result = initial?.characters ? initial : await api("/api/characters");
    if (!characterBox.isConnected) return;
    characterRows = []; characterList.replaceChildren();
    for (const card of result.characters) {
      const stored = (saved.characters || []).find(c => c.name === card.name);
      const prior = previous.get(card.name);
      const row = node("div", undefined, "candidate"), label = node("label"), check = node("input");
      check.type = "checkbox"; check.checked = prior ? prior.checked : !!stored&&stored.selected!==false;
      check.dataset.editKey = "prompt-character-use-" + card.id;
      label.append(check, document.createTextNode(card.name + (card.image_id ? " · 有参考图" : " · 文字角色")));
      const details = node("div", undefined, "prompt-character-details");
      if (card.image_id) {
        const img = node("img"); img.src = "/api/character-image?id=" + encodeURIComponent(card.image_id);
        img.loading = "lazy"; img.decoding = "async";
        img.alt = card.name + "参考图"; img.className = "prompt-character-image"; details.append(img);
      }
      const visual = ["identity","face","hair","eyes","body","accessories","palette","marks"]
        .map(k => card.profile[k]?.trim()).filter(v => clothingPolicy.known(v));
      const seenVisual = new Set();
      const defaultPrompt = visual.flatMap(v => clothingPolicy.parts(v)).filter(v => {
        const key = v.toLowerCase();
        if (seenVisual.has(key)) return false;
        seenVisual.add(key);
        return clothingPolicy.parts(card.profile.identity || "").includes(v) || !clothingPolicy.has(v);
      }).join(", ");
      let restoredPrompt = prior?.prompt ?? stored?.source_prompt ?? stored?.prompt ?? defaultPrompt;
      if ((card.source?.drawing_prompt_cleanup?.previous_defaults || []).includes(restoredPrompt))
        restoredPrompt = defaultPrompt;
      const prompt = labeledInput(details, card.name + " · 外观提示词", true, restoredPrompt);
      if (clothingPolicy.known(card.profile.clothing)) details.append(node("small",
        "默认服装（提示词未指定时使用）：" + card.profile.clothing));
      if (stored && stored.revision !== card.revision) details.append(node("p",
        "角色库资料已更新，本次使用最新角色资料；已保留上次的外观提示词，请核对。"));
      prompt.rows = 3; prompt.dataset.editKey = "prompt-character-text-" + card.id;
      const useReference = node("input"), useReferenceLabel = node("label");
      useReference.type = "checkbox"; useReference.checked = prior?.useReference ?? stored?.use_reference ?? true;
      useReference.dataset.editKey = "prompt-character-reference-" + card.id;
      useReferenceLabel.append(useReference, document.createTextNode("使用" + card.name + "的参考图"));
      useReferenceLabel.hidden = !card.image_id; details.append(useReferenceLabel);
      const resetPrompt = button("重置外观", () => {
        prompt.value = defaultPrompt;
        prompt.dispatchEvent(new Event("input", {bubbles:true}));
      });
      resetPrompt.title = "重置为角色外观资料";
      const regionLabel=node("label","位置"),region=node("select");
      region.setAttribute("aria-label", card.name + " · 画面位置");
      regionLabel.title = "可选；不指定时按画面描述自由安排，角色 LoRA 仍可使用";
      const positions=[[null,"未指定"],[[0,0,0.5,1],"左半边"],[[0.5,0,1,1],"右半边"],[[0,0,1/3,1],"左侧三分之一"],[[1/3,0,2/3,1],"中间三分之一"],[[2/3,0,1,1],"右侧三分之一"],[[0,0,0.25,1],"第 1/4 区"],[[0.25,0,0.5,1],"第 2/4 区"],[[0.5,0,0.75,1],"第 3/4 区"],[[0.75,0,1,1],"第 4/4 区"]];
      for(const [value,text] of positions){const o=node("option",text);o.value=JSON.stringify(value);region.append(o);}
      region.value=prior?.region??JSON.stringify(stored?.region||null);region.dataset.editKey="prompt-character-region-"+card.id;
      regionLabel.append(region);
      const detailTools = node("div", undefined, "character-detail-tools");
      detailTools.append(regionLabel, resetPrompt); details.append(detailTools);
      region.addEventListener("change",refreshCharacters);
      // Auto-select only for an explicit character checkbox change. Refreshing,
      // restoring a draft or selecting another character must preserve opt-outs.
      check.addEventListener("change", () => {
        selectCharacterLoras(card.id, check.checked);
        refreshCharacters();
      });
      prompt.addEventListener("input", refreshCharacters);
      useReference.addEventListener("change", refreshCharacters);
      row.append(label, details); characterList.append(row);
      characterRows.push({card, row, check, prompt, details, region, useReference,defaultPrompt});
    }
    loraPicker?.updateCharacters(characterRows.map(r=>r.card));
    refreshCharacterPage();
    refreshCharacters();
  }
  characterBox.insertBefore(characterList, preview.parentElement);
  const characterTools = node("div", undefined, "character-tools");
  characterTools.append(characterSearch);
  characterBox.insertBefore(characterTools, characterList);
  const referenceControls = node("div", undefined, "reference-controls");
  referenceControls.append(regionalLabel, strengthLabel);
  const characterActions = node("div", undefined, "compact-actions");
  characterActions.append(button("管理角色", () =>
    window.openCharacterLibrary(() => loadPromptCharacters().catch(e => notify(e.message, true)))),
    button("刷新角色列表", loadPromptCharacters));
  characterBox.append(characterCount, bindingStatus, referenceControls, characterActions);
  positive.addEventListener("input", refreshCharacters);
  negative.addEventListener("input", refreshCharacters);
  strength.addEventListener("input", refreshCharacters);
  regional.addEventListener("change", refreshCharacters);
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
    const selected = characterRows.filter(r => r.check.checked);
    if (!positive.value.trim()) throw Error("请先填写正向提示词");
    if (!data.prompt_translate) throw Error("后台尚未更新，暂不能转换提示词。当前输入已保留。");
    const original = {positive:positive.value, negative:negative.value,
      characters:new Map(selected.map(r=>[r.card.id,r.prompt.value]))};
    const controls = [positive, negative, start, clearInputs, undoConversion, ...characterBox.querySelectorAll("input, textarea, button")];
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
  const catalog = data.comparison_models || [];
  for (const item of catalog) {
    const opt = node("option", item.label + (item.installed ? "" : " · 待下载"));
    opt.value = item.name; opt.disabled = !item.installed; model.append(opt);
  }
  for (const name of data.comfy.checkpoints.filter(n => !catalog.some(c => c.name === n))) {
    const opt = node("option", data.diffusion_models?.[name]?.label || name); opt.value = name; model.append(opt);
  }
  const selectableModels = [...model.options].filter(o => !o.disabled);
  model.value = [saved.checkpoint, data.comfy.configured]
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
  const taskNameLabel=node("label","排队任务名称（可选）"),taskName=node("input");taskName.maxLength=80;taskName.placeholder="例如：城市夜景 · 第二批";taskName.setAttribute("aria-label","排队任务名称");taskNameLabel.append(taskName);taskNameLabel.dataset.queueNameField="true";taskNameLabel.hidden=!window.lastStudioStatus?.queue?.can_rename;
  taskName.value=saved.task_name||"";
  const sizeFields=node("div",undefined,"image-size-fields");fields.append(sizeFields);
  const imageSize = createImageSizeControls(sizeFields, saved);
  const handLabel = node("label"), hand = node("input");
  hand.type = "checkbox"; hand.checked = saved.hand_detail ?? data.hand_detail?.enabled ?? false;
  hand.dataset.editKey = "prompt-hand-detail";
  handLabel.append(hand, document.createTextNode("自动增强手部"));
  handLabel.title = "局部放大细化，保留增强前原图";
  const handNote = node("small", data.hand_detail?.ready
    ? "手部检测模型已安装。检测到手部后最多细化 4 个区域，会增加出图时间；细化仍可能出现错误。"
    : "手部检测模型尚未就绪，开启后会保留原图并提示增强失败。");
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
  loraBox.append(node("h3","LoRA · 风格与角色"));
  loraBox.title = "选中角色时自动勾选对应 LoRA，可手动取消；刷新页面或选择其他角色不会重新勾选。全图风格手动勾选。每张最多 5 个，强度 0–2。";
  loraBox.append(node("small", "选中角色会自动勾选对应 LoRA；可随时取消，取消后不会自动勾回。"));
  const items=(data.comfy.loras||[]).map(file=>{
    const label=data.lora_labels?.[file]||{},stored=(saved.loras||[]).find(l=>resolveLoraFile(l.file,data)===file);
    return {file,name:label.name||stored?.label||file.split("/").pop().replace(/\.safetensors$/i,""),
      ...("character_id" in label?{character_id:label.character_id}:"character_id" in (stored||{})?{character_id:stored.character_id}:{}),
      character:label.character||stored?.character||"",selected:!!stored,weight:stored?.weight??label.weight??0.7,
      trigger_words:label.trigger_words||"",confirmed:!!label.confirmed};
  });
  loraPicker=createLoraPicker(loraBox,data,items,characterRows.map(r=>r.card),()=>{refreshCharacters();},"prompt-lora-");
  const loraRows=loraPicker.rows;
  for(const row of loraRows)row.row.querySelector("details").append(button("添加触发词",()=>{
    const words=row.item.trigger_words?.trim();if(!words)throw Error("请先在编辑中填写触发词");
    const character=characterRows.find(r=>r.card.id===row.owner.value);
    if(row.owner.value&&!character?.check.checked)throw Error("请先勾选对应角色");
    const input=character?character.prompt:positive;
    if(!input.value.toLowerCase().includes(words.toLowerCase()))input.value+=(input.value.trim()?", ":"")+words;
    input.dispatchEvent(new Event("input",{bubbles:true}));refreshCharacters();
  }));
  for(const row of loraRows)if(data.lora_labels?.[row.file]?.appearance_prompt)row.row.querySelector("details").append(button("使用该版本外观",()=>{
    const character=characterRows.find(r=>r.card.id===row.owner.value);
    if(!character?.check.checked)throw Error("请先勾选对应角色");
    character.prompt.value=data.lora_labels[row.file].appearance_prompt;
    character.prompt.dispatchEvent(new Event("input",{bubbles:true}));
    refreshCharacters();
  }));
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
    negative.disabled = krea;
    negative.parentElement.title = krea ? "Krea2 Turbo 使用零负向条件，负面提示词不参与生成" : "";
    modelDescription.textContent = animaActive
      ? "输入画面描述，选择角色与当前模型兼容的 LoRA。人物位置和互动请写入提示词。"
      : "输入画面描述，选择角色并填写外观。可先整理 / 转换英文提示词，核对后出图；多人可指定位置并分区绘制外观。";
    if (krea) modelDescription.textContent = "Krea2 Turbo · 默认 8 步。负面提示词不参与生成，LoRA 仅使用 Krea2 架构。";
    if (moss) modelDescription.textContent = "MOSS-Image SFT · BF16 高精度，默认 25 步。支持正负提示词和画面文字描述，LoRA 仅使用 MOSS-Image 架构。";
    regionalLabel.title = animaActive ? "当前模型请在提示词中写明人物位置。" : "需为每人指定位置；人物外观分别限制到对应区域。";
    hand.disabled = animaActive;
    if(animaActive) hand.checked = false;
    loraPicker.setCheckpoint(model.value);
    refreshCharacters();refreshLoras();
    if(animaActive) handNote.textContent = krea ? "Krea2 Turbo 默认 8 步，负面提示词不参与生成。仅使用 Krea2 LoRA。" : moss ? "MOSS 使用角色文字和 MOSS-Image LoRA；不使用 SDXL 参考图、分区控制和手部修复。" : "当前模型使用角色文字和 Anima LoRA；不使用 SDXL 参考图、分区控制和手部修复。";
  }
  model.addEventListener("change", modelCompatibility);
  modelCompatibility();
  window.refreshPromptControls=()=>{refreshCharacters();refreshLoras();};
  async function submitPrompt(queued=false) {
    if(queued && window.studioQueueAvailable===false)throw Error("夜间列表需要新版后台。当前生成结束后更新工作室即可使用，已填内容保留。");
    if (!positive.value.trim()) throw Error("请先填写正向提示词");
    if (!model.value || model.selectedOptions[0]?.disabled) throw Error("请选择已下载完整的模型");
    const loras=loraRows.filter(r=>r.check.checked && !r.check.disabled).map(r=>({file:r.file,weight:Number(r.weight.value),character_id:r.owner.value,character:r.item.character}));
    if (loras.length>5) throw Error("最多同时选择 5 个 LoRA，请减少勾选");
    if (loras.some(l=>!Number.isFinite(l.weight)||l.weight<0||l.weight>2)) throw Error("LoRA 强度须为 0–2");
    const characters = characterRows.filter(r=>r.check.checked).map(r=>({
      name:r.card.name, revision:r.card.revision, prompt:r.prompt.value, region:JSON.parse(r.region.value),
      use_reference:r.useReference.checked,
    }));
    const contaminated = characters.filter(c=>chatCard.test(c.prompt));
    if (contaminated.length) throw Error(contaminated.map(c=>c.name).join("、") + "的外观提示词含聊天设定，请先整理 / 转换英文提示词");
    const useReference = characterRows.some(r=>r.check.checked && r.card.image_id && r.useReference.checked);
    if (characters.length>4) throw Error("最多同时选择 4 个角色，请减少勾选");
    const referenceWeight = Number(strength.value);
    if (!Number.isFinite(referenceWeight) || referenceWeight < 0 || referenceWeight > 1)
      throw Error("参考图强度须为 0–1");
    if (useReference && referenceWeight === 0) throw Error("参考图已勾选，请将强度设为大于 0；只用文字请取消角色的参考图勾选");
    if (characters.length && !data.prompt_characters) throw Error("请更新工作室后台后使用角色选择。当前输入已保留。");
    await loraPicker.flush();
    await preserve();
    const created = await act(queued?"queue_prompt":"create_quick_sample", {text:positive.value, negative:negative.value,task_name:queued?taskName.value:undefined,
      checkpoint:model.value, count:Number(count.value), ...imageSize.read(), loras,
      characters, regional_characters:regional.checked && !regional.disabled,
      hand_detail:hand.checked,
      consistency_lora:consistency.checked && !consistency.disabled,
      delta_matte_skin:matte.checked,
      use_reference:useReference, reference_weight:referenceWeight});
    if(queued){
      taskName.value="";
      saveWorkspace();
      queuedReceipt.hidden=false;queuedReceiptText.textContent=`已加入：${count.value} 张 · ${positive.value.trim().slice(0,32)}。修改上方内容可继续添加。`;
      notify("已加入任务列表，结果会自动保存。可以修改提示词继续添加下一项。");await refreshStatus();await window.afterQueuedTask?.(created);return;
    }
    await openProject(created.project);
    await act("prompt_batch", {base_hash:snap.hash});
    notify("已开始出图，进度在上方显示；完成后图片会出现在右侧。");
  }
  const start = button("开始画图",()=>submitPrompt(),"primary");
  const enqueue = button("加入任务列表 · 稍后绘制",()=>submitPrompt(true));
  start.dataset.startJob = "true";
  start.dataset.unavailable = String(!model.value);
  start.disabled = !model.value || !!window.activeStudioJob;
  root.append(fields, handLabel, handNote, consistencyLabel, consistencyNote, loraBox, node("small", "一次 1–16 张。LoRA 不自动改写原提示词；需要触发词请填写在对应角色的外观提示词中。角色参考图按勾选启用，不自动审核，中文不会自动翻译。"), start);
  const gallery = node("section", undefined, "prompt-gallery workspace-pane"); root.append(gallery);
  let activeImage = 0;
  let galleryRevision = 0;
  const refresh = async () => {
    if (!gallery.isConnected) return;
    const revision = ++galleryRevision;
    gallery.replaceChildren(node("h2", "图片预览"));
    let images = [];
    const copyTools = node("div", undefined, "gallery-copy-tools");
    const copyCurrent = button("复制当前图片", () => copyImages(false), "small");
    const copyAll = button("复制所有图片", () => copyImages(true), "small");
    const clearImages = button("一键清除所有图片", async () => {
      if (window.activeStudioJob) throw Error("请等待当前任务结束，或取消任务后再清除图片");
      ++galleryRevision;
      clearImages.dataset.unavailable = "true";
      copyCurrent.disabled = copyAll.disabled = true;
      try {
        const result = await api("/api/action", {action:"clear_prompt_images", project:thisProject});
        activeImage = 0;
        await refresh();
        notify(`已清除 ${result.cleared} 张图片，可以继续生成新图片。`);
      } catch (error) {
        await refresh();
        throw error;
      }
    }, "small danger");
    clearImages.title = "清除本次全部生成图片，保留提示词、角色、LoRA 和出图设置";
    clearImages.dataset.startJob = "true";
    clearImages.dataset.unavailable = "true";
    clearImages.disabled = true;
    copyAll.setAttribute("aria-label", "复制所有图片");
    copyAll.title = "复制本次已生成的所有原图，可粘贴到文件夹或支持多图粘贴的软件";
    copyCurrent.disabled = copyAll.disabled = true;
    copyTools.append(copyCurrent, copyAll, clearImages); gallery.append(copyTools);
    async function copyImages(all) {
      if (!images.length) throw Error("还没有可复制的图片");
      const selected = all ? [...images] : [images[activeImage]];
      copyCurrent.disabled = copyAll.disabled = true;
      try {
        const copied = await api("/api/action", {action:"copy_prompt_images", project:thisProject,
          images:selected.map(image => image.id)});
        notify(all ? `已复制 ${copied.copied} 张原图，可粘贴到文件夹或支持多图粘贴的软件。` : "已复制当前图片，可直接粘贴。");
      } finally {
        copyCurrent.disabled = copyAll.disabled = !images.length;
      }
    }
    if (!thisProject) {
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
    const result = await api("/api/images?id=" + encodeURIComponent(thisProject));
    if (!gallery.isConnected || revision !== galleryRevision) return;
    images = result.images.batches.flatMap(b => b.images.map(image => ({...image, batch:b})));
    if (!images.length) {gallery.append(node("div", window.activeStudioJob
      ? "等待图片生成…\n生成进度显示在顶部"
      : project?.transient===false?"这项排队作品还没有图片\n打开「任务列表」查看等待或执行状态":"暂无生成图片\n点击“开始画图”生成新图片", "gallery-empty"));return;}
    clearImages.dataset.unavailable = "false";
    clearImages.disabled = !!window.activeStudioJob;
    copyCurrent.disabled = copyAll.disabled = false;
    copyAll.textContent = `复制所有图片（${images.length}）`;
    activeImage = Math.min(activeImage, images.length - 1);
    const viewer = node("div", undefined, "gallery-viewer");
    const navigation = node("div", undefined, "gallery-navigation");
    const imageNumber = node("small");
    const previous = button("‹ 上一张", () => {activeImage--; showImage();}, "small");
    const next = button("下一张 ›", () => {activeImage++; showImage();}, "small");
    navigation.append(previous, imageNumber, next);
    const thumbnails = node("div", undefined, "gallery-thumbnails");
    const imageUrl = image => {
      const [bundle, file]=image.file.split("/");
      return `/api/image?id=${encodeURIComponent(thisProject)}&bundle=${encodeURIComponent(bundle)}&file=${encodeURIComponent(file)}`;
    };
    function showImage() {
      const image = images[activeImage];
      const card=node("article",undefined,"gallery-image");
      const url = imageUrl(image);
      const link=node("a"), img=node("img");img.src=url;img.alt="提示词出图 "+image.id;
      link.href=url;link.target="_blank";link.append(img);
      const open=node("a","下载原图");open.href=url;open.download="提示词出图-"+image.id+".png";
      const info = node("details", undefined, "gallery-info");
      info.append(node("summary", "生成详情"), node("p",`模型：${image.batch.checkpoint} · 种子：${image.seed}`));
      card.append(link,open);
      const panel = Object.values(project?.pages || {}).flatMap(page => page.panels)
        .find(p => p.id === image.batch.panel_id);
      if (panel && image.review?.verdict !== "pending") card.append(button("框选错误区域修复", () =>
        window.ensureDrawingTools().then(()=>window.openImageRepairDialog({root,img,image,project,panel})), "small"));
      card.append(info);
      if (image.review?.verdict && image.review.verdict !== "unreviewed") {
        const verdict = {pass:"通过",reject:"未通过",uncertain:"无法确认",pending:"核对中"}[image.review.verdict] || "待核对";
        card.append(node("p", "AI 核对：" + verdict + "（供参考）",
          ["reject","uncertain"].includes(image.review.verdict) ? "warning" : ""));
        if (image.review.reason) info.append(node("p", image.review.reason));
      }
      info.append(node("p", image.batch.references?.length
        ? "实际使用参考图：" + image.batch.references.map(r=>`${r.name} × ${r.weight}`).join("、")
        : "本张未使用角色参考图"));
      if(image.hand_detail) {
        const detail=image.hand_detail, status={done:"已完成局部细化",no_hands:"未检测到手部，保留原图",failed:"增强失败，保留原图",uncertain:"任务状态待确认，保留原图"};
        info.append(node("p","手部增强："+(status[detail.status]||detail.status)+(detail.error?" · "+detail.error:"")));
        const [baseBundle,baseFile]=detail.base_file.split("/"), baseLink=node("a","查看增强前原图");
        baseLink.href=`/api/image?id=${encodeURIComponent(thisProject)}&bundle=${encodeURIComponent(baseBundle)}&file=${encodeURIComponent(baseFile)}`;
        baseLink.target="_blank";info.append(baseLink);
      }
      if(image.batch.loras?.length) info.append(node("p","LoRA："+image.batch.loras.map(l=>`${l.label||l.file} × ${l.weight}`).join("、")));
      if(image.batch.consistency) info.append(node("p",image.batch.consistency.enabled
        ? `一致性：Anima Edit v2 × ${image.batch.consistency.weight} · ${image.batch.consistency.reference ? image.batch.consistency.character+"原图" : "无图像参考（"+image.batch.consistency.reference_reason+"）"}`
        : `一致性未启用：${image.batch.consistency.reason}`));
      viewer.replaceChildren(card);
      imageNumber.textContent = `${activeImage + 1} / ${images.length} 张`;
      previous.disabled = activeImage === 0; next.disabled = activeImage === images.length - 1;
      const first = Math.floor(activeImage / 8) * 8;
      thumbnails.replaceChildren();
      images.slice(first, first + 8).forEach((image, offset) => {
        const thumb = button("", () => {activeImage = first + offset; showImage();}, "gallery-thumb");
        thumb.setAttribute("aria-label", "查看第 " + (first + offset + 1) + " 张图片");
        thumb.setAttribute("aria-pressed", String(activeImage === first + offset));
        const img = node("img"); img.src = imageUrl(image); img.alt = "第 " + (first + offset + 1) + " 张";
        thumb.append(img); thumbnails.append(thumb);
      });
    }
    gallery.append(viewer, navigation, thumbnails);
    showImage();
  };
  const inputPane = node("section", undefined, "prompt-input workspace-pane");
  const introduction = root.children[1];
  inputPane.append(root.firstElementChild, introduction, positive.parentElement, negative.parentElement, languageNotice);
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
  topActions.append(clearInputs,undoClear, node("small", project?.quick_sample && !project.transient ? "已保存的排队作品 · 图片可随时回来查看" : "直接出图为临时预览；加入任务列表会自动保存作品和结果"));
  const draftStatus=node("small",draft?.persisted===false?"已恢复本页暂存；浏览器存储不可用，关闭或刷新前请复制提示词。":draft?"已恢复上次输入 · 草稿保存在当前浏览器":"提示词和设置会自动保存在当前浏览器","prompt-draft-status");
  draftStatus.classList.toggle("warning",draft?.persisted===false);
  draftStatus.setAttribute("role","status");topActions.append(draftStatus);
  inputPane.prepend(topActions);
  inputPane.append(conversionTools, conversionStatus, fields, matteLabel, handLabel, consistencyLabel, consistencyNote);
  const finalPreview = node("details", undefined, "prompt-preview");
  finalPreview.open = false;
  finalPreview.append(node("summary", "最终提示词预览 / 出图说明"), preview.parentElement, handNote,
    node("small", "一次 1–16 张，角色参考图按勾选启用。LoRA 触发词需填入提示词，不自动审核或翻译。"));
  inputPane.append(finalPreview,taskNameLabel);
  const formScroll=node("div",undefined,"prompt-form-scroll");
  formScroll.append(...inputPane.childNodes);
  const submitActions=node("div",undefined,"prompt-submit-actions");submitActions.append(start,enqueue);
  const queuedReceipt=node("div",undefined,"prompt-queue-receipt"),queuedReceiptText=node("small");queuedReceipt.hidden=true;queuedReceipt.setAttribute("role","status");
  queuedReceipt.append(queuedReceiptText,button("查看列表 / 安排开始",()=>window.openStudioQueue()));submitActions.append(queuedReceipt);
  const submitSummary=node("small");submitActions.prepend(submitSummary);
  const updateSubmitSummary=()=>{
    const missingModel=!model.value||!!model.selectedOptions[0]?.disabled,emptyPrompt=!positive.value.trim();
    let sizeText,invalidSize=false;try{sizeText=imageSize.summary();}catch(error){sizeText=error.message;invalidSize=true;}
    submitSummary.textContent=`${model.selectedOptions[0]?.textContent||"请选择模型"} · ${count.value} 张 · ${sizeText}`+(missingModel?" · 请选择已下载完整的模型":emptyPrompt?" · 先写下想画的画面":"");
    start.dataset.unavailable=enqueue.dataset.unavailable=String(missingModel||emptyPrompt||invalidSize);
    start.disabled=missingModel||emptyPrompt||invalidSize||!!window.activeStudioJob||start.hasAttribute("aria-busy");
    enqueue.disabled=missingModel||emptyPrompt||invalidSize||enqueue.hasAttribute("aria-busy");
  };
  for(const field of [model,count,...imageSize.controls])field.addEventListener(field.tagName==="INPUT"?"input":"change",updateSubmitSummary);updateSubmitSummary();
  inputPane.append(formScroll,submitActions);
  start.classList.add("prompt-start");
  const libraryPane = node("div", undefined, "prompt-library");
  characterBox.classList.add("workspace-pane"); loraBox.classList.add("workspace-pane", "prompt-loras");
  libraryPane.append(characterBox, loraBox);
  root.replaceChildren(inputPane, libraryPane, gallery);
  const draftInput=()=>({text:positive.value,negative:negative.value,checkpoint:model.value,count:Number(count.value),...imageSize.draft(),
      task_name:taskName.value,regional_characters:regional.checked,reference_weight:strength.value,hand_detail:hand.checked,consistency_lora:consistency.checked,delta_matte_skin:matte.checked,
      characters:characterRows.filter(r=>r.check.checked||r.prompt.value!==r.defaultPrompt||r.region.value!=="null"||(!animaActive&&!r.useReference.checked)).map(r=>({name:r.card.name,revision:r.card.revision,selected:r.check.checked,prompt:r.prompt.value,
        region:JSON.parse(r.region.value),use_reference:r.useReference.checked})),
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
