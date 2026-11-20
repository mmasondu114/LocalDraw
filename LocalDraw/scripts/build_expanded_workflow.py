"""Build inspectable ComfyUI graphs from the live node schemas."""
import copy
import json
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
INFO = json.loads((ROOT / 'data/comfy-node-info.json').read_text(encoding='utf-8-sig'))
CAT = json.load(urlopen('http://127.0.0.1:8000/local_draw_unified/catalog'))
POS = 'masterpiece, best quality, safe, anime illustration. An adult female traveler with silver hair wears a blue cloak over a white dress. She stands on a stone bridge in a mountain village, warm morning sunlight, mountains in the distance, detailed background.'
NEG = 'worst quality, low quality, blurry, jpeg artifacts, watermark, signature, text'


def build(label, profile):
    family = profile['family']
    lf = 'anima' if family in ('anima', 'anima38', 'delta') else family
    first_lora = next(n for n, f in CAT['loras'].items() if f == lf)
    nodes, links = [], []

    def node(id, typ, title, xy, size, values=None, branch=False, role=None):
        schema = INFO.get(typ, {})
        inputs, widgets = [], []
        for section in ('required', 'optional'):
            for name, spec in schema.get('input', {}).get(section, {}).items():
                kind = spec[0]
                if isinstance(kind, list) or kind in ('INT', 'FLOAT', 'BOOLEAN', 'STRING', 'COMBO'):
                    default = spec[1].get('default') if len(spec) > 1 else None
                    if default is None:
                        default = kind[0] if isinstance(kind, list) else 0
                    widgets.append((values or {}).get(name, default))
                    if name == 'seed':
                        widgets.append('randomize')
                else:
                    inputs.append({'name': name, 'type': kind, 'link': None})
        out = [{'name': n, 'type': t, 'links': []} for n, t in zip(schema.get('output_name', []), schema.get('output', []))]
        n = dict(id=id, type=typ, title=title, pos=xy, size=size, flags={}, order=len(nodes), mode=0,
                 inputs=inputs, outputs=out, properties={'Node name for S&R': typ, 'ld_role': role or typ, 'ld_branch': branch}, widgets_values=widgets)
        nodes.append(n)
        return n

    def wire(a, output, b, input):
        src, dst = next(n for n in nodes if n['id'] == a), next(n for n in nodes if n['id'] == b)
        slot = next(i for i, p in enumerate(dst['inputs']) if p['name'] == input)
        id = len(links) + 1
        links.append([id, a, output, b, slot, src['outputs'][output]['type']])
        src['outputs'][output]['links'].append(id)
        dst['inputs'][slot]['link'] = id

    controls = node(1, 'LocalDrawExpandedControls', '① 常用设置 · 模型 / LoRA / 步数', [40, 80], [390, 400], role='controls')
    controls['widgets_values'] = [label, CAT['none'], .7, CAT['none'], .7, CAT['none'], .7, 0, 0, 832, 1216, 1]
    controls.update(color='#223', bgcolor='#335')
    if family == 'sdxl':
        node(10, 'CheckpointLoaderSimple', '底模 · 自带 CLIP 与 VAE', [40, 850], [340, 160], {'ckpt_name': profile['file']}, True, 'loader')
        clip, clipout, vae, vaeout = 10, 1, 10, 2
    else:
        typ = 'Anima38BV2Loader' if family == 'anima38' else 'UNETLoader'
        node(10, typ, '底模 · ' + label, [40, 850], [340, 120], {'model_name': profile['file'], 'unet_name': profile['file'], 'weight_dtype': 'default'}, True, 'loader')
        node(11, 'CLIPLoader', '文本编码器 · 自动匹配', [40, 1040], [340, 130], {
            'clip_name': 'qwen3vl_4b_fp8_scaled.safetensors' if family == 'krea2' else 'qwen_3_06b_base.safetensors',
            'type': 'krea2' if family == 'krea2' else 'stable_diffusion', 'device': 'default'}, True, 'clip')
        node(13, 'VAELoader', 'VAE · 自动匹配', [1480, 1030], [340, 100], {'vae_name': 'qwen_image_vae.safetensors'}, True, 'vae')
        clip, clipout, vae, vaeout = 11, 0, 13, 0
    if family == 'anima38':
        node(12, 'AnimaQwen35Loader', '语义编码器 · Qwen3.5', [40, 1230], [340, 100], {'qwen35_model': 'qwen35_4b.safetensors'}, True, 'qwen35')
    previous, mo, co = 10, 0, clipout
    for i in range(3):
        typ = 'DeltaMixQ35LoraLoader' if family in ('anima38', 'delta') else 'LoraLoader'
        n = node(20+i, typ, f'LoRA {i+1} · 关闭（旁路）', [460+330*i, 850], [300, 230], {
            'lora_name': first_lora, 'strength_model': .7, 'strength_clip': .7 if family == 'sdxl' else 0,
            'skip_gen2': True, 'skip_watch': True}, True, f'lora_{i+1}')
        n['mode'] = 4
        wire(previous, mo, 20+i, 'model')
        wire(clip if i == 0 else previous, co if i == 0 else 1, 20+i, 'clip')
        previous, mo, co = 20+i, 0, 1
    for id, title, y, text, role in ((30, '② 正向提示词 · 想画什么', 80, POS, 'positive'), (31, '③ 反向提示词 · 避免什么', 410, NEG, 'negative')):
        typ = 'Anima38BV2Prompt' if family == 'anima38' else 'CLIPTextEncode'
        n = node(id, typ, title, [490, y], [440, 260 if id == 30 else 210], {'prompt': text, 'text': text}, True, role)
        n.update(color='#232' if id == 30 else '#322', bgcolor='#353' if id == 30 else '#533')
        if family == 'anima38':
            wire(22, 0, id, 'model'); wire(22, 1, id, 'native_clip'); wire(12, 0, id, 'qwen35_clip')
        else:
            wire(22, 1, id, 'clip')
    if family == 'krea2':
        node(32, 'ConditioningZeroOut', 'Krea2 · 使用零负向条件', [1000, 650], [340, 70], branch=True, role='zero_negative')
        wire(30, 0, 32, 'conditioning')
        next(n for n in nodes if n['id'] == 31)['title'] = '反向提示词 · Krea2 不使用此框'
    node(40, 'EmptyLatentImage', '④ 画布尺寸与张数', [1000, 500], [350, 130], {'width': 832, 'height': 1216, 'batch_size': 1}, role='latent')
    steps, cfg, sampler, scheduler = CAT['defaults'][family]
    node(41, 'KSampler', '⑤ 采样 · 实际步数 / CFG / 种子', [1000, 80], [350, 330], {'seed': 20261016, 'steps': steps, 'cfg': cfg, 'sampler_name': sampler, 'scheduler': scheduler, 'denoise': 1}, role='sampler')
    node(42, 'VAEDecode', '⑥ VAE 解码 · 潜空间转图片', [1480, 850], [340, 100], role='decode')
    node(43, 'SaveImage', '⑦ 图片预览 · 自动保存', [1480, 80], [440, 650], {'filename_prefix': 'FullNodes/image'}, role='save')
    wire(22, 0, 41, 'model'); wire(30, 0, 41, 'positive'); wire(32 if family == 'krea2' else 31, 0, 41, 'negative')
    wire(40, 0, 41, 'latent_image'); wire(41, 0, 42, 'samples'); wire(vae, vaeout, 42, 'vae'); wire(42, 0, 43, 'images')
    note = node(50, 'Note', '操作说明 · 所有生成步骤均已展开', [40, 550], [390, 200], role='note')
    note['widgets_values'] = ['先在①选择模型、LoRA与常用参数，再填写正反提示词，点击运行。\n步数 / CFG = 0：使用模型推荐值，实际数值见⑤。\n①只同步设置，不参与生成。下方每个节点都可检查与修改。\nLoRA选择“不使用”时，该节点变为紫色旁路。\n切换模型会替换配套加载、LoRA及编码节点；保留提示词、尺寸和手动步数。\nLoRA触发词请写入正向提示词。Krea2不使用反向提示词。']
    compact = {
        10: ([40, 650], [340, 110]), 11: ([40, 790], [340, 115]), 12: ([40, 940], [340, 90]),
        13: ([1480, 850], [340, 100]), 20: ([460, 650], [300, 230]), 21: ([790, 650], [300, 230]),
        22: ([1120, 650], [300, 230]), 30: ([490, 80], [440, 220]), 31: ([490, 360], [440, 180]),
        32: ([1000, 570], [350, 65]), 40: ([1000, 440], [350, 120]), 42: ([1480, 650], [340, 100]),
        43: ([1480, 80], [440, 500]), 50: ([460, 940], [960, 110]),
    }
    for n in nodes:
        if n['id'] in compact:
            n['pos'], n['size'] = compact[n['id']]
    return dict(id='b28efb99-85dd-4914-9535-7231176c3908', revision=0, last_node_id=50, last_link_id=len(links), nodes=nodes, links=links,
                groups=[{'id': 1, 'title': '常用操作区', 'bounding': [10, 10, 1390, 580], 'color': '#3f789e', 'font_size': 24, 'flags': {}},
                        {'id': 2, 'title': '模型加载与 LoRA · 完整连线', 'bounding': [10, 600, 1450, 460], 'color': '#7460a0', 'font_size': 24, 'flags': {}}],
                config={}, extra={'ds': {'scale': .55, 'offset': [100, 100]}}, version=.4)


templates = {label: build(label, profile) for label, profile in CAT['models'].items()}
out = ROOT / 'comfy_nodes/local_draw_unified/web/expanded-templates.json'
out.write_text(json.dumps(templates, ensure_ascii=False, indent=2), encoding='utf-8')
flow = templates['Anima 3.8B v1.1']
(ROOT / 'workflows/unified-txt2img-full-nodes.json').write_text(json.dumps(flow, ensure_ascii=False, indent=2), encoding='utf-8')
print(f'Built {len(templates)} complete graphs, {len(flow["nodes"])} visible nodes in default graph.')
