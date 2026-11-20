"""Import the installed character LoRAs into the versioned character library.

Prepare downloads and provenance first; apply only complete records. Existing
cards are preserved, and the original labels and cards are backed up once.
"""
import argparse
import base64
import concurrent.futures
import copy
import hashlib
import io
import json
import re
import struct
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from comic_workflow import engine as e
from comic_workflow.characters import CharacterLibrary
from comic_workflow.lora_inspection import inspect_lora
from comic_workflow.lora_names import EXTRA_IDENTITIES, display_name, identities

DEST = ROOT / "data/character-import"
SOURCES = ROOT / "data/lora-curation/sources"
GAMES = {"原神": "genshin impact", "明日方舟": "arknights"}
ALIASES = {("明日方舟", "机械师"): "Mechanist",
           ("原神", "留云借风真君"): "闲云",
           ("原神", "芙宁娜与芙卡洛斯"): "芙宁娜",
           ("原神", "荧与空"): "荧"}
VISUAL = ("identity", "face", "hair", "eyes", "body", "clothing", "accessories", "palette", "marks")


def clean(text):
    if text is None or text == "None":
        return ""
    text = re.sub(r"<[^>]+>", "", str(text))
    return text.replace("\\n", "\n").strip()


def fetch(url, destination, binary=False):
    path = Path(destination)
    if path.is_file():
        return path.read_bytes() if binary else e.read(path)
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "LocalDraw-CharacterLibrary/1.0"})
            with urllib.request.urlopen(request, timeout=30) as response:
                data = response.read()
            if not binary:
                data = json.loads(data)
                e.save(path, data)
            else:
                with Image.open(io.BytesIO(data)) as image:
                    image.verify()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            return data
        except Exception:
            if attempt == 2:
                raise
            time.sleep(attempt + 1)


def training_tags(path):
    with path.open("rb") as file:
        length = struct.unpack("<Q", file.read(8))[0]
        if not 2 <= length <= 16_000_000:
            return []
        metadata = json.loads(file.read(length)).get("__metadata__", {})
    counts = {}
    try:
        for dataset in json.loads(metadata.get("ss_tag_frequency", "{}") or "{}").values():
            for tag, count in dataset.items():
                tag = tag.replace("_", " ").replace("\\(", "(").replace("\\)", ")").strip()
                if not re.search(r"[\u3400-\u9fff]", tag):
                    counts[tag] = counts.get(tag, 0) + int(count)
    except (ValueError, TypeError, AttributeError):
        pass
    threshold = max(counts.values(), default=0) * 0.05
    return [tag for tag, count in sorted(counts.items(), key=lambda item: -item[1]) if count >= threshold]


def source_tables():
    return (e.read(SOURCES / "genshin-cn.json"),
            e.read(SOURCES / "genshin-en.json"),
            e.read(SOURCES / "arknights-game.json"),
            e.read(DEST / "research/handbook.json"))


def plan():
    roster = identities()
    labels = e.read(ROOT / "data/lora-labels.json")
    chosen = {}
    for row in e.read(ROOT / "data/lora-curation/selection.json")["characters"]:
        choice = row.get("selected") or {}
        if choice.get("relative_path"):
            chosen[(row["character"]["game"], row["character"]["name"])] = choice
    groups, styles = {}, []
    for path in sorted((ROOT / "models/loras").rglob("*.safetensors")):
        file = path.relative_to(ROOT / "models/loras").as_posix()
        parts = file.split("/")
        game = parts[1] if parts[0] in ("anime", "sdxl", "Anima") and len(parts) > 2 else parts[0]
        name = display_name(file)
        if EXTRA_IDENTITIES.get((game, name), {}).get("is_style"):
            styles.append(file)
            continue
        name = ALIASES.get((game, name), name)
        group = groups.setdefault((game, name), {"game": game, "name": name, "files": []})
        inspection = inspect_lora(path)
        group["files"].append({"file": file, "compatible": inspection["structurally_compatible"],
                               "label": labels.get(file, {}), "tags": training_tags(path)})
    cn, en, ak, handbook = source_tables()
    wiki_path = DEST / "research/genshin-stories.json"
    wiki = e.read(wiki_path) if wiki_path.exists() else {}
    voices = {}
    voice_path = DEST / "research/ak-voice.json"
    if voice_path.exists():
        for line in e.read(voice_path).get("charWords", {}).values():
            if line.get("voiceTitle") in ("交谈1", "交谈2", "任命助理"):
                voices.setdefault(line["charId"], []).append(line["voiceText"])
    cn = {c["name"]: c for c in cn}
    en = {c["id"]: c for c in en}
    ak = {c["name"]: dict(c, id=key) for key, c in ak.items()
          if c.get("name") and key.startswith("char_") and c.get("profession") not in ("TOKEN", "TRAP")}
    for key, group in groups.items():
        choice = chosen.get(key, {})
        existing_owner = [f for f in group["files"] if f["label"].get("character_id")]
        compatible = [f for f in group["files"] if f["compatible"]]
        preferred = next((f for f in compatible if f["file"] == choice.get("relative_path")), None)
        if group["name"] == "阿米娅":
            preferred = next((f for f in compatible if f["file"].endswith("_amiya_pro.safetensors")), preferred)
        preferred = preferred or next((f for f in existing_owner if f["compatible"]), None)
        # Prefer single-identity weights over a combined-character legacy model.
        preferred = preferred or next((f for f in compatible if display_name(f["file"]) == group["name"]), None)
        preferred = preferred or next(iter(compatible or group["files"]), None)
        group["primary_file"] = preferred["file"]
        group["preferred_tags"] = preferred["tags"]
        group["trigger"] = (choice.get("triggers") or [preferred["label"].get("trigger_words", "")])[0]
        identity = roster.get(key, EXTRA_IDENTITIES.get(key, {}))
        group["english"] = identity.get("english", "")
        group["lora_source"] = choice.get("page") or preferred["label"].get("source_page", "")
        if group["game"] == "原神":
            group["game_data"] = cn.get(group["name"], {})
            english = en.get(group["game_data"].get("id"), {})
            group["english"] = group["english"] or english.get("name", "")
            group["wiki_stories"] = wiki.get(group["name"], {})
        else:
            group["game_data"] = ak.get(group["name"], {})
            if group["name"] == "Mechanist":
                group["game_data"] = next((c for c in ak.values() if c.get("appellation") == "Mechanist"), {})
            group["english"] = group["english"] or group["game_data"].get("appellation", "")
            group["handbook"] = handbook["handbookDict"].get(group["game_data"].get("id"), {})
            group["voice_lines"] = voices.get(group["game_data"].get("id"), [])[:3]
    result = {"groups": list(groups.values()), "global_loras": styles,
              "installed_loras": sum(len(g["files"]) for g in groups.values()) + len(styles)}
    e.save(DEST / "plan.json", result)
    return result


def wiki_assets(groups):
    assets = {}
    names = [g["name"] for g in groups if g["game"] == "明日方舟" and g["name"] not in ("佩丽卡", "陈千语")]
    for start in range(0, len(names), 40):
        batch = names[start:start + 40]
        url = "https://wiki.biligame.com/arknights/api.php?" + urllib.parse.urlencode({
            "action": "query", "format": "json", "prop": "imageinfo", "iiprop": "url|size|sha1",
            "titles": "|".join("文件:Pack " + n + " skin 0 0.png" for n in batch)})
        data = fetch(url, DEST / f"research/wiki-art-{start}.json")
        for page in data["query"]["pages"].values():
            if page.get("imageinfo"):
                name = page["title"].removeprefix("文件:Pack ").removesuffix(" skin 0 0.png")
                assets[name] = page["imageinfo"][0]
        print(f"立绘索引 {min(start + 40, len(names))}/{len(names)}", flush=True)
    names = [g["name"] for g in groups if g["game"] == "原神"]
    for start in range(0, len(names), 8):
        batch = names[start:start + 8]
        titles = ["文件:" + n + "立绘.png" for n in batch]
        if "罗诺瓦" in batch:
            titles += ["文件:若娜瓦.png"]
        titles += ["文件:" + n + ".png" for n in batch if n in ("伊斯塔露", "留云借风真君", "派蒙")]
        url = "https://wiki.biligame.com/ys/api.php?" + urllib.parse.urlencode({
            "action": "query", "format": "json", "prop": "imageinfo", "iiprop": "url|size|sha1",
            "titles": "|".join(titles)})
        data = fetch(url, DEST / f"research/genshin-art-{start}.json")
        for page in data["query"]["pages"].values():
            if page.get("imageinfo"):
                name = page["title"].removeprefix("文件:").removesuffix(".png").removesuffix("立绘")
                name = "罗诺瓦" if name == "若娜瓦" else name
                asset = page["imageinfo"][0]
                if min(asset.get("width", 0), asset.get("height", 0)) >= 500:
                    assets["genshin:" + name] = asset
        print(f"原神立绘索引 {min(start + 8, len(names))}/{len(names)}", flush=True)
    return assets


def artwork_choices(group, assets):
    name, game, data = group["name"], group["game"], group["game_data"]
    candidates = []
    special_path = DEST / "research/special-artwork.json"
    special = e.read(special_path).get(name) if special_path.exists() else None
    if special:
        candidates.append((special["url"], special["source_type"], special["variant"]))
    if game == "原神":
        images = data.get("images", {})
        if assets.get("genshin:" + name):
            candidates.append((assets["genshin:" + name]["url"], "official_game_illustration_rehosted_by_game_wiki", "default_illustration"))
        for key in ("cover1", "cover2", "portrait", "card"):
            if images.get(key):
                candidates.append((images[key], "official_character_illustration", key))
        splash = images.get("filename_gachaSplash")
        if not splash and images.get("filename_icon"):
            splash = images["filename_icon"].replace("UI_AvatarIcon_", "UI_Gacha_AvatarImg_")
        if splash:
            for host in ("https://api.ambr.top/assets/UI/", "https://homdgcat.wiki/images/UI/"):
                candidates.append((host + splash + ".png", "official_game_asset_mirror", "gacha_splash"))
    else:
        if name in ("佩丽卡", "陈千语"):
            html = (DEST / "research/endfield.html").read_text(encoding="utf-8")
            key = "perlica" if name == "佩丽卡" else "chen"
            match = re.search('data-key="' + key + '" style="background-image:url\\(([^)]+)\\)', html)
            if match:
                candidates.append((match[1], "official_website_character_illustration", "official_website"))
        asset = assets.get(name)
        if asset:
            candidates.append((asset["url"], "official_game_illustration_rehosted_by_game_wiki", "default_e0"))
        if data.get("id"):
            candidates.append(("https://raw.githubusercontent.com/fexli/ArknightsResource/main/charpack/" + data["id"] + "_1.png",
                               "official_game_asset_mirror", "default_e0"))
    return candidates


def prepare_art(group, assets):
    destination = DEST / "art" / group["game"] / (group["name"] + ("-standing" if group["game"] == "原神" else "") + ".png")
    provenance = destination.with_suffix(".json")
    if destination.exists() and provenance.exists():
        return dict(group, artwork=e.read(provenance), status="prepared")
    errors = []
    for url, source_type, variant in artwork_choices(group, assets):
        try:
            raw = fetch(url, destination, binary=True)
            with Image.open(io.BytesIO(raw)) as image:
                dimensions = list(image.size)
            host = urllib.parse.urlsplit(url).hostname or ""
            record = {"asset_url": url, "source_type": source_type, "variant": variant,
                      "official_host": host.endswith((".mihoyo.com", ".hoyoverse.com", ".hypergryph.com", ".hycdn.cn")),
                      "sha256": hashlib.sha256(raw).hexdigest(), "dimensions": dimensions,
                      "file": destination.relative_to(ROOT).as_posix(),
                      "source_page": (group["game_data"].get("url") or
                                      ("https://endfield.hypergryph.com/operator" if group["name"] in ("佩丽卡", "陈千语") else
                                       "https://wiki.biligame.com/" + ("ys/" if group["game"] == "原神" else "arknights/") + urllib.parse.quote(group["name"])))}
            e.save(provenance, record)
            return dict(group, artwork=record, status="prepared")
        except Exception as exc:
            errors.append({"url": url, "error": str(exc)[:160]})
    return dict(group, status="missing_artwork", errors=errors)


def prepare():
    result = plan()
    assets = wiki_assets(result["groups"])
    prepared = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        tasks = {pool.submit(prepare_art, group, assets): group for group in result["groups"]}
        for task in concurrent.futures.as_completed(tasks):
            group = task.result()
            prepared.append(group)
            if len(prepared) % 10 == 0 or group["status"] != "prepared":
                print(f"立绘 {len(prepared)}/{len(tasks)} · {group['name']} · {group['status']}", flush=True)
            e.save(DEST / "prepared.json", dict(result, groups=prepared))
    e.save(DEST / "prepared.json", dict(result, groups=sorted(prepared, key=lambda g: (g["game"], g["name"]))))


def appearance(group):
    tags = group["preferred_tags"]
    trigger = group["trigger"].replace("\\(", "(").replace("\\)", ")")
    # Public model descriptions often list mutually exclusive costumes. Use the
    # first authored default trigger; training tags fill appearance fields only.
    authored = [t.strip().replace("_", " ") for t in re.split(r"[,，\n]", trigger) if t.strip()]
    unique = list(dict.fromkeys(authored + tags))
    def select(pattern, limit=4):
        match = lambda t: len(t) < 95 and re.fullmatch(pattern, t, re.I)
        authored_matches = [t for t in authored if match(t)]
        return ", ".join((authored_matches or [t for t in tags if match(t)])[:limit]) or "unknown"
    english = group["english"] or next((t.split("(")[0].strip() for t in tags if GAMES[group["game"]] in t.lower()), "")
    if not english:
        english = Path(group["primary_file"]).stem.split("_", 1)[-1].split("-", 1)[0].replace("_", " ")
    token = authored[0] if authored and not re.search(r"[\u3400-\u9fff]", authored[0]) else ""
    identity = english.lower() + " (" + GAMES[group["game"]] + ")"
    if token and len(token) < 95 and not re.search(r"hair|eyes|dress|gloves|solo|1girl|1boy|all in one", token, re.I):
        identity = token if GAMES[group["game"]] in token.lower() else token + ", " + identity
    color = r"(?:black|white|blue|light blue|dark blue|pink|red|orange|green|purple|blonde|brown|silver|grey|gray|yellow|auburn|two-tone|multicolored|gradient)"
    hair_color = select(color + r" hair", 1)
    hair_length = select(r"long hair|short hair|medium hair|very long hair", 1)
    hair_style = select(r"ponytail|side ponytail|twin braids|braid|twin tails|twintails|ahoge|hair bun|double bun|straight hair|wavy hair", 1)
    hair_bangs = select(r"bangs|blunt bangs|sidelocks|hair between eyes|side-swept bangs", 1)
    gender = group["game_data"].get("gender")
    base = story_sections(group).get("基础档案", "")
    gender = gender or ("女" if "【性别】女" in base else "男" if "【性别】男" in base else "")
    body = "1girl" if gender in ("女", "Female") else "1boy" if gender in ("男", "Male") else select(r"1girl|1boy", 1)
    def clothing():
        clothing_pattern = r"dress|coat|jacket|shirt|vest|skirt|shorts|pants|trousers|sleeves|boots|shoes|pantyhose|thighhighs|bodysuit|leotard|kimono|uniform"
        authored_clothes = [t for t in authored if re.search(clothing_pattern, t)]
        if authored_clothes:
            return ", ".join(authored_clothes[:10])
        fields = [select(r"(?:" + color + r" )?(?:dress|frilled dress|leotard|bodysuit|kimono|uniform)", 1),
                  select(r"(?:" + color + r" )?(?:coat|jacket|shirt|vest)|hooded coat|hooded jacket|bulletproof vest", 2),
                  select(r"(?:" + color + r" )?(?:skirt|shorts|pants|trousers)|pleated skirt|hakama skirt", 1),
                  select(r"detached sleeves|wide sleeves|long sleeves|short sleeves|sleeveless", 1),
                  select(r"(?:" + color + r" )?(?:pantyhose|thighhighs|stockings)|bare legs", 1),
                  select(r"(?:" + color + r" )?(?:boots|shoes)|high heels", 1)]
        return ", ".join(v for v in fields if v != "unknown") or "unknown"
    return {"identity": identity,
            "face": select(r"(?:delicate|sharp|soft|refined) facial features|mole(?: under .+)?|freckles", 2),
            "hair": ", ".join(v for v in (hair_color, hair_length, hair_style, hair_bangs) if v != "unknown") or "unknown",
            "eyes": select(color + r" eyes|heterochromia|mismatched pupils", 1),
            "body": body,
            "clothing": clothing(),
            "accessories": select(r"(?:" + color + r" )?(?:hair ornament|hair ribbon|hair bow|gloves|hat|headwear|ribbon|bow|choker|necklace|earrings|brooch|belt|mask|headdress)|neck bell|bell|hairclip|hair flower|fingerless gloves|utility belt|top hat|conical hat", 4),
            "palette": "unknown",  # Colors are already in their appearance fields.
            "marks": select(r"halo|ahoge|heterochromia|mismatched gloves|dragon horns|rabbit ears|fox ears|cat ears|wolf ears|goat horns|demon horns|giraffe horns|horns|animal ears|pointy ears|dragon tail", 2)}


def story_sections(group):
    sections = {section["storyTitle"]: "\n".join(clean(s["storyText"]) for s in section.get("stories", []))
                for section in group.get("handbook", {}).get("storyTextAudio", [])}
    wiki = group.get("wiki_stories", {})
    for source, target in (("角色详细", "客观履历"), ("角色故事1", "档案资料一"), ("角色故事2", "档案资料二")):
        if wiki.get(source):
            sections[target] = clean(wiki[source])
    return sections


def profile(group):
    visual = appearance(group)
    data = group["game_data"]
    sections = story_sections(group)
    base = sections.get("基础档案", "")
    height = re.search(r"【身高】([^\n]+)", base)
    if height:
        visual["body"] = (visual["body"] + ", " if visual["body"] != "unknown" else "") + "height " + height[1]
    description = clean(data.get("description", "")) if group["game"] == "原神" else clean(data.get("itemDesc", ""))
    resume = sections.get("客观履历", "") or clean(data.get("itemUsage", "")) or description
    personality = sections.get("档案资料一", "")
    if not personality:
        personality = description or "目前来源未提供完整性格档案。演绎时以原作剧情和所选角色资料为依据，不将同名角色或其他版本的性格移入本卡。"
    # Retain the official text in source.notes; fields remain readable in the UI.
    first_paragraphs = [p.strip() for p in personality.split("\n") if p.strip()]
    personality = "\n".join(first_paragraphs[:3])[:1000]
    affiliation = data.get("affiliation", "")
    background = resume[:1300] or "身份及经历尚未从可核验资料提取，采用本卡来源链接中的角色版本；不补写未知年龄或时间线。"
    if group["game"] == "原神":
        background = "\n".join(t for t in [data.get("title", ""), description, affiliation,
                               "所属地区：" + data["region"] if data.get("region") else ""] if t)
    result = dict(visual,
                personality=personality,
    voice="本卡演绎建议：围绕“" + (description[:60] or resume[:60] or group["name"]) + "”这一人物基础，把握表达方式。" +
                      ("原作语音参考：" + clean(group["voice_lines"][0])[:110] if group.get("voice_lines") else "以对应人物档案中的性格、职业和经历控制语气。") +
                      "创作台词明确作为原创示例，避免套用其他角色口头禅。",
                motivation=(resume[:450] or description[:450] or "动机依据原作剧情；当前资料未确认的个人愿望与弱点不作定论。"),
                background=background,
                relationships=("所属组织：" + affiliation + "。具体关系依照剧情版本。" if affiliation else
                               "人物关系依据原作档案及当前时间线。合作、敌对、亲属关系需有原作依据，不默认恋爱关系。"),
                constraints="保持" + group["name"] + "的原作身份和辨识特征；默认立绘版本见来源记录。外观提示词取自对应 LoRA 的作者触发词与训练标签，非官方文字设定；切换皮肤或 LoRA 时核对服装。未知年龄、经历、对白和人物关系不编造。同人扩展明确标为创作建议。",
                observed=[],
                suggested=["绘图时使用英文外观字段；性格、背景和人物关系供剧本使用。", "同一角色的不同 LoRA 可切换，默认仅启用推荐版本。"],
                unknowns=["训练标签反映 LoRA 数据集，部分服装细节可能与默认官方立绘不同。",
                          "未逐字收录的语音、确切年龄和场景时间线需按原作确认。"])
    override_path = DEST / "research/profile-overrides.json"
    if override_path.exists():
        result.update(e.read(override_path).get(group["name"], {}))
    artwork = group.get("artwork", {})
    if "no_standard_standing" in artwork.get("variant", ""):
        result["unknowns"].append("当前参考图为官方角色头像；尚未取得这一形象的标准全身立绘。")
    return result


def normalized_art(path):
    with Image.open(path) as original:
        image = original.convert("RGBA")
        bounds = image.getchannel("A").getbbox()
        if bounds:
            image = image.crop(bounds)
        image.thumbnail((1000, 1000))
        size = max(image.size)
        square = Image.new("RGBA", (size, size), "white")
        square.alpha_composite(image, ((size - image.width) // 2, (size - image.height) // 2))
        output = io.BytesIO()
        square.convert("RGB").save(output, format="PNG")
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")


def apply():
    prepared = e.read(DEST / "prepared.json")
    latest = {(g["game"], g["name"]): g for g in plan()["groups"]}
    prepared["groups"] = [dict(g, **latest[(g["game"], g["name"])]) for g in prepared["groups"]]
    library = CharacterLibrary()
    cards = {c["name"]: c for c in library.listing()}
    labels_path = ROOT / "data/lora-labels.json"
    labels = e.read(labels_path)
    backup = DEST / "backup"
    if not (backup / "lora-labels.json").exists():
        e.save(backup / "lora-labels.json", labels)
        e.save(backup / "original-cards.json", list(cards.values()))
    outcomes = []
    for group in prepared["groups"]:
        name = group["name"]
        card = cards.get(name)
        if group["status"] != "prepared" and not card:
            outcomes.append({"name": name, "status": "missing_artwork"})
            continue
        if not card:
            image_id = library.upload(normalized_art(ROOT / group["artwork"]["file"]))["image_id"]
            source = {"mode": "image", "personality": profile(group)["personality"],
                      "text": group["name"] + "｜原作资料、官方立绘与 LoRA 作者提示词整理",
                      "game": "明日方舟：终末地" if name in ("佩丽卡", "陈千语") else group["game"], "official_artwork": group["artwork"],
                      "lora_files": [f["file"] for f in group["files"]],
                      "primary_lora": group["primary_file"], "lora_source": group["lora_source"],
                      "notes": story_sections(group),
                      "profile_basis": "原作游戏资料；英文外观依据 LoRA 触发词及训练标签"}
            card = library.save(name, profile(group), source, image_id)
            cards[name] = card
        for item in group["files"]:
            file = item["file"]
            label = copy.deepcopy(labels.get(file, {}))
            variant = dict(group, preferred_tags=item["tags"], trigger=label.get("trigger_words", ""))
            label.update(character_id=card["id"], character=name,
                         preferred=file == group["primary_file"],
                         appearance_prompt=", ".join(v for v in appearance(variant).values() if v != "unknown"))
            labels[file] = label
        outcomes.append({"name": name, "character_id": card["id"], "status": "linked",
                         "existing_card": card["id"] in [c["id"] for c in e.read(backup / "original-cards.json")],
                         "image_id": card["image_id"], "primary_lora": group["primary_file"],
                         "files": [f["file"] for f in group["files"]]})
        if len(outcomes) % 20 == 0:
            print(f"建档/关联 {len(outcomes)}/{len(prepared['groups'])}", flush=True)
    for file in prepared["global_loras"]:
        labels[file] = dict(labels.get(file, {}), character_id="", character="", preferred=False)
    with __import__("comic_workflow.repository", fromlist=["LOCK"]).LOCK:
        e.save(ROOT / "data/lora-label-history" / (e.stamp() + ".json"), e.read(labels_path))
        e.save(labels_path, labels)
    report = {"installed_loras": prepared["installed_loras"], "characters": outcomes,
              "global_loras": prepared["global_loras"], "total_cards": len(library.listing()),
              "linked_files": sum(len(r.get("files", [])) for r in outcomes),
              "missing": [r["name"] for r in outcomes if r["status"] != "linked"]}
    e.save(DEST / "result.json", report)
    print(json.dumps({k: v for k, v in report.items() if k not in ("characters", "global_loras")}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("plan", "prepare", "apply"))
    args = parser.parse_args()
    if args.action == "plan":
        result = plan()
        print(len(result["groups"]), "characters;", len(result["global_loras"]), "global weights")
    elif args.action == "prepare":
        prepare()
    else:
        apply()
