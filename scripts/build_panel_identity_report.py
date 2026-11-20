"""Publish the manually reviewed safe experiment, without touching story data."""
import base64
import html
import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data/test-results/panel-identity-20261017"


def build():
    review = json.loads((OUT / "verification.json").read_text(encoding="utf-8"))
    renders = json.loads((OUT / "render-report.json").read_text(encoding="utf-8"))
    reviewed = {r["id"] for r in review["visual_review"]}
    assert all(r["id"] in reviewed for r in renders), "Review every attempted case before publishing"
    cards = []
    for row in review["visual_review"]:
        path = OUT / (row["id"] + ".png")
        picture = (f'<img loading="lazy" src="data:image/png;base64,{base64.b64encode(path.read_bytes()).decode()}" '
                   f'alt="{html.escape(row["id"])}">') if path.exists() else '<p class="error">本次运行失败，没有成图。</p>'
        cards.append(f'<figure>{picture}<figcaption><b>{html.escape(row["id"])}</b>'
                     f'<p>{html.escape(row["finding"])}</p></figcaption></figure>')
    count = sum(r["status"] == "success" for r in renders)
    failures = len(renders) - count
    body = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>分镜与角色归属 · 两轮验证</title><style>
body{{margin:0;background:#f3f5f8;color:#192333;font:16px/1.65 system-ui,"Microsoft YaHei",sans-serif}}
main{{max-width:1240px;margin:auto;padding:32px}}header,section{{background:white;padding:24px;border-radius:14px;margin-bottom:24px}}
h1{{font-size:30px;margin:8px 0}}.badge{{display:inline-block;padding:5px 12px;border-radius:20px;background:#fff0cf;color:#795515}}
.grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px}}
figure{{margin:0;background:white;border:1px solid #dce2ea;border-radius:12px;overflow:hidden}}
img{{width:100%;display:block;aspect-ratio:4/3;object-fit:contain;background:#e8edf2}}figcaption{{padding:16px}}
p{{margin:8px 0}}li{{margin:6px 0}}summary{{cursor:pointer;font-size:20px;padding:12px}}
@media(max-width:700px){{.grid{{grid-template-columns:1fr}}main{{padding:16px}}}}</style>
<main><header><span class="badge">人物归属有改善 · 尚未通过整部重生成验收</span>
<h1>分镜与角色归属 · 两轮验证</h1><p>2026-10-17 · 独立非露骨短篇《归还信封》 · 共 {count} 张成图，{failures} 次失败提交。</p>
<p>Qwen3.8-27B Q5 已核验 GPU 推理。绘图使用 Delta Mix v6，35 步、CFG 4.5。本轮区域实验不加载角色 LoRA；实际提示词、随机种子与工作流另存供复查。</p></header>
<section><h2>本轮发现</h2><p>{html.escape(review['round_two']['conclusion'])}</p>
<ul><li>修正了实验中的组合方式：人物条件分别限定区域，背景条件只填补未覆盖区域，避免再叠加一份全图人物描述。</li>
<li>双人中景用三个不同种子复测：三张均未见红发人物长狼耳、黑发人物长光环，也未见多余人物。样本有限，不能当作成功率保证。</li>
<li>动作和道具尚不稳定：一张多出第二个信封，递交动作和服装细节仍有偏差。</li>
<li>区域过小会漏人；区域裁切可能让身体与建筑融合。扩大区域的远景结果见下方逐张评价。</li>
<li>另试了 <a href="https://github.com/Sen-sou/Comfyui-Anima-Regional-Conditioning">Anima 专用区域注意力节点</a>，并修正当前引擎的接口兼容问题。四项兼容检查通过，但成图仍须独立验收，详见全部成功与失败记录。</li>
<li>普通界面已说明角色 LoRA 绑定只是按出场加载，不能保证空间隔离。实验参数尚未启用到普通漫画流程。</li></ul>
<p>原作品、已确认提示词、图片与选图未改写；没有启动整部重生成。上一轮功能检查 53 项通过；本轮实验脚本与修改的界面脚本语法检查通过。</p></section>
<section><h2>本轮全部样图</h2><div class="grid">{''.join(cards[14:])}</div></section>
<details><summary>展开上一轮对照与失败记录（14 次提交）</summary><div class="grid">{''.join(cards[:14])}</div></details>
</main></html>'''
    previous = OUT / "report.html"
    backup = OUT / "report-round-one.html"
    if previous.exists() and not backup.exists():
        backup.write_bytes(previous.read_bytes())
    previous.write_text(body, encoding="utf-8")
    print(f"Saved report: {count} images, {failures} failures, {len(cards)} reviewed attempts")


if __name__ == "__main__":
    build()
