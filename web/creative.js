"use strict";
let characterCache = [],
  characterFields = {};
const characterListFields = {
  observed: "图像中可见的特征",
  suggested: "AI 建议补充（并非原作事实）",
  unknowns: "未知 / 需确认的内容",
};
function characterMatchesSearch(card, query) {
  const q = normalizeLoraSearch(query);
  return !q || [card.name, card.pinyin || "", ...(card.english_aliases || []), ...(card.search_terms || [])]
    .some(term => normalizeLoraSearch(term).includes(q));
}
function characterProfileText(profile) {
  return Object.entries({ ...characterFields, ...characterListFields })
    .map(([key, label]) => {
      const value = profile[key];
      return `## ${label}\n${Array.isArray(value) ? value.join("\n") : value || ""}`;
    })
    .join("\n\n");
}
function parseCharacterText(text) {
  const fields = { ...characterFields, ...characterListFields };
  const headings = new Map(
    Object.entries(fields).map(([key, label]) => [`## ${label}`, key]),
  );
  const sections = {};
  let current = null;
  for (const line of text.split(/\r?\n/)) {
    const key = headings.get(line.trim());
    if (key) {
      if (Object.hasOwn(sections, key)) return null;
      current = key;
      sections[key] = [];
    } else if (current) sections[current].push(line);
    else if (line.trim()) return null;
  }
  if (Object.keys(fields).some((key) => !Object.hasOwn(sections, key)))
    return null;
  return Object.fromEntries(
    Object.entries(sections).map(([key, lines]) => [
      key,
      Object.hasOwn(characterListFields, key)
        ? lines.map((line) => line.trim()).filter(Boolean)
        : lines.join("\n").trim(),
    ]),
  );
}
async function loadCharacters() {
  const r = await api("/api/characters");
  characterCache = r.characters;
  characterFields = r.fields;
  return r;
}
function labeledInput(root, label, multiline = true, initial = "") {
  const wrap = node("label", label),
    input = node(multiline ? "textarea" : "input");
  input.value = initial;
  input.dataset.editKey = "field-" + label;
  input.setAttribute("aria-label", label);
  if (multiline) input.rows = 3;
  wrap.append(input);
  root.append(wrap);
  return input;
}
async function waitCreativeJob(jobId, status) {
  for (;;) {
    const j = await api("/api/job?id=" + encodeURIComponent(jobId));
    status.replaceChildren(jobProgress(j));
    if (j.status === "done") return j.result;
    if (j.status !== "running") throw Error(j.message);
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
}
window.openCharacterLibrary = async function (onSaved, resumeName = null) {
  await loadCharacters();
  const body = $("dialog-body");
  body.replaceChildren(node("h2", "角色库"));
  const list = node("div", undefined, "toolbar"),
    form = node("div");
  list.classList.add("character-library-list");
  const search = node("input");
  search.type = "search";
  search.placeholder = "搜索角色：中文名、拼音、英文别名";
  search.setAttribute("aria-label", "搜索角色库");
  const entries = [];
  const empty = node("p", "没有匹配的角色", "character-empty");
  empty.hidden = true;
  search.addEventListener("input", () => {
    for (const {card, entry} of entries) entry.hidden = !characterMatchesSearch(card, search.value);
    empty.hidden = entries.some(({entry}) => !entry.hidden);
  });
  body.append(search, list, empty, form);
  function edit(card = null) {
    workspaceState.characterEditor={name:card?.name||null,open:true};
    form.replaceChildren();
    let imageId = card?.image_id || null,
      revision = card?.revision || 0;
    const name = labeledInput(
      form,
      "角色名（例如：林晴）",
      false,
      card?.name || "",
    );
    name.readOnly = !!card;
    const typeLabel = node("label", "建档方式"),
      type = node("select");
    [
      ["image", "图片＋性格"],
      ["text", "详细文字"],
    ].forEach(([v, t]) => {
      const o = node("option", t);
      o.value = v;
      type.append(o);
    });
    type.value = card?.source.mode || "image";
    typeLabel.append(type);
    form.append(typeLabel);
    const imageBox = node("div"),
      upload = node("input"),
      preview = node("img");
    upload.type = "file";
    upload.accept = "image/png,image/jpeg,image/webp";
    upload.setAttribute("aria-label", "角色参考图");
    preview.className = "character-preview";
    preview.hidden = !imageId;
    if (imageId) preview.src = "/api/character-image?id=" + imageId;
    imageBox.append(
      node("p", "上传一张主体清楚的角色图片（最多 10 MB）。图片仅保存在本机。"),
      upload,
      preview,
    );
    form.append(imageBox);
    const material = labeledInput(
      form,
      "角色资料（在这里填写、查看和修改）",
      true,
      card ? characterProfileText(card.profile) : "",
    );
    material.rows = 20;
    material.placeholder =
      "把外貌、性格、说话习惯、背景和补充说明都写在这里，也可以直接粘贴已有资料。图片建档时请写明性格，AI 会从参考图识别外观。生成后仍在这个框里修改和确认。";
    function keepCharacter(){
      const draft={name:name.value,text:material.value,type:type.value,imageId,revision,source:generatedSource};
      cacheLocal("character-edit-"+(card?.name||"new"),JSON.stringify(draft));
      workspaceState.characterDrafts ||= {};workspaceState.characterDrafts[card?.name||"new"]=draft;
      window.saveWorkspace?.();
    }
    function typeChange() {
      imageBox.hidden = type.value !== "image";
    }
    type.onchange = typeChange;
    typeChange();
    upload.onchange = safe(async () => {
      const file = upload.files[0];
      if (!file) return;
      if (file.size > 10000000) throw Error("图片不能超过 10 MB");
      const data = await new Promise((resolve, reject) => {
        const r = new FileReader();
        r.onload = () => resolve(r.result);
        r.onerror = reject;
        r.readAsDataURL(file);
      });
      const result = await act("character_upload", { data_url: data });
      imageId = result.image_id;
      preview.src = "/api/character-image?id=" + imageId;
      preview.hidden = false;
      keepCharacter();
    });
    const status = node(
      "p",
      "先生成可修改的草稿，再确认保存。所有资料都在上方一个框里编辑。",
    );
    let generatedSource = card?.source;
    async function draftCharacter() {
      generate.disabled = true;
      save.disabled = true;
      material.readOnly = true;
      try {
        const r = await act("character_draft", {
          name: name.value,
          mode: type.value,
          image_id: imageId,
          text: material.value,
          personality: type.value === "image" ? material.value : "",
        });
        const result = await waitCreativeJob(r.job, status);
        generatedSource = result.source;
        material.value = characterProfileText(result.profile);
        keepCharacter();
        status.textContent =
          "草稿已放回上方资料框。可直接修改各段内容，核对后确认保存。";
      } finally {
        generate.disabled = false;
        save.disabled = false;
        material.readOnly = false;
      }
    }
    const generate = button(
      "让 AI 识别 / 整理详细角色卡",
      draftCharacter,
      "primary",
    );
    const save = button(
      "确认保存角色",
      async () => {
        const profile = parseCharacterText(material.value);
        if (!profile) {
          await draftCharacter();
          status.textContent =
            "已将整段文字整理成角色草稿，请在上方核对后再次确认保存。";
          return;
        }
        const result = await act("character_save", {
          name: name.value.trim(),
          profile,
          image_id: imageId,
          base_revision: revision,
          source: {
            ...generatedSource,
            mode: type.value,
            text: generatedSource?.text || material.value,
            personality: profile.personality,
          },
        });
        revision = result.revision;
        keepCharacter();
        name.readOnly = true;
        await loadCharacters();
        status.textContent = `已保存 ${result.name} · 版本 ${revision}。已引用旧版的作品不会被自动修改。`;
        if (onSaved) onSaved(result);
      },
      "primary",
    );
    form.append(generate, status, save);
    const draftKey=card?.name||"new";
    let pending=workspaceState.characterDrafts?.[draftKey];
    try{pending ||= JSON.parse(localStorage.getItem("character-edit-"+draftKey)||"null");}catch{}
    if(pending && pending.revision===revision){
      name.value=pending.name;material.value=pending.text;type.value=pending.type;imageId=pending.imageId;generatedSource=pending.source;
      typeChange();preview.hidden=!imageId;if(imageId)preview.src="/api/character-image?id="+imageId;
      status.textContent="已恢复上次未提交的角色编辑。";
    }
    form.addEventListener("input",keepCharacter);form.addEventListener("change",keepCharacter);

    if (card)
      form.append(
        button("查看历史版本", async () => {
          const history = await api(
            "/api/character-history?name=" + encodeURIComponent(card.name),
          );
          const h = node("div");
          for (const item of history) {
            h.append(
              node("h3", `版本 ${item.revision}`),
              readObject(item.profile),
              button("用此版内容继续编辑", () => {
                material.value = characterProfileText(item.profile);
                generatedSource = item.source;
                imageId = item.image_id;
                type.value = item.source.mode;
                typeChange();
                preview.hidden = !imageId;
                if (imageId) preview.src = "/api/character-image?id=" + imageId;
                status.textContent = "历史内容已填入，点击保存会建立新版本。";
                keepCharacter();
              }),
            );
          }
          form.append(h);
        }),
      );
  }
  list.append(button("＋ 新建角色", () => edit()));
  for (const card of characterCache) {
    const entry=button(card.name, () => edit(card));entry.dataset.characterName=card.name;list.append(entry);
    entries.push({card, entry});
  }
  edit(characterCache.find(c=>c.name===resumeName)||null);
  if (!$("dialog").open) $("dialog").showModal();
  window.saveWorkspace?.();
};
window.renderStoryStart = async function (root, project) {
  const renderToken = root.dataset.renderToken;
  await loadCharacters();
  if (root.dataset.renderToken !== renderToken) return;
  root.replaceChildren();
  root.className = "story-studio";
  root.append(
    node("h2", project?.script ? "已确认剧本" : "粘贴故事，就从这里开始"),
    node(
      "p",
      "粘贴英文故事可以整理成剧本。已有绘图提示词，每行粘贴一格，再点「下一步 · 按提示词出图」。",
    ),
  );
  const chars = node("div", undefined, "toolbar");
  root.append(button("已有完整分镜？导入剧本 JSON", () => window.openStoryImport()));
  chars.classList.add("character-library-list");
  const characterSearch = node("input");
  characterSearch.type = "search";
  characterSearch.placeholder = "搜索角色：中文名、拼音、英文别名";
  characterSearch.setAttribute("aria-label", "搜索故事角色");
  const picker = node("details"), pickerTitle = node("summary", "角色");
  picker.append(pickerTitle, characterSearch, chars);
  root.append(picker);
  const text = labeledInput(
    root,
    "故事或逐格提示词",
    true,
    project?.script || "",
  );
  text.rows = 13;
    text.id="story-source-text";text.parentElement.htmlFor=text.id;
  text.dataset.editKey = "story-text";
    text.placeholder =
      "例如：After closing her bookshop, a young woman finds a paper boat at the door. Inside is a note from an old friend...";
  text.oninput = () => cacheLocal("comic-story-input", text.value);
    const textCaption=node("span",undefined,"story-text-caption");textCaption.append(text.parentElement.firstChild,button("展开编辑",()=>{
      const dialog=node("dialog",undefined,"story-editor-dialog");dialog.setAttribute("aria-label","展开编辑故事");
      const top=node("div",undefined,"queue-heading");top.append(node("h2","专心写好这个故事"),button("取消",()=>dialog.close()));
      const editor=node("textarea");editor.setAttribute("aria-label","完整故事文本");editor.value=text.value;editor.placeholder=text.placeholder;editor.spellcheck=false;
      const count=node("small");const updateCount=()=>{count.textContent=`${Array.from(editor.value).length} 个字符 · 完成编辑后才会替换左侧故事文本`;};editor.oninput=updateCount;updateCount();
      const apply=button("完成编辑",()=>{text.value=editor.value;text.dispatchEvent(new Event("input",{bubbles:true}));dialog.close();notify("故事文本已更新，输入草稿已保留。");},"primary");
      dialog.append(top,editor,count,apply);dialog.onclose=()=>dialog.remove();document.body.append(dialog);dialog.showModal();editor.focus();
    },"small"));textCaption.querySelector("button").setAttribute("aria-label","展开编辑故事文本");text.parentElement.prepend(textCaption);
  const drawPasted = button("下一步 · 按提示词出图", () => window.openDirectPromptImport("", text.value), "primary");
  root.append(drawPasted);
  const defaults = (workspaceState.storyResult?.payload?.brief || project?.brief)?.character_refs || [];
  const selected = new Set(workspaceState.storyCharacters ?? defaults.map(card => card.name));
  const selectedNames = () => [...selected];
  const updateSelection = () => {
    pickerTitle.textContent = selected.size ? "角色 · " + selectedNames().join("、") : "角色";
  };
  function chips() {
    chars.replaceChildren(button("管理角色", () => window.openCharacterLibrary(chips)));
    const matches = characterCache.filter(card => characterMatchesSearch(card, characterSearch.value));
    for (const card of matches) {
      const label = node("label"), box = node("input");
      box.type = "checkbox"; box.checked = selected.has(card.name);
      box.onchange = () => {
        if (box.checked) selected.add(card.name); else selected.delete(card.name);
        workspaceState.storyCharacters = selectedNames();
        updateSelection();
      };
      label.append(box, node("span", card.name));
      chars.append(label);
    }
    if (!matches.length) chars.append(node("p", "没有匹配的角色", "character-empty"));
  }
  picker.addEventListener("toggle", () => {if (picker.open) chips();});
  characterSearch.addEventListener("input", chips);
  updateSelection();
  const modeLabel = node("label", "想让 AI 帮你做什么"),
    mode = node("select");
  [
    ["structure", "整理英文场景，完整保留原文细节"],
    ["expand", "用英文扩写细节、动作和对白"],
    ["continue", "保留英文原文并续写后续"],
  ].forEach(([v, t]) => {
    const o = node("option", t);
    o.value = v;
    mode.append(o);
  });
  modeLabel.append(mode);
  mode.dataset.editKey = "story-mode";
  root.append(modeLabel);
  root.append(node("p", "请粘贴英文原文。整理模式完整保留原文，只添加英文场景标题并提取英文资料；扩写、续写、分镜描述和对白也统一使用英文。"));
  const preferences = labeledInput(
    root,
    "可选要求（页数、风格、结局等，留空由 AI 建议）",
    false,
  );
  const status = node("p"),
    review = node("section", undefined, "story-review workspace-pane");
  review.append(node("h2", "AI 改好的剧本"), node("p", "整理后的草稿会显示在这里，确认后即可继续分镜。", "story-empty"));
    if(!project?.script){
      const guide=node("section",undefined,"story-start-guide");guide.append(node("h3","第一次创作，跟着这三步"));
      const steps=node("ol");for(const [title,description] of [["写下故事","左侧粘贴英文原文，写清人物、地点和发生的事情。"],["选择要保留的内容","整理模式保留原文；想加细节就选扩写。角色和 LoRA 都是可选项。"],["核对后进入分镜","AI 草稿会显示在这里。可改标题、页数和文字，确认后再画分镜。"]]){const step=node("li");step.append(node("strong",title),node("p",description));steps.append(step);}guide.append(steps);
      guide.append(node("h3","没有故事？先试一个"));
      const examples=node("div",undefined,"story-starter-examples");
      for(const [label,story,pref] of [
        ["雨夜重逢","After closing her small cafe, Maya notices a man waiting in the rain. It is Leo, an old friend she has not seen in years. He is holding the umbrella she lent him on their last day of school. Maya laughs and opens the cafe again. Over two cups of tea, they finally finish the conversation they left unfinished.","Two pages, four panels per page. A quiet, warm reunion between two adults. Keep the gentle ending."],
        ["书店奇遇","When Nora opens her second-hand bookshop, she finds a tiny paper boat on the counter. A note inside says, 'Look where the sun sets.' Following the evening light, she discovers a forgotten box of letters behind a shelf. The letters were written by the shop's first owner. Nora puts the first letter in the window, inviting its recipient to come home.","Two pages, four panels per page. A gentle mystery in a cozy bookshop. Keep the paper boat as a recurring visual detail."],
        ["轻松日常","Two adult roommates, Sam and Alex, decide to bake a birthday cake for their neighbor. Sam carefully follows the recipe while Alex improvises every decoration. The cake tilts, the icing slips, and their kitchen becomes a mess. Their neighbor arrives early and laughs. They finish decorating it together, turning the crooked cake into a tiny mountain village.","Two pages, four panels per page. Lighthearted comedy with clear visual actions and a warm ending."]
      ])examples.append(button(label,()=>{
        if(text.value.trim()){notify("已保留当前故事，未替换。新建作品可以试用其他示例。");return;}
        text.value=story;if(!preferences.value.trim())preferences.value=pref;text.dispatchEvent(new Event("input",{bubbles:true}));preferences.dispatchEvent(new Event("input",{bubbles:true}));text.focus();notify("示例已填入，可以自由修改，再点击整理剧本。");
      }));guide.append(examples,node("small","示例只填入文字，不会自动运行模型。"));review.append(guide);
    }
  preferences.dataset.editKey = "story-preferences";
  const generate = button(
    "让 AI 整理剧本",
    async () => {
      generate.disabled = true;
      try {
        const r = await act("story_draft", {
          text: text.value,
          character_names: selectedNames(),
          mode: mode.value,
          preferences: preferences.value,
        });
        const result = await waitCreativeJob(r.job, status);
        showResult(result);
      } finally {
        generate.disabled = false;
      }
    },
    "primary",
  );
  generate.dataset.startJob="true";
  const readiness=node("p",undefined,"story-readiness");
  const refreshStoryControls=()=>{
    if(root.dataset.renderToken!==renderToken)return;
    updateSelection();
    const length=Array.from(text.value.trim()).length;
    drawPasted.dataset.unavailable = String(!length);
    drawPasted.disabled = !length || drawPasted.hasAttribute("aria-busy");
    if(!generate.hasAttribute("aria-busy"))generate.textContent={structure:"让 AI 整理剧本",expand:"让 AI 扩写剧本",continue:"让 AI 续写故事"}[mode.value]||"让 AI 整理剧本";
    generate.dataset.unavailable=String(!length||!!window.studioDisconnected);generate.disabled=generate.dataset.unavailable==="true"||!!window.activeStudioJob||generate.hasAttribute("aria-busy");
    readiness.textContent=window.studioDisconnected?"后台暂时未连接，输入会保留，恢复后可继续。":length?`已输入 ${length} 个字符 · ${window.activeStudioJob?"当前任务结束后，可处理这个故事。":"准备好后，点击下方按钮生成草稿。"}`:"先粘贴故事，或点击右侧示例。填写后即可生成草稿。";
  };
  window.refreshStoryControls=refreshStoryControls;text.addEventListener("input",refreshStoryControls);mode.addEventListener("change",refreshStoryControls);refreshStoryControls();
  root.append(
    readiness,
    generate,
    status,
    button("找回输入草稿", async () => {
      const drafts = await api("/api/input-drafts");
      review.replaceChildren(node("h3", "之前的新作品输入"));
      if (!drafts.length) review.append(node("p", "暂无已存档的输入草稿。"));
      for (const d of drafts) {
        const fields = d.state?.views?.["script:"]?.fields || {};
        const content = fields["story-text"] || d.result?.payload?.story || "";
        if (!content) continue;
        review.append(button(`${d.id} · ${content.slice(0, 45)}`, () => {
          const body = $("dialog-body");
          body.replaceChildren(node("h2", "已保留的输入草稿"));
          const copy = labeledInput(body, "草稿全文（可复制）", true, content);
          copy.rows = 18;
          body.append(node("p", "复制需要的内容到故事文本框；不会覆盖当前输入。"));
          $("dialog").showModal();
        }));
      }
    }),
    button("找回未确认草稿", async () => {
      const drafts = await api("/api/creative-drafts");
      review.replaceChildren();
      for (const d of drafts.filter((x) => x.kind === "story_draft"))
        review.append(
          button(d.payload.brief.title + " · " + d.id.slice(0, 8), () =>
            showResult(d),
          ),
        );
    }),
    review,
  );
  const loraRoot = node("section");
  loraRoot.append(
    jobProgress({
      label: "读取 LoRA 列表",
      status: "running",
      message: "正在检查本地文件…",
      started_at: Date.now() / 1000,
    }),
  );
  const inputPane = node("section", undefined, "story-input workspace-pane");
  inputPane.append(...Array.from(root.children).filter(child => child !== loraRoot && child !== review));
  const settingsPane = node("section", undefined, "story-settings workspace-pane");
  settingsPane.setAttribute("aria-label", "绘图设置 · 尺寸与 LoRA");
  settingsPane.append(node("h2", "绘图设置 · 尺寸与 LoRA"), loraRoot);
  root.replaceChildren(inputPane, settingsPane, review);
  const generateWasDisabled = generate.disabled;
  generate.disabled = true;
  const loraControl = await window.renderLoraSettings(
    loraRoot,
    project,
    project ? pid : null,
  );
  if (root.dataset.renderToken !== renderToken) return;
  generate.disabled = generateWasDisabled || !!window.activeStudioJob;
  refreshStoryControls();
  if (project?.script)
    inputPane.append(
      button(
        "继续文字分镜",
        async () => {
          await preserve();
          stage = "page";
          target = "";
          renderStage();
        },
        "primary",
      ),
      node("p", "重新整理会另存为新作品，保留当前作品的分镜、选图和历史。"),
    );
  let cached = workspaceState.storyResult;
  try {cached ||= JSON.parse(localStorage.getItem("story-result-"+(newMode?"new":pid||"new"))||"null");}catch{}
  if (!cached) {
    // Recover a finished job automatically when its original input matches this
    // workspace. Never place an unrelated latest draft into a blank new work.
    const source = workspaceState.views?.["script:"]?.fields?.["story-text"] || text.value;
    if (source.trim()) {
      try {
        const jobs = (await api("/api/status")).jobs || [];
        cached = jobs.find(j => j.status === "done" && j.result?.payload?.story &&
          j.result.original?.trim() === source.trim())?.result;
      } catch {}
    }
  }
  if (root.dataset.renderToken !== renderToken) return;
  if (cached) showResult(cached);
  function showResult(result) {
    if (!workspaceState.storyCharacters) {
      selected.clear();
      for (const card of result.payload.brief.character_refs || []) selected.add(card.name);
      updateSelection();
      if (picker.open) chips();
    }
    review.replaceChildren(node("h2", "AI 改好的剧本"));
    const story = labeledInput(
      review,
      "AI 剧本草稿（可修改）",
      true,
      result.payload.story,
    );
    story.rows = 18;
    const title = labeledInput(
      review,
      "作品名称",
      false,
      result.payload.brief.title,
    );
    const pages = labeledInput(
      review,
      "页数",
      false,
      result.payload.brief.page_count,
    );
    pages.type = "number";
    pages.min = 1;
    pages.max = 80;
    const directionLabel = node("label", "阅读方向"),
      direction = node("select");
    for (const [v, t] of [
      ["LTR", "左到右"],
      ["RTL", "右到左"],
    ]) {
      const o = node("option", t);
      o.value = v;
      direction.append(o);
    }
    direction.dataset.editKey="draft-reading-direction";
    direction.value = result.payload.brief.reading_direction;
    directionLabel.append(direction);
    review.append(directionLabel);
    review.append(
      node("p", "自由分格：每页 1–9 格，可混合大格、小格、横向长格。"),
    );
    const detail = node("details");
    detail.append(
      node("summary", "AI 增补了什么 / 使用的角色资料"),
      readObject({
        assumptions: result.payload.assumptions,
        changes: result.payload.changes,
        characters: result.payload.canon.characters,
      }),
    );
    review.append(detail);
    const next = button(
        "下一步",
        async () => {
          // button() already disables the control before invoking this handler.
          next.textContent = "正在进入…";
          const approvedStory = story.value, approvedTitle = title.value;
          const approvedPages = Number(pages.value), approvedDirection = direction.value;
          for (const input of [story, title, pages, direction]) input.disabled = true;
          try {
            let confirmed = structuredClone(result);
            const names = selectedNames();
            const previous = (result.payload.brief.character_refs || []).map(c => c.name);
            if (approvedStory !== result.payload.story || names.length !== previous.length || names.some(name => !previous.includes(name))) {
              status.textContent = "正在同步角色和场景资料，完成后进入文字分镜…";
              const r = await act("story_draft", {
                text: approvedStory,
                character_names: names,
                mode: "structure",
                preferences: preferences.value + "；标题 " + approvedTitle + "；页数 " + approvedPages,
              });
              confirmed = await waitCreativeJob(r.job, status);
              // The user's edits remain the approved script; synchronization is
              // for the associated character/scene metadata only.
              confirmed.payload.story = approvedStory;
            }
            confirmed.payload.brief.title = approvedTitle;
            confirmed.payload.brief.page_count = approvedPages;
            confirmed.payload.brief.reading_direction = approvedDirection;
            await loraControl.flush();
            const r = await act("create_story", {
              result: confirmed,
              drawing_settings: loraControl.read(),
            });
            await openProject(r.project, true, "page");
            if (pid === r.project) {
              await act("storyboard_all", {project:r.project, base_hash:snap.hash});
              notify("剧本已确认，正在生成 POV 分镜。");
            }
          } finally {
            next.textContent = "下一步";
            for (const input of [story, title, pages, direction]) input.disabled = false;
          }
        },
        "primary",
      );
    next.title = "使用右侧剧本，进入文字分镜";
    review.append(next);
    story.focus({preventScroll:true});
    // Keep the result with the workspace, which is also saved on disk. Rendering
    // must not depend on writing a large, duplicate story-result-* browser cache.
    workspaceState.storyResult = result;
    saveWorkspace();
  }
};
