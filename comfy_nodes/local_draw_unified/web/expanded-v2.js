import { app } from "../../../scripts/app.js";
import { api } from "../../../scripts/api.js";

// This extension edits ordinary graph nodes. It never performs inference.
const TYPE = "LocalDrawExpandedControls";
const labels = { model_name: "模型", steps: "步数（0 = 推荐）", cfg: "CFG（0 = 推荐）",
    width: "宽度", height: "高度", batch_size: "每次张数",
    lora_1: "LoRA 1", lora_2: "LoRA 2", lora_3: "LoRA 3",
    strength_1: "LoRA 1 强度", strength_2: "LoRA 2 强度", strength_3: "LoRA 3 强度" };
let catalog, templates;
const ready = Promise.all([
    api.fetchApi("/local_draw_unified/catalog").then(r => { if (!r.ok) throw Error("模型目录读取失败"); return r.json(); }),
    fetch(new URL("./expanded-templates.json", import.meta.url), {cache: "no-store"}).then(r => { if (!r.ok) throw Error("完整节点模板读取失败"); return r.json(); }),
]).then(([c, t]) => { catalog = c; templates = t; });
const widget = (node, name) => node?.widgets?.find(w => w.name === name);
const role = name => app.graph?._nodes.find(n => n.properties?.ld_role === name);
const control = () => app.graph?._nodes.find(n => n.type === TYPE);
const get = (n, k) => widget(n, k)?.value;
function put(n, k, value) { const w = widget(n, k); if (w) w.value = value; }
function family(c) { return catalog?.models[get(c, "model_name")]?.family; }
function loraChoices(c) {
    let f = family(c);
    if (["anima", "anima38", "delta"].includes(f)) f = "anima";
    return Object.entries(catalog.loras).filter(([, v]) => v === f).map(([n]) => n);
}
function filter(c) {
    const choices = loraChoices(c);
    for (let i = 1; i <= 3; i++) {
        widget(c, `lora_${i}`).options.values = [catalog.none, ...choices];
        const w = widget(role(`lora_${i}`), "lora_name");
        if (w) w.options.values = choices;
    }
}
function apply(c, changed) {
    if (!catalog || !family(c)) return;
    const defaults = catalog.defaults[family(c)];
    if (!changed || changed === "steps") put(role("sampler"), "steps", get(c, "steps") || defaults[0]);
    if (!changed || changed === "cfg") put(role("sampler"), "cfg", get(c, "cfg") || defaults[1]);
    for (const key of ["width", "height", "batch_size"])
        if (!changed || changed === key) put(role("latent"), key, get(c, key));
    for (let i = 1; i <= 3; i++) {
        if (changed && changed !== `lora_${i}` && changed !== `strength_${i}`) continue;
        const n = role(`lora_${i}`), name = get(c, `lora_${i}`), strength = get(c, `strength_${i}`);
        if (!n) continue;
        const active = name !== catalog.none && loraChoices(c).includes(name);
        n.mode = active ? 0 : 4;
        n.title = `LoRA ${i} · ${active ? "启用" : "关闭（旁路）"}`;
        if (active) put(n, "lora_name", name);
        put(n, "strength_model", strength);
        put(n, "strength_clip", family(c) === "sdxl" ? strength : 0);
    }
    app.graph.setDirtyCanvas(true, true);
}
function bindNative(c) {
    filter(c);
    for (const n of app.graph._nodes) {
        const r = n.properties?.ld_role;
        for (const w of n.widgets ?? []) {
            if (w.ldExpandedBound || n === c) continue;
            let target;
            if (r === "sampler" && ["steps", "cfg"].includes(w.name)) target = w.name;
            if (r === "latent" && ["width", "height", "batch_size"].includes(w.name)) target = w.name;
            if (/^lora_[123]$/.test(r ?? "")) {
                if (w.name === "lora_name") target = r;
                if (w.name === "strength_model") target = `strength_${r.slice(-1)}`;
            }
            if (!target) continue;
            const original = w.callback;
            w.callback = function (value, ...rest) {
                original?.call(this, value, ...rest);
                const current = control();
                if (current) { put(current, target, value); apply(current, target); }
            };
            w.ldExpandedBound = true;
        }
    }
}

async function switchModel(c) {
    await ready;
    const label = get(c, "model_name"), template = templates[label];
    if (!template) throw Error(`尚未配置该模型：${label}`);
    const current = app.graph.serialize();
    const oldBranch = new Set(current.nodes.filter(n => n.properties?.ld_branch).map(n => n.id));
    const next = structuredClone(template);
    // Preserve prompts and user layout for nodes that have the same role.
    for (const n of next.nodes.filter(n => n.properties?.ld_branch)) {
        const old = current.nodes.find(x => x.properties?.ld_role === n.properties.ld_role);
        if (old) {
            n.pos = old.pos;
            if (["positive", "negative"].includes(n.properties.ld_role)) n.widgets_values = old.widgets_values;
        }
    }
    const newBranch = new Set(next.nodes.filter(n => n.properties?.ld_branch).map(n => n.id));
    const branchLinks = next.links.filter(l => newBranch.has(l[1]) || newBranch.has(l[3]));
    current.nodes = [...current.nodes.filter(n => !oldBranch.has(n.id)), ...next.nodes.filter(n => newBranch.has(n.id))];
    current.links = current.links.filter(l => !oldBranch.has(l[1]) && !oldBranch.has(l[3]));
    let linkId = Math.max(current.last_link_id ?? 0, ...current.links.map(l => l[0]));
    current.links.push(...branchLinks.map(l => [++linkId, ...l.slice(1)]));
    current.last_link_id = linkId;
    current.last_node_id = Math.max(current.last_node_id, ...current.nodes.map(n => n.id));
    // Rebuild both ends of every link, including links to preserved user nodes.
    for (const n of current.nodes) {
        for (const p of n.inputs ?? []) p.link = null;
        for (const p of n.outputs ?? []) p.links = [];
    }
    for (const l of current.links) {
        current.nodes.find(n => n.id === l[1]).outputs[l[2]].links.push(l[0]);
        current.nodes.find(n => n.id === l[3]).inputs[l[4]].link = l[0];
    }
    // Reconfigure the current graph in place, without opening another workflow tab.
    app.graph.beforeChange();
    app.graph.configure(current);
    app.graph.afterChange();
    const active = control();
    const choices = [catalog.none, ...loraChoices(active)];
    let reset = false;
    for (let i = 1; i <= 3; i++) if (!choices.includes(get(active, `lora_${i}`))) {
        put(active, `lora_${i}`, catalog.none); reset = true;
    }
    const defaults = catalog.defaults[family(active)];
    put(role("sampler"), "sampler_name", defaults[2]);
    put(role("sampler"), "scheduler", defaults[3]);
    bindNative(active);
    apply(active);
    app.extensionManager.toast.add({severity: "info", summary: "模型已切换", detail:
        `已展开 ${label} 的配套节点，提示词和手动步数已保留。${reset ? "不兼容的 LoRA 已关闭。" : ""}`, life: 5000});
}

app.registerExtension({
    name: "local_draw.expanded",
    beforeRegisterNodeDef(nodeType, data) {
        if (data.name !== TYPE) return;
        const original = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            original?.apply(this, arguments);
            this.isVirtualNode = true;
            for (const w of this.widgets ?? []) {
                w.label = labels[w.name] ?? w.name;
                const callback = w.callback;
                w.callback = async (value, ...rest) => {
                    callback?.call(w, value, ...rest);
                    try {
                        await ready;
                        if (w.name === "model_name") await switchModel(this);
                        else apply(this, w.name);
                    } catch (error) {
                        console.error(error);
                        app.extensionManager.toast.add({severity: "error", summary: "设置未同步", detail: String(error), life: 10000});
                    }
                };
            }
        };
    },
    async afterConfigureGraph() {
        const c = control();
        if (!c) return;
        try { await ready; if (c === control()) bindNative(c); }
        catch (error) { console.error("完整节点设置面板：", error); }
    },
});
