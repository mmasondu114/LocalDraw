"use strict";
(() => {
  const entry = document.getElementById("model-download-entry");
  const dialog = node("dialog", undefined, "model-download-dialog");
  dialog.setAttribute("aria-labelledby", "model-download-title");
  const heading = node("div", undefined, "queue-heading");
  const title = node("h2", "下载模型");
  title.id = "model-download-title";
  heading.append(title, button("关闭", () => dialog.close()));
  const status = node("p");
  status.setAttribute("role", "status");
  const list = node("div");
  dialog.append(heading, node("p", "提示词出图需要前三个文件。已有模型可放入对应目录，工作室会自动校验。"), status, list);
  document.body.append(dialog);
  const controls = new Map();
  const gib = bytes => (bytes / 1024 ** 3).toFixed(2) + " GiB";
  function addRow(item) {
    const row = node("section", undefined, "model-file");
    const name = node("h3", item.label), path = node("code", item.path);
    const detail = node("p"), progress = node("progress");
    progress.setAttribute("aria-label", item.label + "进度");
    progress.max = item.size;
    const download = button("下载", async () => {
      await act("download_model_asset", {asset:item.id});
      await refresh();
    }, "primary");
    const open = button("打开模型目录", () => act("open_model_folder", {asset:item.id}));
    open.setAttribute("aria-label", "打开模型目录：" + item.label);
    const actions = node("div", undefined, "dialog-actions");
    actions.append(download, open);
    row.append(name, node("small", item.use + " · " + gib(item.size)), path, detail, progress, actions);
    list.append(row);
    const fields = {detail, progress, download};
    controls.set(item.id, fields);
    return fields;
  }
  let timer, reading = false;
  async function refresh() {
    if (reading) return;
    clearTimeout(timer);
    reading = true;
    try {
      const {items} = await api("/api/model-downloads");
      const missing = items.filter(item => !item.ready).length;
      entry.textContent = missing ? "下载模型 · " + missing : "模型管理";
      entry.classList.toggle("primary", missing > 0);
      status.textContent = "已就绪 " + (items.length - missing) + " / " + items.length;
      if (dialog.open) for (const item of items) {
        const {detail, progress, download} = controls.get(item.id) || addRow(item);
        detail.textContent = item.error || item.state + (item.busy ? " · " + gib(item.done) + " / " + gib(item.size) : "");
        progress.hidden = !item.busy;
        progress.value = item.done;
        download.dataset.unavailable = String(item.busy || item.ready);
        download.disabled = item.busy || item.ready;
        download.textContent = item.ready ? "已就绪" : item.busy ? item.state : item.error ? "重试" : item.state === "可续传" ? "继续下载" : "下载";
      }
    } catch (e) { status.textContent = "模型列表读取失败：" + e.message; }
    finally { reading = false; timer = setTimeout(refresh, dialog.open ? 1200 : 20000); }
  }
  entry.onclick = () => {dialog.showModal(); refresh();};
  dialog.onclose = () => entry.focus();
  refresh();
})();
