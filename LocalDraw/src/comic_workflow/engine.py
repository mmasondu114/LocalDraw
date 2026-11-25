"""Local comic workflow. Draft -> validate -> explicit accept, with immutable history."""

import copy, datetime, hashlib, json, pathlib, re
import tempfile
import time
import jsonschema
from .schemas import SCHEMAS

ROOT = pathlib.Path(__file__).resolve().parent


def read(p):
    return json.loads(pathlib.Path(p).read_text(encoding="utf-8-sig"))


def encoded(x):
    return json.dumps(x, ensure_ascii=False, indent=2)


def digest(x):
    return hashlib.sha256(
        json.dumps(x, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def story_fingerprint(project):
    return digest(
        {
            key: project.get(key)
            for key in ["brief", "canon", "plan", "pages", "script", "script_source"]
        }
    )


def save(p, x):
    p = pathlib.Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    # Readers/antivirus can briefly deny replacement on Windows. Keep the old
    # complete JSON visible, use a private temp file, and retry only replacement.
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=p.parent,
            prefix=p.name + ".",
            suffix=".tmp",
            delete=False,
        ) as stream:
            tmp = pathlib.Path(stream.name)
            stream.write(encoded(x) + "\n")
        for attempt in range(8):
            try:
                tmp.replace(p)
                break
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(min(0.02 * 2**attempt, 0.25))
    finally:
        if tmp is not None:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass


def stamp():
    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")


def need(ok, msg):
    if not ok:
        raise ValueError(msg)


def schema(stage, x):
    jsonschema.Draft202012Validator(SCHEMAS[stage]).validate(x)


def ids(items):
    return [x["id"] for x in items]


def unique(values, label):
    need(len(values) == len(set(values)), f"{label} 编号重复")


def validate_canon(c):
    schema("canon", c)
    for key, prefix in [("characters", "C"), ("scenes", "S"), ("props", "O")]:
        unique(ids(c[key]), key)
        for x in c[key]:
            need(x["id"].startswith(prefix), f"{key} 编号须以 {prefix} 开头")
    for c in c["characters"]:
        if c["origin"] == "existing_work" and c["verified_facts"]:
            need(bool(c["source_notes"]), "原作事实须注明资料来源")


def turn_after(brief, n):
    if brief["page_mode"] == "single_page":
        return True
    side = (
        brief["first_story_page_side"]
        if n % 2
        else ("left" if brief["first_story_page_side"] == "right" else "right")
    )
    return side == ("right" if brief["reading_direction"] == "LTR" else "left")


def validate_plan(p, project):
    schema("plan", p)
    b = project["brief"]
    c = project["canon"]
    need(len(p["pages"]) == b["page_count"], "页数不符")
    for n, item in enumerate(p["pages"], 1):
        need(item["id"] == f"P{n:03}" and item["number"] == n, "页编号或顺序不符")
        if b.get("panel_mode", "fixed") == "fixed":
            need(item["panel_count"] == b["panels_per_page"], "规划格数不符")
        else:
            need(1 <= item["panel_count"] <= 9, "自由分格每页须为 1–9 格")
        need(set(item["scene_ids"]) <= set(ids(c["scenes"])), "未知场景")
        need(set(item["character_ids"]) <= set(ids(c["characters"])), "未知角色")
        need(
            item["turn_after"] == turn_after(b, n),
            f"{item['id']} 的 turn_after 必须为 {str(turn_after(b, n)).lower()}，它表示物理可翻页位置而非悬念",
        )


def panel_start_state(page, panel):
    """Confirmed pages number panels in reading order, starting at one."""
    number = panel["number"]
    return page["state_in"] if number == 1 else page["panels"][number - 2]["state_after"]


def validate_page(page, project, start=None):
    schema("page", page)
    c = project["canon"]
    n = page["number"]
    pid = f"P{n:03}"
    need(
        project.get("plan") and 1 <= n <= len(project["plan"]["pages"]),
        "本页不在已确认分页内",
    )
    plan = project["plan"]["pages"][n - 1]
    need(page["id"] == plan["id"] == pid, "页编号不符")
    need(
        page["reading_direction"] == project["brief"]["reading_direction"],
        "阅读方向不符",
    )
    expected_ids = [f"{pid}-K{i:02}" for i in range(1, plan["panel_count"] + 1)]
    need(len(page["panels"]) == plan["panel_count"],
         f"漏格或多格：{pid} 必须有 {plan['panel_count']} 格，实际 {len(page['panels'])} 格；"
         f"必需编号：{', '.join(expected_ids)}。不得删除画格来匹配排版。")
    if page.get("layout_rows"):
        sequence = [x["panel_id"] for row in page["layout_rows"] for x in row["panels"]]
        need(sequence == ids(page["panels"]), "排版行必须按阅读顺序包含每格恰好一次")
    need(
        set(page["state_in"]) == set(c["initial_state"]),
        "状态键缺失或擅自增加；请先修订设定",
    )
    if start is not None:
        need(page["state_in"] == start, "本页初始状态与前页结尾不一致")
    charids = set(ids(c["characters"]))
    sceneids = set(ids(c["scenes"]))
    propids = set(ids(c["props"]))
    warnings = []
    panelids = []
    for i, p in enumerate(page["panels"], 1):
        expected = f"{pid}-K{i:02}"
        need(
            p["id"] == expected and p["page_id"] == pid and p["number"] == i,
            "格编号/顺序错误",
        )
        need(p["scene_id"] in sceneids, "格引用未知场景")
        for field in [
            "purpose",
            "layout",
            "shot",
            "angle",
            "composition",
            "main_instant",
            "background",
            "transition_in",
            "transition_out",
            "drawing_description",
        ]:
            need(p[field].strip(), expected + " 必填字段为空：" + field)
        need(
            not any(
                marker in p["drawing_description"]
                for marker in [
                    "最终输出仅为纯JSON",
                    "省略大量重复",
                    "同上省略",
                    "保证承诺担保",
                ]
            ),
            expected + " 画面描述混入模型自述或重复校验废话，请重新生成这段描述",
        )
        need(
            set(x["character_id"] for x in p["characters"]) <= charids, "格引用未知角色"
        )
        unique([x["character_id"] for x in p["characters"]], expected + "人物")
        need(set(x["prop_id"] for x in p["props"]) <= propids, "格引用未登记道具")
        unique([x["prop_id"] for x in p["props"]], expected + "道具")
        need(
            set(p["state_after"]) == set(c["initial_state"]),
            expected + " 状态快照不完整",
        )
        panelids.append(expected)
        if re.search(r"(然后|接着|随后|再走|再转身)", p["main_instant"]):
            warnings.append(expected + " 主要瞬间可能包含连续动作，请人工检查可画性")
        if i > 1:
            prev = page["panels"][i - 2]
            if prev["scene_id"] == p["scene_id"]:
                for ch in p["characters"]:
                    old = next(
                        (
                            x
                            for x in prev["characters"]
                            if x["character_id"] == ch["character_id"]
                        ),
                        None,
                    )
                    if old and (
                        ("左" in old["position"] and "右" in ch["position"])
                        or ("右" in old["position"] and "左" in ch["position"])
                    ):
                        warnings.append(
                            expected
                            + " "
                            + ch["character_id"]
                            + " 左右位置变化，检查跨轴或移动交代"
                        )
                previous_props = {x["prop_id"] for x in prev["props"]}
                current_props = {x["prop_id"] for x in p["props"]}
                if previous_props - current_props:
                    warnings.append(
                        expected
                        + " 道具未入镜 "
                        + ",".join(sorted(previous_props - current_props))
                        + "；检查是否因取景隐藏或遗漏"
                    )
        if i >= 3 and len({x["shot"] for x in page["panels"][i - 3 : i]}) == 1:
            warnings.append(expected + " 连续三格同景别，建议复核节奏")
    need(
        page["state_out"] == page["panels"][-1]["state_after"], "页末状态不等于末格状态"
    )
    unique(ids(page["lettering"]), "文字")
    for p in panelids:
        texts = [x for x in page["lettering"] if x["panel_id"] == p]
        need(
            sorted(x["order"] for x in texts) == list(range(1, len(texts) + 1)),
            p + " 气泡顺序重复或跳号",
        )
        panel_text = " ".join(x["text"] for x in texts)
        if re.search(r"[\u3400-\u9fff]", panel_text):
            if sum(len(x["text"]) for x in texts) > 60:
                warnings.append(p + " 文字超过60字，建议拆分")
        elif len(panel_text.split()) > 55:
            warnings.append(p + " 文字超过55个英文单词，建议拆分")
        for t in texts:
            need(t["id"] == f"{p}-T{t['order']:02}", "文字编号与所属格不一致")
    for t in page["lettering"]:
        need(t["panel_id"] in panelids, "文字找不到对应格")
        need(
            t["speaker_id"] in charids
            if t["kind"] == "dialogue"
            else t["speaker_id"] is None,
            "对白说话者无效，或旁白/拟声错误引用角色",
        )
        if t["kind"] == "dialogue":
            if re.search(r"[\u3400-\u9fff]", t["text"]):
                if len(t["text"]) > 28:
                    warnings.append(t["id"] + " 对白超过28字")
            elif len(t["text"].split()) > 25:
                warnings.append(t["id"] + " 对白超过25个英文单词")
    return warnings


def validate_project(p, complete=False):
    schema("brief", p["brief"])
    if not p.get("canon"):
        need(
            not any(p.get(k) for k in ["options", "outline", "plan", "pages"]),
            "缺少已确认角色与世界观",
        )
        need(not complete, "尚未确认设定和完整页面")
        return []
    validate_canon(p["canon"])
    if p.get("options"):
        schema("options", p["options"])
        unique(ids(p["options"]["options"]), "剧情方案")
    if p.get("outline"):
        need(p.get("options") or p.get("workflow") == "direct", "先确认剧情方案")
        schema("outline", p["outline"])
        need(
            (
                p.get("workflow") == "direct"
                and p["outline"]["selected_option"] == "direct"
            )
            or p["outline"]["selected_option"]
            in ids((p.get("options") or {}).get("options", [])),
            "大纲引用未知剧情方案",
        )
    if p.get("plan"):
        need(p.get("outline"), "先确认大纲")
    if p.get("pages"):
        need(p.get("plan"), "先确认分页")
    if p.get("plan"):
        validate_plan(p["plan"], p)
    warnings = []
    state = p["canon"]["initial_state"]
    for plan_page in p.get("plan", {}).get("pages", []) if p.get("plan") else []:
        n = plan_page["number"]
        if n > 1 and plan_page["reveal"].strip() and not turn_after(p["brief"], n - 1):
            warnings.append(
                plan_page["id"] + " 揭晓与前页同属一个纸质跨页；请检查是否提前泄底"
            )
    for i, (pid, page) in enumerate(sorted(p["pages"].items()), 1):
        need(pid == f"P{i:03}", "已确认页存在空洞，须按页确认")
        warnings += validate_page(page, p, state)
        state = page["state_out"]
    if complete:
        need(len(p["pages"]) == p["brief"]["page_count"], "剧本尚有未生成页面")
    return warnings


def merged(p, stage, target, payload):
    schema(stage, payload)
    q = copy.deepcopy(p)
    if stage == "brief":
        need(not p.get("canon"), "已有下游确认内容；请创建分支后修改需求")
        q["brief"] = payload
    elif stage in ["canon", "options", "outline", "plan"]:
        predecessor = {"options": "canon", "outline": "options", "plan": "outline"}
        if stage in predecessor:
            need(p.get(predecessor[stage]), "请先确认前一阶段")
        downstream = {
            "canon": ["options", "outline", "plan", "pages"],
            "options": ["outline", "plan", "pages"],
            "outline": ["plan", "pages"],
            "plan": ["pages"],
        }
        need(
            not any(p.get(x) for x in downstream[stage]),
            "已有下游确认内容；请先 fork 新版本并明确重规划，防止无意覆盖",
        )
        if stage == "canon":
            validate_canon(payload)
        if stage == "plan":
            validate_plan(payload, p)
        q[stage] = payload
    elif stage == "page":
        need(payload["id"] == target, "候选页与目标不符")
        if target in p["pages"]:
            old = p["pages"][target]
            need(
                payload["state_in"] == old["state_in"]
                and payload["state_out"] == old["state_out"],
                "重做已确认页须保持出入状态；改变剧情请 fork 重规划",
            )
        q["pages"][target] = payload
    elif stage == "panel":
        pid = target.split("-")[0]
        page = q["pages"][pid]
        old = next((x for x in page["panels"] if x["id"] == target), None)
        need(old is not None, "找不到原格")
        new = payload["panel"]
        need(new["id"] == target, "不能修改另一格")
        need(new["state_after"] == old["state_after"], "单格修改不能改变后续状态边界")
        need(
            all(x["panel_id"] == target for x in payload["lettering"]),
            "局部修改包含其他格文字",
        )
        page["panels"] = [new if x["id"] == target else x for x in page["panels"]]
        page["lettering"] = [
            x for x in page["lettering"] if x["panel_id"] != target
        ] + payload["lettering"]
        page["lettering"].sort(key=lambda x: (x["panel_id"], x["order"]))
    elif stage == "audit":
        need(target in p["pages"], "审核目标页不存在")
        scope = {
            k: v
            for k, v in p["pages"].items()
            if abs(v["number"] - p["pages"][target]["number"]) <= 1
        }
        for issue in payload["issues"]:
            need(issue["page_id"] in scope, "审核引用了本次范围以外的页")
            if issue["panel_id"]:
                need(
                    issue["panel_id"] in ids(scope[issue["page_id"]]["panels"]),
                    "审核引用未知格",
                )
        return q
    elif stage == "drawing_prompt":
        from .framing import validate_framing

        validate_framing(payload)
        need(payload["panel_id"] == target, "绘图提示词对应格不符")
        page = p["pages"].get(target.split("-")[0], {})
        panel = next((x for x in page.get("panels", []) if x["id"] == target), None)
        need(panel is not None, "请先确认该格文字分镜")
        need(
            set(x["character_id"] for x in payload["character_anchors"])
            == set(x["character_id"] for x in panel["characters"]),
            "绘图角色锚点与该格人物不符",
        )
        need(
            payload["positive"].strip() and payload["negative"].strip(),
            "绘图提示词不能为空",
        )
        unique(
            [x["character_id"] for x in payload["character_anchors"]], "绘图角色锚点"
        )
        need(
            all(x["tags"].strip() for x in payload["character_anchors"]),
            "角色外观标签不能为空",
        )
        from .character_binding import identity_hash

        q.setdefault("drawing_prompts", {})[target] = {
            "payload": payload,
            "panel_hash": digest(panel),
            "identity_hash": identity_hash(p, panel),
        }
    validate_project(q)
    return q


def render_page(page, p):
    names = {x["id"]: x["name"] for x in p["canon"]["characters"]}
    lines = [
        f"## {page['id']} · 第{page['number']}页",
        "",
        f"阅读：{page['reading_direction']}；版式：{page['layout']}",
        f"翻页：{page['page_turn_note']}",
        "",
    ]
    labels = {
        "purpose": "剧情作用",
        "layout": "格形/大小",
        "aspect_ratio": "画格比例",
        "shot": "景别",
        "angle": "视角",
        "composition": "构图",
        "main_instant": "主要瞬间",
        "background": "背景",
        "transition_in": "承前",
        "transition_out": "启后",
        "drawing_description": "绘图内容（尚非模型专用提示词）",
    }
    for panel in page["panels"]:
        lines += [f"### {panel['id']}", ""]
        for key, label in labels.items():
            lines.append(f"- **{label}**：{panel[key]}")
        for ch in panel["characters"]:
            lines.append(
                f"- **{names[ch['character_id']]} {ch['character_id']}**：{ch['position']}；视线：{ch['gaze']}；{ch['action']}；表情：{ch['expression']}"
            )
        lines.append(
            "- **道具**："
            + "；".join(
                f"{x['prop_id']} {x['position']} {x['state']}" for x in panel["props"]
            )
        )
        for t in sorted(
            [x for x in page["lettering"] if x["panel_id"] == panel["id"]],
            key=lambda x: x["order"],
        ):
            lines.append(
                f"- **文字 {t['id']}**（{t['kind']} / {names.get(t['speaker_id'], '无说话者')}）：{t['text']}〔{t['bubble_position']}；顺序{t['order']}〕"
            )
        lines += [
            "",
            "<details><summary>本格之后的剧情状态</summary>",
            "",
            "```json",
            encoded(panel["state_after"]),
            "```",
            "</details>",
            "",
        ]
    return "\n".join(lines)


def export(p, folder):
    validate_project(p)
    folder = pathlib.Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    save(folder / "script.json", p)
    save(folder / "characters.json", p["canon"]["characters"])
    save(folder / "scenes.json", p["canon"]["scenes"])
    save(
        folder / "lettering.json",
        [x for page in p["pages"].values() for x in page["lettering"]],
    )
    save(
        folder / "states.json",
        {
            pid: {"in": page["state_in"], "out": page["state_out"]}
            for pid, page in p["pages"].items()
        },
    )
    save(
        folder / "drawing-handoff.json",
        {
            "status": "text_only_not_images",
            "image_model": None,
            "adapter_status": "pending_model_selection",
            "panels": [
                {
                    "panel_id": x["id"],
                    "description": x["drawing_description"],
                    "character_ids": [c["character_id"] for c in x["characters"]],
                    "candidates": [],
                    "requested_candidates": 6,
                    "selected_candidate": None,
                }
                for page in p["pages"].values()
                for x in page["panels"]
            ],
        },
    )
    lines = [
        f"# {p['brief']['title']}",
        "",
        "状态：文字分镜；不是可视化分镜草图或最终成图。",
        p.get("provenance", "来源未标记"),
        f"已确认 {len(p['pages'])}/{p['brief']['page_count']} 页。JSON 为正式数据源；Markdown 为阅读导出。",
        "",
        "## 创作需求",
        "",
        encoded(p["brief"]),
        "",
        "## 已确认大纲",
        "",
        encoded(p.get("outline")),
        "",
    ]
    lines += [render_page(page, p) for _, page in sorted(p["pages"].items())]
    if p.get("script"):
        lines[6:6] = ["## 已确认剧本", "", p["script"], ""]
    (folder / "script.md").write_text("\n".join(lines), encoding="utf-8")
