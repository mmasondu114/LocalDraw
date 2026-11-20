"use strict";

async function openDirectImport(kind, initialProject, initialText = "") {
  const story = kind === "story";
  const title = story ? "直接导入分镜" : "直接导入每格提示词";
  const dialog = node("dialog", undefined, "exchange-preview");
  dialog.setAttribute("aria-label", title);
  const status = node("p"), preview = node("div", undefined, "exchange-preview-list");
  status.setAttribute("role", "status");
  dialog.append(node("h2", title));
  const project = node("select");
  if (!story) {
    const projects = await api("/api/projects");
    project.append(node("option", "新建逐格出图作品"));
    project.firstChild.value = "";
    for (const item of projects) {
      const option = node("option", item.title);
      option.value = item.id;
      project.append(option);
    }
    project.value = initialProject || "";
    const label = node("label", "目标作品");
    label.append(project);
    const destination = node("details");
    destination.open = Boolean(initialProject);
    destination.append(node("summary", "使用已有分镜"), label);
    dialog.append(destination);
  }
  const input = node("input"), paste = node("textarea"), base = node("input");
  input.type = base.type = "file";
  input.accept = base.accept = ".json,application/json";
  if (!story) input.accept = ".txt,.json,text/plain,application/json";
  paste.rows = 4;
  paste.placeholder = story ? "粘贴分镜 JSON" : '[{"id":"P001-K01","visual_prompt":"雨夜街口，远景","negative":"水印，多余文字"}]';
  const fileLabel = node("label", story ? "选择 JSON 文件" : "选择提示词文件（TXT / JSON）"), pasteLabel = node("label", story ? "或粘贴 JSON" : "粘贴逐格 JSON，或每行一条提示词");
  fileLabel.append(input);
  pasteLabel.append(paste);
  if (story) dialog.append(fileLabel, pasteLabel);
  else {
    const files = node("details");
    files.append(node("summary", "从文件导入"), fileLabel);
    dialog.append(pasteLabel, files);
    const copyFormat=button("复制给 AI 的 JSON 格式",async()=>{
      const sample=JSON.stringify([panelJson("P001-K01","第1页第1格的正面提示词原文","第1页第1格的负面提示词原文")],null,2);
      await copyPromptText("按漫画阅读顺序输出 JSON 数组，每格一项。id 用 P001-K01 表示第1页第1格，下一页从 P002-K01 开始。逐字保留我的正面和负面提示词，缺少负面时填空字符串。只输出 JSON。\n\n"+sample);
      status.textContent="已复制给 AI 的格式要求和示例。";
    });
    dialog.append(copyFormat);
  }
  let promptRows = [];
  if (story) {
    const pair = node("details"), label = node("label", "与草稿配对的 project.json");
    label.append(base);
    pair.append(node("summary", "附上原作品 project.json（分镜草稿或旧版导出）"), label);
    dialog.append(pair, node("p", "分镜将保存为新作品。可直接导入完整分镜 JSON，或为 bundle.json 附上生成时的 project.json。"));
  }
  const controls = node("div", undefined, "dialog-actions");
  const check = node("button", "预览"), save = node("button", story ? "确认导入为新作品" : "导入并设置出图", "primary");
  const close = node("button", "取消");
  check.hidden = !story;
  controls.append(close, check, save);
  dialog.append(status, preview, controls);
  let timer, saved = null, revision = 0, verified = null, importKey = crypto.randomUUID().replaceAll("-", ""), saving = false;
  const invalidate = () => {
    revision++;
    verified = null;
    promptRows = [];
    save.disabled = true;
    preview.replaceChildren();
    status.textContent = "";
  };
  invalidate();
  const changed = () => {
    check.hidden = !story;
    clearTimeout(timer); saved = null; importKey = crypto.randomUUID().replaceAll("-", ""); invalidate();
    if (!story && (input.files.length || paste.value.trim())) timer = setTimeout(() => check.onclick(), 180);
  };
  for (const control of [base, project]) control.onchange = changed;
  paste.oninput = () => {input.value = ""; changed();};
  input.onchange = () => {paste.value = ""; changed();};
  close.onclick = () => dialog.close();
  dialog.oncancel = event => {if (saving) event.preventDefault();};
  dialog.onclose = () => {clearTimeout(timer); revision++; dialog.remove();};
  async function readFile(file, limit) {
    if (file.size > limit) throw Error("JSON 文件过大，请分批导入。");
    return file.text();
  }
  async function flushEdits() {
    await preserve();
    clearTimeout(workspaceTimer);
    if (workspaceProject() && stage !== "prompt")
      await api("/api/action", {action: "save_workspace", project: workspaceProject(), state: structuredClone(workspaceState)});
  }
  check.onclick = async () => {
    invalidate();
    const version = revision;
    try {
      const doc = input.files.length ? await readFile(input.files[0], 5000000) : paste.value;
      if (!doc.trim()) throw Error("请选择 JSON 文件或粘贴内容。");
      const pasted = doc.trim().replace(/^```(?:json|text)?\s*\n([\s\S]*?)\n```$/i, "$1").trim();
      const isText = !story && (input.files.length ? /\.txt$/i.test(input.files[0].name) : !/^(?:\[\s*(?:["{\]]|$)|\{\s*(?:["}]|$))/.test(pasted));
      const documentText = input.files.length || story ? doc : pasted;
      const request = {document: isText ? JSON.stringify(documentText.split(/\r?\n/).filter(line => line.trim())) : documentText, project: project.value};
      if (isText && project.value) throw Error("已有分镜请导入带画格编号的 JSON，或选择新建作品。");
      if (story && base.files.length) request.base_project = await readFile(base.files[0], 5000000);
      if (project.value) await flushEdits();
      const data = await act(story ? "preview_direct_story" : "preview_direct_prompts", request);
      if (version !== revision || !dialog.open) return;
      if (story) {
        preview.append(node("strong", data.title), node("p", data.page_count + " 页 · " + data.panel_count + " 格"));
        for (const page of data.pages) {
          const details = node("details");
          details.append(node("summary", page.id + " · " + page.panels.length + " 格"));
          for (const panel of page.panels) details.append(node("p", panel.id + " · " + panel.main_instant));
          preview.append(details);
        }
        for (const warning of data.warnings) preview.append(node("p", warning, "warning"));
      } else {
        promptRows = data.panels;
        const panelCount = promptRows.length;
        status.textContent = "已识别 " + panelCount + " 格。下一步选择角色、LoRA 和张数。";
        const list = node("details");
        list.append(node("summary", "查看 " + panelCount + " 格提示词"));
        preview.append(list);
        if (data.mapped_from) preview.append(node("p", "来源作品 " + data.mapped_from + "。已逐格核对编号和分镜摘要，确认后写入当前所选作品。"));
        if (data.replace.length) preview.append(node("p", "确认后将替换这些格的已有提示词：" + data.replace.join("、"), "warning"));
        for (const row of data.panels) {
          const details = node("details");
          details.append(node("summary", row.panel_id + (row.summary ? " · " + row.summary : "")),
            node("p", "正向：" + row.positive), node("p", "负向：" + row.negative));
          list.append(details);
        }
      }
      verified = {...request, preview_hash: data.preview_hash};
      save.disabled = false;
    } catch (error) {
      if (version === revision) status.textContent = error.message;
    }
  };
  save.onclick = async () => {
    if (saving || !verified) return;
    const request = verified;
    saving = true;
    for (const control of [input, paste, base, project, check, close, save]) control.disabled = true;
    try {
      await flushEdits();
      const result = saved || await act(story ? "save_direct_story" : "save_direct_prompts",
        {...request, confirm: true, import_key: importKey});
      saved = result;
      if (!story) {
        status.textContent = "提示词已保存，正在打开出图设置…";
        await openImportedSetup(result.project, result.bundle, promptRows.map((row, i) => ({
          ...row, panel_id:result.targets[i], source:row.source || row.panel_id
        })));
        dialog.close();
        return;
      }
      dialog.close();
      await openProject(result.project, true, "page");
      window.leaveStudioHome?.();
      notify("已导入分镜。");
    } catch (error) {
      if (dialog.open) {status.textContent = error.message; if (!saved) {verified = null; check.hidden = false;}}
      else notify(error.message, true);
    } finally {
      saving = false;
      for (const control of [input, paste, base, project, check, close]) control.disabled = false;
      save.disabled = !verified;
    }
  };
  document.body.append(dialog);
  dialog.showModal();
  if (!story) {
    paste.value = initialText;
    paste.focus();
    if (initialText) changed();
  }
}

window.openDirectStoryboardImport = () => openDirectImport("story");
window.openDirectPromptImport = (project, text) => openDirectImport("prompts", project, text);
