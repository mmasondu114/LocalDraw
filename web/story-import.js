"use strict";

window.openStoryImport = function () {
  const dialog = node("dialog", undefined, "exchange-preview");
  dialog.setAttribute("aria-label", "导入剧本 JSON");
  const upload = node("input"), status = node("p"), preview = node("div", undefined, "exchange-preview-list");
  upload.type = "file";
  upload.accept = ".json,application/json";
  upload.setAttribute("aria-label", "选择整部剧本 JSON 文件");
  status.setAttribute("role", "status");
  dialog.append(node("h2", "导入剧本 JSON"),
    node("p", "选择包含角色资料和全部分页分镜的 JSON 文件（最多 5 MB）。预览后将创建新作品，画面描述和对白原样保存。"),
    node("p", "支持 brief、canon、pages 数组格式。单页文件、绘图提示词和作品备份请勿在此导入。"),
    upload, status, preview);
  let documentText = null, result = null, importKey = null, selection = 0, busy = false;
  const confirm = node("button", "导入为新作品", "primary");
  confirm.type = "button";
  confirm.disabled = true;
  const close = button("取消", () => dialog.close());
  const controls = node("div", undefined, "dialog-actions");
  controls.append(close, confirm);
  dialog.append(controls);

  upload.onchange = async () => {
    const revision = ++selection;
    result = null; documentText = null; importKey = null;
    confirm.disabled = true;
    preview.replaceChildren();
    status.textContent = "";
    const file = upload.files[0];
    if (!file) return;
    try {
      if (!file.name.toLowerCase().endsWith(".json")) throw Error("请选择 .json 文件。");
      if (file.size > 5_000_000) throw Error("剧本 JSON 不能超过 5 MB。");
      status.textContent = "正在检查页号、角色引用、对白和前后页状态…";
      const text = await file.text();
      if (revision !== selection || !dialog.open) return;
      const data = await act("preview_story_import", {document: text});
      if (revision !== selection || !dialog.open) return;
      documentText = text; result = data; importKey = crypto.randomUUID().replaceAll("-", "");
      preview.append(node("h3", data.title),
        node("p", `${data.page_count} 页 · ${data.panel_count} 格 · ${data.characters.length} 位角色 · ${data.reading_direction === "RTL" ? "从右到左" : "从左到右"}`));
      for (const c of data.characters)
        preview.append(node("p", `${c.name}：${c.local_match ? "关联本地同名角色卡快照" : "使用文件中的角色资料"}`));
      preview.append(node("p", "同名角色会关联本地卡片；文件内的角色卡附件引用不直接载入。场景指定的服装仍以分镜为准。"));
      const names = Object.fromEntries(data.characters.map(c => [c.id, c.name]));
      for (const page of data.pages) {
        const section = node("details");
        section.append(node("summary", `第 ${page.number} 页 · ${page.panels.length} 格`));
        for (const panel of page.panels) {
          const card = node("article");
          card.append(node("h4", `${panel.id} · ${panel.shot}`), node("p", panel.main_instant), node("p", panel.drawing_description));
          const lines = page.lettering.filter(t => t.panel_id === panel.id).sort((a, b) => a.order - b.order);
          for (const t of lines) card.append(node("p", `${t.kind === "dialogue" ? names[t.speaker_id] : t.kind === "sfx" ? "拟声" : "旁白"}：${t.text}`));
          if (!lines.length) card.append(node("p", "无对白"));
          section.append(card);
        }
        preview.append(section);
      }
      if (data.warnings.length) {
        const warnings = node("details");
        warnings.append(node("summary", `${data.warnings.length} 项分镜提醒（可展开核对，不阻止导入）`));
        for (const warning of data.warnings) warnings.append(node("p", warning));
        preview.append(warnings);
      }
      status.textContent = "结构检查通过。导入后直接进入分镜编辑，不启动模型或绘图。";
      confirm.disabled = false;
    } catch (error) {
      if (revision !== selection || !dialog.open) return;
      result = null; documentText = null; importKey = null;
      confirm.disabled = true;
      status.textContent = error.message;
    }
  };
  confirm.onclick = async () => {
    if (!result || busy) return;
    busy = true; confirm.disabled = true; upload.disabled = true; close.disabled = true;
    try {
      // Flush the current workspace before switching; keep its input and edits recoverable.
      await preserve();
      clearTimeout(workspaceTimer);
      if (workspaceProject() && stage !== "prompt")
        await api("/api/action", {action: "save_workspace", project: workspaceProject(), state: structuredClone(workspaceState)});
      status.textContent = "正在保存新作品…";
      const saved = await act("save_story_import", {
        document: documentText, preview_hash: result.preview_hash, import_key: importKey, confirm: true,
      });
      status.textContent = "已导入，正在打开分镜…";
      confirm.textContent = "打开已导入作品";
      await openProject(saved.project);
      dialog.close();
      notify(`已导入《${result.title}》：${result.page_count} 页、${result.panel_count} 格。`);
    } catch (error) {
      status.textContent = error.message;
    } finally {
      busy = false; confirm.disabled = !result; upload.disabled = false; close.disabled = false;
    }
  };
  dialog.addEventListener("cancel", event => {if (busy) event.preventDefault();});
  dialog.onclose = () => {selection++; dialog.remove();};
  document.body.append(dialog);
  dialog.showModal();
};
