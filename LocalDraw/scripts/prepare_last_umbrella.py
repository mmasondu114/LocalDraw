"""Author a safe four-panel story using studio APIs; never starts a model/job."""
import copy
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from comic_workflow import engine as e, prompt_exchange
from comic_workflow.characters import CharacterLibrary
from comic_workflow.repository import Repository
from comic_workflow.creative import STORY

OUT = ROOT / "data/authored-stories/last-umbrella-20261017"
MODEL = "animaDeltaMix38BQwen0_deltaMixV60.safetensors"
STORY_TEXT = """《最后一把伞》

傍晚，小店刚刚打烊，雨还在下。

能天使站在屋檐下，看着手里唯一的一把深蓝色长柄伞。她把伞握在右手，伞尖垂向地面，笑得有一点为难。
能天使：“只剩一把了。”

德克萨斯停在屋檐另一侧。她侧过脸，看向能天使，黑色狼耳上的水珠还没有干。
德克萨斯：“一把也够。”

能天使撑开伞，右手握住中间的伞柄，把伞面偏向德克萨斯。两个人并肩站进同一把伞下。能天使笑起来，德克萨斯的嘴角也轻轻抬起。
能天使：“那就一起回去。”

镜头转到两人身后。能天使在左，德克萨斯在右，两人共撑同一把深蓝色伞，沿没有行人的湿石板街走远。雨水映着店里的暖光。
最后一格留白，不加对白。
"""


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    receipt_path = OUT / "prepared.json"
    receipt = e.read(receipt_path) if receipt_path.exists() else {}
    library, repo = CharacterLibrary(), Repository()
    names = ["能天使", "缄默德克萨斯"]
    cards = [library.get(name) for name in names]
    clothing = ["红色短夹克、白色衬衫、黑色长裤和黑靴", "深蓝色长外套、黑色长裤和黑靴"]
    looks = ["成年女性，红色短发、橙色眼睛、人类耳朵、头顶光环，" + clothing[0],
             "成年女性，黑色长发、黄色眼睛、头顶一对黑色狼耳，" + clothing[1]]
    initial = {"C001.location": "小店门口屋檐左侧", "C002.location": "同一屋檐右侧",
               "C001.clothing": clothing[0], "C002.clothing": clothing[1],
               "O001.holder": "C001右手", "O001.state": "收拢的深蓝色长柄伞", "time": "傍晚细雨"}
    brief = dict(title="最后一把伞", genre="日常温情短篇", direction="一页四格，以一把伞写两人一起回家的默契，全部穿着完整。",
                 page_count=1, panels_per_page=4, panel_mode="fixed", reading_direction="LTR", page_mode="single_page",
                 first_story_page_side="right", must_include=["唯一的一把深蓝色伞", "一把也够", "两人共伞离开的背影"],
                 avoid=["额外人物", "人物耳朵与光环混用", "第二把伞", "裸露或性内容"],
                 character_material="/能天使 与 /缄默德克萨斯 的独立日常改编；本篇服装以逐格描述为准。", character_refs=cards)
    canon = {"characters": [dict(id=f"C{i+1:03}", name=name, origin="existing_work", source_notes=["用户本地已保存角色资料"],
                verified_facts=[], adaptation_choices=["独立日常短篇，本篇服装为叙事选择"], unknowns=[], appearance=looks[i],
                voice="简短自然，不说教", sample_lines=["只剩一把了。" if i == 0 else "一把也够。"],
                motivation="和同伴一起避雨回家", taboos=["擅自改变同伴外观"]) for i, name in enumerate(names)],
             "scenes": [dict(id="S001", name="雨中小店门前", layout="门口屋檐面向湿石板街；双人同框时能天使左、德克萨斯右",
                landmarks=["木制店门", "暖色窗灯", "湿石板路"], lighting="傍晚冷蓝雨光与店内暖光", axis_rule="前三格保持人物左右关系；末格明确切换到背面")],
             "props": [dict(id="O001", description="唯一一把深蓝色长柄伞，木柄")], "world_rules": [], "initial_state": initial}
    beats = ["能天使发现只剩一把伞", "德克萨斯回答一把也够", "两人站到同一把伞下", "两人共伞离开的背影"]
    outline = dict(selected_option="direct", premise="雨还没停，两个人只有一把伞。", beats=beats,
                   ending="两人一起走进雨里，末格留白。", must_include_mapping=[f"P001-K{i+1:02} {b}" for i, b in enumerate(beats)], unknowns=[])
    payload = dict(story=STORY_TEXT, brief=brief, canon=canon, outline=outline,
                   assumptions=["由 Codex 直接撰写，未调用本地语言模型", "一页四格，每格两张候选，共八张；默认绘图模型 Delta Mix v6",
                                "使用简短逐格英文提示词，不加载全图角色 LoRA，不启用实验区域功能"], changes=[])
    no_refs = copy.deepcopy(payload); no_refs["brief"].pop("character_refs")
    e.jsonschema.validate(no_refs, STORY)
    plan = {"pages": [dict(id="P001", number=1, scene_ids=["S001"], character_ids=["C001", "C002"], panel_count=4,
              purpose="用镜头变化呈现共伞的默契", beat="；".join(beats), hook="一把伞如何让两人一起回家", reveal="一把也够",
              turn_after=True, constraints=["只有两位角色", "只有一把深蓝色伞", "末格从背面拍摄"]) ]}
    actions = ["能天使独自看着右手握着的收拢蓝伞，略带为难地微笑。", "德克萨斯的面部特写，向画外左侧投去平静的目光。",
               "两人并肩站在同一把撑开的蓝伞下，左侧能天使用右手握伞柄，伞面向右侧德克萨斯倾斜。",
               "从背面看两人并肩沿湿石板路离开，共撑同一把深蓝色伞。"]
    shots = ["单人中全景，膝上入镜", "单人头肩特写，两只狼耳完整入镜", "双人腰上中景，完整伞面入镜", "背面全身远景，两人头脚完整入镜"]
    backgrounds = ["木店门与屋檐，背景无人", "虚化的暖色店窗，背景无人", "店门前的雨幕，背景无人", "空旷的湿石板街与暖色窗灯，无路人"]
    states = [copy.deepcopy(initial) for _ in range(4)]
    states[2].update({"O001.state": "撑开并覆盖两人，伞面向C002倾斜"})
    states[3].update({"C001.location": "石板街上画面左侧", "C002.location": "石板街上画面右侧", "O001.state": "撑开并覆盖两人"})
    roles = [[dict(character_id="C001", position="画面中间", gaze="看向右手的伞", action="右手握收拢的伞", expression="略带为难的笑")],
             [dict(character_id="C002", position="画面中间", gaze="画外左侧", action="侧脸回答", expression="平静柔和")],
             [dict(character_id="C001", position="画面左侧", gaze="右侧同伴", action="右手握伞柄举伞", expression="明亮微笑"),
              dict(character_id="C002", position="画面右侧", gaze="左侧同伴", action="站在同一伞下，双手自然垂下", expression="淡淡微笑")],
             [dict(character_id="C001", position="画面左侧背影", gaze="前方街道", action="右手举伞并向前行走", expression="背面不可见"),
              dict(character_id="C002", position="画面右侧背影", gaze="前方街道", action="在同一伞下向前行走", expression="背面不可见")]]
    panels = []
    for i in range(4):
        panels.append(dict(id=f"P001-K{i+1:02}", page_id="P001", number=i+1, purpose=beats[i], layout="按阅读顺序排列",
            aspect_ratio=["3:2", "1:1", "3:2", "16:9"][i], shot=shots[i], angle="背面略高机位" if i==3 else "平视",
            composition=shots[i]+"；"+actions[i], scene_id="S001", characters=roles[i], main_instant=actions[i], background=backgrounds[i],
            props=[] if i==1 else [dict(prop_id="O001",position="C001右手；末两格在两人头顶",state=states[i]["O001.state"])],
            transition_in="故事开场" if i==0 else "切到背面，交代离开方向" if i==3 else "按对话顺序切镜",
            transition_out="留白结束" if i==3 else "切入下一格", state_after=states[i], drawing_description=actions[i]+backgrounds[i]))
    page = dict(id="P001", number=1, reading_direction="LTR", layout="上排两格，中间一格，下方横向收尾格", page_turn_note="独立一页短篇",
                state_in=initial, panels=panels, state_out=states[-1],
                layout_rows=[{"weight":1,"panels":[{"panel_id":"P001-K01","weight":1.4},{"panel_id":"P001-K02","weight":1}]},
                             {"weight":1,"panels":[{"panel_id":"P001-K03","weight":1}]},
                             {"weight":1.2,"panels":[{"panel_id":"P001-K04","weight":1}]}],
                lettering=[dict(id=f"P001-K{i+1:02}-T01",panel_id=f"P001-K{i+1:02}",kind="dialogue",speaker_id="C002" if i==1 else "C001",
                    text=t,bubble_position="上方留白处",order=1) for i,t in enumerate(["只剩一把了。","一把也够。","那就一起回去。"] )])
    warnings = e.validate_project(dict(workflow="direct",brief=brief,canon=canon,options=None,outline=outline,plan=plan,pages={"P001":page},version=1),complete=True)
    # All writing is complete before any persistent project mutation.
    e.save(OUT / "authored-story.json", {"payload":payload,"plan":plan,"page":page,"validation_warnings":warnings})
    (OUT / "剧本与分镜.txt").write_text(STORY_TEXT+"\n逐格镜头：\n"+"\n".join(f"{i+1}. {shots[i]}：{actions[i]}" for i in range(4)),encoding="utf-8")
    def get(path):
        with urllib.request.urlopen("http://127.0.0.1:8765"+path,timeout=20) as r: return json.load(r)
    token = get("/api/bootstrap")["token"]
    def action(name, **values):
        req = urllib.request.Request("http://127.0.0.1:8765/api/action",data=json.dumps({"action":name,**values},ensure_ascii=False).encode(),
             headers={"Content-Type":"application/json","X-Studio-Token":token})
        with urllib.request.urlopen(req,timeout=30) as r: return json.load(r)
    if not receipt.get("project"):
        created = action("create_story",result={"payload":payload,"original":STORY_TEXT,"mode":"authored_by_codex","metrics":{}},
                         drawing_settings={"mode":"none","checkpoint":MODEL,"items":[],"hand_detail":False})
        receipt.update(project=created["project"], title=brief["title"])
        e.save(receipt_path,receipt)
    pid = receipt["project"]
    for stage,target,value in [("plan","",plan),("page","P001",page)]:
        project = repo.get(pid)
        existing = project.get("pages",{}).get(target) if stage=="page" else project.get(stage)
        if existing is not None:
            assert existing==value, "Existing stage differs; inspect instead of overwriting"
            continue
        candidate = action("propose",project=pid,stage=stage,target=target,payload=value,base_hash=e.digest(project))
        action("accept",project=pid,candidate=candidate["id"])
    document = prompt_exchange.export_storyboard(repo,pid)
    positives = [
        "Solo medium-long shot, eye level, one adult woman shown from head to knees under a shop awning. Exusiai looks down with a slightly sheepish smile at the single CLOSED navy-blue umbrella held vertically in her right hand. Short red hair, orange eyes, human ears, a halo; fully clothed in a short red jacket over a white shirt and black trousers. Warm wooden shop door behind her, cool evening rain outside. Anime illustration. Only one person.",
        "Tight head-and-shoulders portrait of one adult woman, three-quarter view facing left. Texas the Omertosa glances toward someone outside the left edge with a calm, faint smile. Long black hair, yellow eyes, exactly one pair of black wolf ears fully inside the frame, a few raindrops on her hair. Fully clothed navy coat with a high collar. Softly blurred warm shop window behind her. Anime illustration. Only one person is visible.",
        "Medium two-person shot, eye level, both adults shown from head to waist beneath ONE open navy-blue umbrella, its whole canopy inside the frame. LEFT: Exusiai, short red hair, orange eyes, human ears and a halo, red jacket over a white shirt; her right hand holds the single umbrella shaft between the two women. RIGHT: Texas the Omertosa, long black hair, yellow eyes, black wolf ears, navy coat, both hands relaxed at her sides. They stand shoulder to shoulder and smile at each other. The umbrella tilts toward the woman on the right. Both fully clothed, empty shop entrance, evening rain, anime illustration.",
        "Rear-view full-body long shot, slightly elevated camera, exactly two adult women walking away together along an empty wet stone street. Their complete figures including boots occupy about half the picture height. LEFT: Exusiai has short red hair, human ears and a halo, wears a red jacket and black trousers, and holds the one navy-blue umbrella above both women with her right hand. RIGHT: Texas the Omertosa has long black hair and black wolf ears, wears a long navy coat and black trousers, and walks under the same umbrella with empty hands. Faces are hidden by the rear view. Warm shop-window reflections on wet paving, light evening rain, continuous anime street scene."
    ]
    negatives = ["extra person, crowd, duplicate person, wolf ears, animal ears, extra umbrella, open umbrella, text, speech bubble, watermark",
                 "extra person, background people, duplicate person, red hair, halo, cropped ear tips, umbrella, text, speech bubble, watermark",
                 "extra person, crowd, duplicate person, extra umbrella, two umbrellas, sitting, text, speech bubble, watermark",
                 "extra person, crowd, background pedestrians, duplicate person, extra umbrella, two umbrellas, front view, portrait, cropped feet, text, speech bubble, watermark"]
    for row,pos,neg in zip(document["panels"],positives,negatives): row.update(positive=pos,negative=neg)
    e.save(OUT/"storyboard-prompts.json",document)
    check = action("preview_prompt_import",project=pid,document=document)
    assert check["valid"],check["errors"]
    if not receipt.get("bundle"):
        bundle = action("save_prompt_import",project=pid,document=document,preview_hash=check["preview_hash"],confirm=True)
        receipt["bundle"]=bundle["id"]
    receipt.update(status="prepared_not_started",candidate_count=2,image_count=8,checkpoint=MODEL,
                   prompt_warnings=check["warnings"],storyboard_warnings=warnings)
    e.save(receipt_path,receipt)
    print(json.dumps(receipt,ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
