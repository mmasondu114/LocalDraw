import { app } from "../../../scripts/app.js";
import { api } from "../../../scripts/api.js";

const labels = {
    model_name: "绘图模型", lora_1: "LoRA 1", strength_1: "LoRA 1 强度",
    lora_2: "LoRA 2", strength_2: "LoRA 2 强度", lora_3: "LoRA 3", strength_3: "LoRA 3 强度",
    positive: "正向提示词", negative: "反向提示词", width: "宽度", height: "高度",
    batch_size: "每次张数", seed: "种子", steps: "步数（0 = 推荐）", cfg: "CFG（0 = 推荐）",
    sampler: "采样器", scheduler: "调度方式", control_after_generate: "生成后种子",
};

app.registerExtension({
    name: "local_draw.unified",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (!nodeData.name.startsWith("LocalDrawUnified")) return;
        const original = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            original?.apply(this, arguments);
            for (const widget of this.widgets ?? []) widget.label = labels[widget.name] ?? widget.name;
            if (nodeData.name !== "LocalDrawUnifiedModel") return;
            const node = this;
            const model = node.widgets.find(w => w.name === "model_name");
            const loraWidgets = node.widgets.filter(w => /^lora_[123]$/.test(w.name));
            let catalog;
            function filter(reset) {
                if (!catalog) return;
                let family = catalog.models[model.value]?.family;
                if (["anima", "anima38", "delta"].includes(family)) family = "anima";
                const choices = [catalog.none, ...Object.entries(catalog.loras)
                    .filter(([, f]) => f === family).map(([name]) => name)];
                let changed = false;
                for (const widget of loraWidgets) {
                    widget.options.values = choices;
                    if (reset && !choices.includes(widget.value)) { widget.value = catalog.none; changed = true; }
                }
                if (changed) app.extensionManager?.toast?.add({severity: "info", summary: "已切换模型", detail: "不兼容的 LoRA 已关闭，请重新选择。", life: 5000});
                node.setDirtyCanvas(true, true);
            }
            const callback = model.callback;
            model.callback = function () { callback?.apply(this, arguments); filter(true); };
            api.fetchApi("/local_draw_unified/catalog").then(r => {
                if (!r.ok) throw new Error(`catalog HTTP ${r.status}`);
                return r.json();
            }).then(data => { catalog = data; filter(false); }).catch(error => console.error("LoRA filter:", error));
        };
    },
});
