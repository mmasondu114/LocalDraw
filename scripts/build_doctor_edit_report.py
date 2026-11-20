"""Package local GPU observations and original-size images into a browsable report."""
import html
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/test-results/delta-v6-anima-edit-v2-20261018/doctor-penguin'
GROUPS = [
    ('baseline', 'A · Delta v6 普通生成', '无图像参考；30 步，CFG 4.5，dpmpp_2m / sgm_uniform。'),
    ('chain', 'B · 前一张传给后一张', '首张参考博士原图，之后参考前一张；长描述；Edit 权重 1，其余参数与 A 相同。'),
    ('direct', 'C · 简短编辑指令传图', '首张参考博士原图，之后参考前一张；Edit 权重 1，30 步，CFG 4，euler_ancestral / simple。'),
    ('fixed', 'D · 每张参考博士原图', '每张直接参考角色库原图；明确面罩约束；Edit 权重 0.8，30 步，CFG 4，euler_ancestral / simple。'),
]
TARGETS = ['目标 01 · 站立抱企鹅', '目标 02 · 侧身行走', '目标 03 · 坐在长椅上', '目标 04 · 蹲下放企鹅']
font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 23)
small = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 19)

def main():
    observations = json.loads((OUT/'visual-review.json').read_text(encoding='utf-8'))
    rows=[]; sections=[]; metrics=[]
    for key, title, desc in GROUPS:
        strip=Image.new('RGB',(1536,590),'#f3f5f7'); d=ImageDraw.Draw(strip)
        d.text((16,8),title,font=font,fill='#182430')
        cards=[]
        for i in range(4):
            case=f'doctor-{key}-{i+1:02}'
            meta=json.loads((OUT/case/'result.json').read_text(encoding='utf-8'))
            metrics.append(meta)
            image=Image.open(OUT/case/(case+'.png')).convert('RGB'); image.thumbnail((384,512))
            strip.paste(image,(384*i,78))
            d.text((384*i+16,47),TARGETS[i],font=small,fill='#394654')
            src=f'{case}/{case}.png'
            note=observations[key]['panels'][i]
            cards.append(f'<article><h3>{TARGETS[i]}</h3><a href="{src}"><img src="{src}" alt="{TARGETS[i]}"></a><p>{html.escape(note)}</p><small>{meta["seconds"]} 秒 · 峰值显存 {meta["peak_device_used_mib"]/1024:.2f} GiB</small><details><summary>生成记录与提示词</summary><p>{html.escape(meta["positive"])}</p><a href="{case}/workflow-api.json">API 工作流</a> · <a href="{case}/result.json">完整参数</a></details></article>')
        strip.save(OUT/(key+'-strip.jpg'),quality=95)
        rows.append(strip)
        sections.append(f'<section><h2>{title}</h2><p>{desc}</p><p class="note">{html.escape(observations[key]["summary"])}</p><div class="grid">{"".join(cards)}</div></section>')
    contact=Image.new('RGB',(1536,590*4),'white')
    for i,row in enumerate(rows): contact.paste(row,(0,590*i))
    contact.save(OUT/'all-comparisons.jpg',quality=95)
    report='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>博士与企鹅 · Delta v6 / Anima Edit v2 实测</title>
<style>body{font:16px/1.65 system-ui,'Microsoft YaHei',sans-serif;background:#eef2f5;color:#182430;margin:auto;max-width:1700px;padding:28px}h1{font-size:32px}h2{font-size:24px}h3{font-size:18px}section,header{background:white;border-radius:16px;padding:24px;margin:24px 0}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:16px}article img{width:100%;border-radius:8px}article p{min-height:4.5em}small{color:#526373}.note{border-left:4px solid #ce9340;padding:8px 16px;background:#fff8ec}.source{display:flex;gap:24px;align-items:center}.source img{width:220px;height:240px;object-fit:contain;background:#f4f4f4}a{color:#176477}details{font-size:13px;overflow-wrap:anywhere}@media(max-width:900px){.grid{grid-template-columns:repeat(2,1fr)}.source{display:block}}</style>
<header><h1>博士与企鹅：连续分镜实测</h1><p>2026-10-18 · 本地 Delta Mix v6 · 768 × 1024 · 16 张生成结果</p><p class="note">''' + html.escape(observations['conclusion']) + '''</p><div class="source"><a href="doctor-source.png"><img src="doctor-source.png" alt="角色库博士原图"></a><div><h2>外观基准：你角色库里的博士</h2><p>黑色兜帽、全遮脸面罩、青色臂带、橙色袖标与口袋标记、灰色下摆。企鹅是本次提示词新增，原图中没有企鹅。</p><p>四列分别要求：抱住 → 行走 → 坐下 → 蹲下放到地面。标题代表要求，是否完成以画面及逐张说明为准。</p><p>所有组使用相同的四个种子。A/B 使用相同提示词与采样参数；C/D 同时改变了提示词和参数，是改进尝试，不能当作单变量实验。</p></div></div></header>''' + ''.join(sections) + '''<section><h2>复现与局限</h2><p>每张原图、实际工作流、种子、提示词和显存采样已保存。显存数值是整张显卡的已用量（包含桌面等占用），按约一秒采样；生成时间含提交和等待，不代表纯模型推理时间。</p><p>这是单个角色、四个动作的小样本，评价来自画面检查，未计算客观身份相似度。B/C 是“上一张参考 → 下一张生成”，没有实现两张目标图同时联合采样；当前参考节点最多接收一张参考图。</p><p>使用 Delta 的 LoRA 映射加载器将 28 层 LoRA 映射到 52 层模型。运行成功不等于编辑效果或作者原模型等效。没有加载不兼容的 SDXL/SD1.5 博士 LoRA，也没有把试验接入正常出图默认流程。</p><p><a href="https://civitai.com/models/2650553?modelVersionId=3089149">Anima Edit v2 来源</a> · <a href="https://github.com/Mirumo0u0/ComfyUI-Cosmos-Reference">参考节点</a> · <a href="all-comparisons.jpg">全部结果拼图</a> · <a href="visual-review.json">观察记录</a></p></section></html>'''
    report = report.replace('</header>', '</header><section><h2>袖章放大对照：未通过</h2><p>左侧为角色库原图，右侧为 D 组四张结果的局部裁剪。图案内部结构、轮廓比例均有变化。仅做裁剪放大，没有重绘细节。</p><img src="sleeve-detail.jpg" alt="参考袖章与四张生成结果的对照" style="max-width:100%;height:auto"></section>')
    (OUT/'report.html').write_text(report,encoding='utf-8')
    (OUT/'metrics.json').write_text(json.dumps([{'case':m['case'],'seconds':m['seconds'],'peak_device_used_mib':m['peak_device_used_mib']} for m in metrics],ensure_ascii=False,indent=2),encoding='utf-8')
    for key,_,_ in GROUPS:
        subset=[m for m in metrics if m['case'].startswith('doctor-'+key+'-')]
        print(key,'time',min(m['seconds'] for m in subset),max(m['seconds'] for m in subset),'peak_mib',max(m['peak_device_used_mib'] for m in subset))

if __name__=='__main__':main()
