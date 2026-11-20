"use strict";
(() => {
  const entry = document.getElementById("drawing-model-entry");
  const dialog = node("dialog", undefined, "drawing-model-dialog");
  const title = node("h2", "绘图模型");
  title.id = "drawing-model-title";
  dialog.setAttribute("aria-labelledby", title.id);
  const label = node("label", "选择模型");
  const select = node("select");
  label.append(select);
  const current = node("p"), feedback = node("div");
  feedback.setAttribute("role", "status");
  const actions = node("div", undefined, "dialog-actions");
  const close = button("关闭", () => dialog.close());
  const toggle = node("button", "加载模型", "primary");
  let state, working = false, timer;
  function render() {
    const selectedIsLoaded = !!state?.loaded && select.value === state.loaded;
    current.textContent = state?.loaded ? "已加载 · " + (state.models.find(m => m.value === state.loaded)?.label || state.loaded)
      : state ? "当前未预加载模型" : "正在读取模型…";
    toggle.textContent = selectedIsLoaded ? "卸载模型" : state?.loaded ? "切换并加载" : "加载模型";
    select.disabled = !state?.models.length || working || !!state?.busy;
    toggle.disabled = select.disabled;
  }
  async function refresh() {
    clearTimeout(timer);
    try {
      const next = await api("/api/drawing-model");
      if (!state || JSON.stringify(next.models) !== JSON.stringify(state.models)) {
        const preferred = select.value || next.loaded || next.default;
        select.replaceChildren(...next.models.map(m => new Option(m.label, m.value)));
        if (next.models.some(m => m.value === preferred)) select.value = preferred;
      }
      state = next;
      if (!working) feedback.textContent = next.busy ? "任务进行中 · " + next.busy
        : !next.models.length ? "尚未找到模型，可先在「下载模型」中安装。" : feedback.dataset.error || "";
      render();
    } catch (err) {
      state = null;
      render();
      feedback.textContent = err.message;
    } finally {
      if (dialog.open && !working) timer = setTimeout(refresh, 2000);
    }
  }
  toggle.onclick = async () => {
    if (toggle.disabled) return;
    const unloading = select.value === state.loaded;
    working = true;
    clearTimeout(timer);
    feedback.dataset.error = "";
    feedback.textContent = unloading ? "正在卸载模型…" : "正在准备加载…";
    render();
    try {
      const {job} = await act(unloading ? "unload_drawing_model" : "preload_drawing_model", {checkpoint: select.value});
      await waitCreativeJob(job, feedback);
      catalogCache.delete("/api/drawing-options");
    } catch (err) {
      feedback.dataset.error = err.message;
    } finally {
      working = false;
      await refresh();
    }
  };
  select.onchange = render;
  actions.append(close, toggle);
  dialog.append(title, label, current, node("p", "提前加载底模后，使用同一模型连续出图可以复用。切换底模或启动分析模型会释放预加载。"), feedback, actions);
  document.body.append(dialog);
  entry.onclick = () => {
    dialog.showModal();
    if (!working) {
      state = null;
      select.replaceChildren();
      feedback.dataset.error = "";
      render();
      refresh();
    }
  };
  dialog.onclose = () => { clearTimeout(timer); entry.focus(); };
})();
