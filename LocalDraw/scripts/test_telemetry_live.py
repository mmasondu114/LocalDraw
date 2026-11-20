"""Explicit real-model telemetry smoke test; isolated story/image output."""
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from comic_workflow import engine as e, creative, comfyui
from comic_workflow.model import request_json, endpoint
from comic_workflow.paths import DATA
from comic_workflow.characters import CharacterLibrary
from comic_workflow.repository import Repository

folder = DATA / "test-results/telemetry-live" / e.stamp()
folder.mkdir(parents=True)
events = []
last_phase = None
def update(value):
    global last_phase
    if not isinstance(value, dict) or "runtime" not in value:
        return
    runtime = value["runtime"]
    events.append(runtime)
    with (folder / "events.jsonl").open("a", encoding="utf-8") as out:
        out.write(json.dumps(runtime, ensure_ascii=False) + "\n")
    if runtime["phase"] != last_phase:
        print(runtime["phase"], flush=True)
        last_phase = runtime["phase"]

assert request_json(endpoint() + "/health")["status"] == "ok", "Start the studio language model first"
result = creative.character_draft(CharacterLibrary(folder / "characters"), {"mode": "text", "name": "进度技术测试角色", "text": "原创成年女钟表匠，28岁，黑色齐肩短发、绿色眼睛、灰色工作外套、棕色靴子。性格沉静，喜欢先观察再说话，害怕迟到。不携带武器。这是独立技术测试资料，不是用户角色。"}, folder / "text", update)
e.save(folder / "text-result.json", result)
assert any(x.get("output_tokens", 0) and x["phase"] == "生成输出" for x in events)
assert any(x["phase"] == "处理输入" for x in events)
# Let the owning studio unload its language model before this test draws.
import urllib.request
bootstrap = request_json("http://127.0.0.1:8765/api/bootstrap")
request = urllib.request.Request("http://127.0.0.1:8765/api/action", data=json.dumps({"action":"stop_model"}).encode(), headers={"Content-Type":"application/json", "X-Studio-Token":bootstrap["token"]})
urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=30).close()

repo = Repository(folder / "projects")
pid = repo.create(None, True)
p = repo.get(pid)
panel = p["pages"]["P001"]["panels"][0]
payload = {"panel_id":panel["id"], "positive":"anime background illustration, a white origami paper boat on a wooden table, soft daylight, no people, clean lineart", "negative":"text, watermark, blurry", "composition_variants":["table front view", "table side view", "table overhead view"], "character_anchors":[{"character_id":x["character_id"],"tags":"technical fixture"} for x in panel["characters"]], "adaptation_notes":"独立进度技术测试，不是用户创作或画面符合度测试"}
c=repo.propose(pid,"drawing_prompt",panel["id"],payload,e.digest(p));repo.accept(pid,c["id"])
manager=comfyui.ComfyManager()
try:
    manager.start()
    batch=comfyui.run_batch(repo,pid,panel["id"],update,count=1)
finally:
    manager.stop()
sampling=[x for x in events if x.get("unit")=="采样步"]
assert sampling and sampling[-1]["completed"] == sampling[-1]["total"], sampling
report={"source":"本机真实模型的独立进度测试，不是用户剧本或用户选图", "text_events":len([x for x in events if x["source"]=="llama.cpp"]), "prefill_events":len([x for x in events if x["phase"]=="处理输入"]), "output_tokens":result["metrics"]["usage"]["completion_tokens"], "sampling_events":len(sampling), "sampling_first":sampling[0], "sampling_last":sampling[-1], "image":str(repo.folder(pid)/"images"/batch["images"][0]["file"])}
e.save(folder/"report.json",report)
print(json.dumps({"folder":str(folder),"status":"passed","sampling_steps":sampling[-1]["total"]}),flush=True)
