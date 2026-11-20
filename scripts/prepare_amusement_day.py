"""Prepare the six-page story through studio APIs, with resumable stage saves.

No generation is submitted by this script. The final request is saved separately.
"""
import copy
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from comic_workflow import engine as e, prompt_exchange
from comic_workflow.characters import CharacterLibrary
from comic_workflow.creative import STORY
from comic_workflow.repository import Repository
from amusement_day_story import TITLE, NAMES, EXUSIAI, DOCTOR, PAGES

OUT = ROOT / "data/authored-stories/amusement-day-20261017"
MODEL = "animaDeltaMix38BQwen0_deltaMixV60.safetensors"
PROPS = {
    "O001":"一张可折叠游乐园导览图，仅图标与路线，无可读文字",
    "O002":"两张游乐园门票，由能天使保管",
    "O003":"一张奶油色三圆格游园集章卡，蓝色图标印章，无可读文字",
    "O004":"旋转木马的白色木马及独立黄铜竖杆",
    "O005":"旋转木马的栗色木马及独立黄铜竖杆",
    "O006":"一个黄色套圈圆环",
    "O007":"一个手掌大小的黑白企鹅玩偶，橙色小嘴，领到后由博士保管",
    "O008":"一片苹果派、一只小叉子与一个纸盘",
    "O009":"一杯带盖咖啡",
    "O010":"摩天轮封闭玻璃座舱，左右各一条座椅",
    "O011":"出口旁自助合影机，带大圆形启动按钮",
    "O012":"最后打印的一张双人合影照片",
}


def get(path):
    with urllib.request.urlopen("http://127.0.0.1:8765"+path, timeout=25) as r:
        return json.load(r)


def write_story():
    refs = [CharacterLibrary().get(name) for name in NAMES.values()]
    initial = {"C001.location":"游乐园入口左侧", "C002.location":"游乐园入口右侧",
        "C001.clothing":EXUSIAI,"C002.clothing":DOCTOR,
        "O001.holder":"C002双手","O001.state":"展开的导览图",
        "O002.holder":"C001左手","O002.state":"两张门票",
        "O003.holder":"C002外套口袋","O003.state":"三个空白圆格",
        "O006.holder":"套圈摊","O006.state":"尚未领取",
        "O007.holder":"套圈摊奖品区","O007.state":"尚未赢得的小企鹅",
        "O008.holder":"尚未购买","O008.state":"尚未购买",
        "O009.holder":"尚未购买","O009.state":"尚未购买",
        "O012.holder":"尚未拍摄","O012.state":"尚未打印","time":"清晨"}
    state = copy.deepcopy(initial)
    pages, planned, prompts = {}, [], []
    script = [f"《{TITLE}》", "六页日常短篇 · 能天使与博士", "关系设定：轻松温暖的同伴出游；博士保持中性称谓与兜帽遮面形象。"]
    for number, chapter in enumerate(PAGES, 1):
        page_id = f"P{number:03}"
        script += ["", f"第 {number} 页：{chapter['title']}", chapter["intro"]]
        page = dict(id=page_id,number=number,reading_direction="LTR",layout="上方宽幅交代环境，中部短格推进，底部宽格收束",
                    page_turn_note="独立单页，按从左到右、从上到下阅读",state_in=copy.deepcopy(state),panels=[],lettering=[])
        for n, spec in enumerate(chapter["panels"], 1):
            panel_id=f"{page_id}-K{n:02}"
            if n==1:
                for cid in NAMES: state[cid+".location"]=chapter["scene"]+("左侧" if cid=="C001" else "右侧")
                state["time"]=["清晨","上午","午前","午后","日落","蓝调傍晚"][number-1]
            assert set(spec["changes"]) <= set(state), f"Unexpected continuity key in {panel_id}"
            state.update(spec["changes"])
            cast = [dict(character_id=cid,position=("画面中央或该角色入镜局部" if len(spec["roles"])==1 else "画面左侧" if cid=="C001" else "画面右侧"),
                         gaze="按本格动作与镜头描述",action=act,expression="轻松自然；局部或背面镜头不额外显示面部") for cid,act in spec["roles"].items()]
            prop_rows = [dict(prop_id=key,position=state.get(key+".holder",chapter["scene"]),
                             state=state.get(key+".state",PROPS[key])) for key in spec["props"]]
            panel=dict(id=panel_id,page_id=page_id,number=n,purpose=chapter["title"]+"："+spec["action"],layout="按页内阅读顺序排列",
                       aspect_ratio=spec["aspect_ratio"],shot=spec["shot"],angle=spec["angle"],composition=spec["shot"]+"；"+spec["action"],
                       scene_id=f"S{number:03}",characters=cast,main_instant=spec["action"],background=chapter["scene"]+"；"+chapter["lighting"]+"；不增加背景路人",
                       props=prop_rows,transition_in="新场景建立" if n==1 else "细节插入镜头" if not cast or "特写" in spec["shot"] else "顺着人物视线与动作切镜",
                       transition_out="进入下一页" if n==5 and number<6 else "全篇留白结束" if n==5 else "切入下一格",
                       state_after=copy.deepcopy(state),drawing_description=spec["action"]+" 光线："+chapter["lighting"]+"。服装与角色卡保持一致，游玩不携带武器。")
            page["panels"].append(panel)
            script += [f"\n{panel_id}｜{spec['shot']}，{spec['angle']}。", spec["action"]]
            for order,(cid,line) in enumerate(spec["dialogue"],1):
                page["lettering"].append(dict(id=f"{panel_id}-T{order:02}",panel_id=panel_id,kind="dialogue",speaker_id=cid,text=line,
                    bubble_position="画面上方留白，避开头顶光环、兜帽和主要道具",order=order))
                script.append(NAMES[cid]+"："+line)
            if not spec["dialogue"]: script.append("（无对白）")
            negative = "extra people, duplicate character, animal ears, firearms, text, speech bubbles, watermark"
            if not cast: negative="people, hands, faces, text, speech bubbles, watermark"
            if set(spec["roles"])=={"C002"}: negative+=", halo, wings, red hair, exposed face"
            if spec["extra_negative"]: negative+=", "+spec["extra_negative"]
            prompts.append(dict(panel_id=panel_id,positive=spec["positive"],negative=negative))
        page["state_out"]=copy.deepcopy(state)
        page["layout_rows"]=[{"weight":1.25,"panels":[{"panel_id":f"{page_id}-K01","weight":1}]},
            {"weight":1,"panels":[{"panel_id":f"{page_id}-K02","weight":1},{"panel_id":f"{page_id}-K03","weight":1}]},
            {"weight":1.15,"panels":[{"panel_id":f"{page_id}-K04","weight":1},{"panel_id":f"{page_id}-K05","weight":1.4}]}]
        pages[page_id]=page
        planned.append(dict(id=page_id,number=number,scene_ids=[f"S{number:03}"],character_ids=list(NAMES),panel_count=5,
                            purpose=chapter["title"],beat=chapter["intro"],hook=chapter["panels"][-1]["action"],reveal="",
                            turn_after=True,constraints=["博士兜帽与面罩始终遮脸，不指定性别", "能天使人耳、白光环与几何光翼不得转移给博士", "同一格只画所列出镜人物或局部"] ))
    brief=dict(title=TITLE,genre="游乐园日常温情喜剧",direction="/能天使 和 /博士 相约游乐园，从精确规划到享受没有截止时间的一天；结尾用合影与下次再来的约定回扣开场。",
               page_count=6,panels_per_page=5,panel_mode="fixed",reading_direction="LTR",page_mode="single_page",first_story_page_side="right",
               must_include=["入园收起路线图","旋转木马","套圈赢得小企鹅","树荫下苹果派与咖啡","日落摩天轮","离园合影与下次再来"],
               avoid=["擅自指定博士性别或露脸外貌","人物特征串位","背景额外路人","枪械","强行设为恋爱关系"],
               character_material="/能天使 与 /博士 使用用户已保存角色卡，本篇为同伴日常出游，衣着完整，不携带武器。",character_refs=refs)
    canon=dict(characters=[dict(id=cid,name=name,origin="existing_work",source_notes=["用户本地已保存角色卡；本次游玩情节为原创改编"],verified_facts=[],
                      adaptation_choices=["不携带武器的游园休息日", "同伴关系，不擅自添加恋爱设定"],unknowns=["不补充未指定的原作时间线细节"],
                      appearance=EXUSIAI if cid=="C001" else DOCTOR,voice="爽朗、自然，称博士为老板" if cid=="C001" else "简洁平静，带克制的温和幽默",
                      sample_lines=["老板，今天禁止加班！"] if cid=="C001" else ["今天没有截止时间。"],motivation="陪同伴放松，认真享受一天假期",
                      taboos=["人物核心外观混用", "博士摘面罩或显露未指定的性别外貌"]) for cid,name in NAMES.items()],
               scenes=[dict(id=f"S{n:03}",name=c["scene"],layout="双人同框默认能天使在画面左侧、博士在右侧；背面或局部镜头明确说明",
                   landmarks=[c["scene"],c["title"]],lighting=c["lighting"],axis_rule="保持双人左右归属，单人和物件特写按画面裁切，不加入画外人物") for n,c in enumerate(PAGES,1)],
               props=[dict(id=k,description=v) for k,v in PROPS.items()],world_rules=[],initial_state=initial)
    outline=dict(selected_option="direct",premise="博士的路线优化，被能天使变成一次没有截止时间的游园。",beats=[c["intro"] for c in PAGES],
                 ending="两人留下合影，博士把下次游园列为长期项目。",must_include_mapping=[f"P{n:03} {c['title']}" for n,c in enumerate(PAGES,1)],unknowns=[])
    story="\n".join(script)+"\n"
    payload=dict(story=story,brief=brief,canon=canon,outline=outline,assumptions=["由 Codex 直接写剧本、分镜和逐格英文提示词，不调用本地模型改写",
        "六页五格，共三十格；每格十六张，共四百八十张候选", "使用 Delta Mix v6，35 步；角色身份靠逐格描述，不叠加全图角色 LoRA，也不开启未验证区域功能"],changes=[])
    no_refs=copy.deepcopy(payload);no_refs["brief"].pop("character_refs")
    e.jsonschema.validate(no_refs,STORY)
    project=dict(workflow="direct",brief=brief,canon=canon,outline=outline,options=None,plan={"pages":planned},pages=pages,version=1)
    warnings=e.validate_project(project,complete=True)
    assert len(prompts)==30 and len({p["positive"] for p in prompts})==30
    return payload,project,prompts,warnings


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    payload,project,prompts,warnings=write_story()
    e.save(OUT/"authored-story.json",dict(payload=payload,plan=project["plan"],pages=project["pages"],prompts=prompts,validation_warnings=warnings))
    (OUT/"剧本与分镜.txt").write_text(payload["story"],encoding="utf-8")
    print(f"Validated {len(project['pages'])} pages and {len(prompts)} distinct panel prompts",flush=True)
    receipt_path=OUT/"prepared.json"
    receipt=e.read(receipt_path) if receipt_path.exists() else {}
    boot=get("/api/bootstrap")
    active=[j for j in boot["jobs"] if j.get("status") in {"running","queued","paused","pausing","cancelling"}]
    assert not active,"Unfinished generation exists; preserve it and resume preparation later"
    def action(name,**values):
        req=urllib.request.Request("http://127.0.0.1:8765/api/action",data=json.dumps(dict(action=name,**values),ensure_ascii=False).encode(),
            headers={"Content-Type":"application/json","X-Studio-Token":boot["token"]})
        with urllib.request.urlopen(req,timeout=30) as r:return json.load(r)
    repo=Repository()
    if not receipt.get("project"):
        # Recover a response-lost create by matching the complete authored source.
        existing=[p for p in boot["projects"] if p["title"]==TITLE and repo.get(p["id"]).get("script_source",{}).get("original")==payload["story"]]
        assert len(existing)<=1,"Multiple matching works: inspect before creating anything"
        if existing: pid=existing[0]["id"]
        else:
            created=action("create_story",result=dict(payload=payload,original=payload["story"],mode="authored_by_codex",metrics={}),
                           drawing_settings=dict(mode="none",checkpoint=MODEL,items=[],hand_detail=False))
            pid=created["project"]
        receipt.update(project=pid,title=TITLE,status="saving_stages")
        e.save(receipt_path,receipt)
    pid=receipt["project"]
    for stage,target,value in [("plan","",project["plan"])]+[("page",key,val) for key,val in project["pages"].items()]:
        current=repo.get(pid)
        saved=current.get("pages",{}).get(target) if stage=="page" else current.get(stage)
        if saved is not None:
            assert saved==value,f"Saved {stage} {target} differs; do not overwrite"
            continue
        candidate=action("propose",project=pid,stage=stage,target=target,payload=value,base_hash=e.digest(current))
        action("accept",project=pid,candidate=candidate["id"])
        receipt["last_saved_stage"]=target or stage;e.save(receipt_path,receipt)
        print("Saved "+(target or stage),flush=True)
    e.validate_project(repo.get(pid),complete=True)
    document=prompt_exchange.export_storyboard(repo,pid)
    keyed={p["panel_id"]:p for p in prompts}
    for row in document["panels"]:row.update({k:keyed[row["panel_id"]][k] for k in ("positive","negative")})
    e.save(OUT/"storyboard-prompts.json",document)
    preview=action("preview_prompt_import",project=pid,document=document)
    assert preview["valid"] and not preview["missing"],preview.get("errors")
    assert not preview["warnings"],preview["warnings"]
    if not receipt.get("bundle"):
        current=prompt_exchange.current_import(repo,pid).get("bundle")
        expected=[(p["panel_id"],p["positive"],p["negative"]) for p in preview["panels"]]
        if current and [(p["panel_id"],p["positive"],p["negative"]) for p in current["panels"]]==expected:
            bundle=current
        else:bundle=action("save_prompt_import",project=pid,document=document,preview_hash=preview["preview_hash"],confirm=True)
        receipt["bundle"]=bundle["id"];e.save(receipt_path,receipt)
    request=dict(action="draw_imported",project=pid,bundle=receipt["bundle"],candidate_count=16,checkpoint=MODEL,steps=35)
    render_plan=prompt_exchange.prepare_request(repo,pid,request)
    assert len(render_plan["targets"])==30 and render_plan["candidate_count"]==16
    assert all(not rows for rows in render_plan["loras_by_panel"].values())
    receipt.update(status="prepared_not_started",page_count=6,panel_count=30,candidate_count=16,image_count=480,
                   prompt_warnings=preview["warnings"],storyboard_warnings=warnings)
    e.save(receipt_path,receipt)
    e.save(OUT/"ready-to-start.json",dict(request=request,status="ready",image_count=480,follow_after_start=False))
    workspace_path=repo.folder(pid)/"workspace.json"
    workspace=e.read(workspace_path) if workspace_path.exists() else dict(views={})
    workspace.update(stage="drawing",target="",updated=int(time.time()*1000))
    workspace.setdefault("views",{}).setdefault("drawing:",{}).setdefault("fields",{})["literal-candidate-count-v2"]="16"
    action("save_workspace",project=pid,state=workspace)
    print(json.dumps(receipt,ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
