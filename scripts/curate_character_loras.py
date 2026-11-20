"""Reproducible character roster, SDXL candidate selection and resumable downloads.

Source responses and every considered version are retained, including missing models.
Credentials are read from the existing local token file and never logged.
"""
import argparse
import concurrent.futures
import datetime as dt
import hashlib
import importlib.util
import json
import math
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from comic_workflow import engine as e

CACHE = ROOT / "data/lora-curation/sources"
FAMILIES = {"SDXL 1.0", "Illustrious", "NoobAI", "Pony", "SDXL Lightning", "SDXL Hyper"}
ALIASES = {
    "鸿雪": ["Pozemka", "Pozёmka"], "早露": ["Rosa"], "玛恩纳": ["Mlynar"],
    "维什戴尔": ["Wisadel", "Wiš'adel", "Walter"], "缄默德克萨斯": ["Texas the Omertosa", "Texas Alter"],
    "浊心斯卡蒂": ["Skadi the Corrupting Heart", "Skadi Alter"],
    "假日威龙陈": ["Ch'en the Holungday", "Chen Alter", "Chalter"],
    "归溟幽灵鲨": ["Specter the Unchained", "Specter Alter"],
    "麒麟R夜刀": ["Kirin R Yato", "Yato Alter"], "耀骑士临光": ["Nearl the Radiant Knight", "Nearl Alter"],
    "纯烬艾雅法拉": ["Eyjafjalla the Hvit Aska", "Eyjafjalla Alter"],
    "圣约送葬人": ["Executor the Ex Foedere", "Executor Alter"],
    "百炼嘉维尔": ["Gavial the Invincible", "Gavial Alter"],
    "魔王": ["Civilight Eterna", "Theresa"], "荒芜拉普兰德": ["Lappland the Decadenza", "Lappland Alter"],
    "凯尔希": ["Kal'tsit", "Kaltsit"], "陈": ["Ch'en", "Chen"],
    "雷电将军": ["Raiden Shogun", "Raiden Ei", "Baal"],
    "流浪者": ["Wanderer", "Scaramouche"], "达达利亚": ["Tartaglia", "Childe"],
    "空": ["Aether", "Male Traveler"], "荧": ["Lumine", "Female Traveler"],
    "神里绫华": ["Kamisato Ayaka", "Ayaka"], "神里绫人": ["Kamisato Ayato", "Ayato"],
    "枫原万叶": ["Kaedehara Kazuha", "Kazuha"], "鹿野院平藏": ["Shikanoin Heizou", "Heizou"],
    "珊瑚宫心海": ["Sangonomiya Kokomi", "Kokomi"], "八重神子": ["Yae Miko", "Yae"],
    "荒泷一斗": ["Arataki Itto", "Itto"], "九条裟罗": ["Kujou Sara", "Sara"],
    "久岐忍": ["Kuki Shinobu", "Shinobu"], "胡桃": ["Hu Tao", "Hutao"],
    "梦见月瑞希": ["Yumemizuki Mizuki", "Mizuki"],
}


def normalized(text):
    text = unicodedata.normalize("NFKD", text.casefold()).translate(str.maketrans({"ł": "l"}))
    return re.sub(r"[^a-z0-9\u3400-\u9fff]", "", text)


def word_text(text):
    text = unicodedata.normalize("NFKD", text.casefold()).translate(str.maketrans({"ł":"l"}))
    return re.sub(r"[^a-z0-9\u3400-\u9fff]+", " ", "".join(c for c in text if not unicodedata.combining(c))).strip()


def fetch_json(url, cache_name, refresh=False):
    path = CACHE / (cache_name + ".json")
    if path.exists() and not refresh:
        return e.read(path)
    token_path = ROOT / "Chenkin_LoRA/civitai-token.txt"
    headers = {"User-Agent": "LocalComicStudio-Curation/1.0", "Accept": "application/json"}
    if urllib.parse.urlsplit(url).hostname == "civitai.com" and token_path.exists():
        headers["Authorization"] = "Bearer " + token_path.read_text().strip()
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=40) as response:
                data = json.load(response)
            e.save(path, data)
            return data
        except Exception:
            if attempt == 3:
                raise RuntimeError("来源请求失败：" + cache_name) from None
            time.sleep(1 + attempt * 2)


def make_roster(refresh=False):
    from pypinyin import lazy_pinyin
    ak = fetch_json("https://raw.githubusercontent.com/Kengxxiao/ArknightsGameData/master/zh_CN/gamedata/excel/character_table.json", "arknights-game", refresh)
    queries = "?query=names&matchCategories=true&verboseCategories=true&resultLanguage="
    cn = fetch_json("https://genshin-db-api.vercel.app/api/v5/characters" + queries + "ChineseSimplified", "genshin-cn", refresh)
    en = fetch_json("https://genshin-db-api.vercel.app/api/v5/characters" + queries + "English", "genshin-en", refresh)
    en = {c["id"]: c for c in en}
    result = []
    for key, c in ak.items():
        if not key.startswith("char_") or c["profession"] in ("TOKEN", "TRAP"):
            continue
        name = c["name"]
        result.append({"id": key, "name": name, "game": "明日方舟", "english": c["appellation"],
                       "aliases": ALIASES.get(name, []), "six_star": c["rarity"] == "TIER_6",
                       "temporary": bool(c["isNotObtainable"]), "target": c["rarity"] == "TIER_6"})
    for c in cn:
        name = c["name"]
        result.append({"id": str(c["id"]), "name": name, "game": "原神", "english": en[c["id"]]["name"],
                       "aliases": ALIASES.get(name, []), "version": c["version"], "target": True})
    for c in result:
        c["pinyin"] = " ".join(lazy_pinyin(c["name"]))
        if c["name"] == "重岳": c["pinyin"] = "chong yue"
        if c["name"] == "行秋": c["pinyin"] = "xing qiu"
    roster = {"as_of": "2026-10-07", "genshin_version": "7.1", "sources": [
        "https://github.com/Kengxxiao/ArknightsGameData", "https://github.com/theBowja/genshin-db",
        "https://ys.mihoyo.com/main/m/news/detail/166090"], "characters": result}
    e.save(ROOT / "data/character-roster.json", roster)
    return result


def matches(model, character, all_characters, version=None):
    """Match a specific identity; an alter must never be assigned its base model."""
    raw_title = model["name"] + " " + ((version or {}).get("name", ""))
    # A physical trait such as 'sharp teeth' or 'thorns' is not a character name.
    identity_tags = []
    for tag in (version or {}).get("trainedWords", []):
        # Keep only the identity preceding the franchise; appearance words later
        # in a long trigger must not turn 'sharp teeth' into the operator Sharp.
        clean = tag.replace('\\(', '(').replace('\\)', ')')
        for match in re.finditer(r"([^,;()]+)\(\s*(arknights|genshin[_ ]impact)\s*\)", clean, re.I):
            identity_tags.append(match.group(1).strip(' _') + '(' + match.group(2) + ')')
    raw_title += " " + " ".join(identity_tags)
    if character['game']=='明日方舟' and re.search(r'endfield|终末地|エンドフィールド',raw_title,re.I):
        # Sharing the Arknights franchise does not make Chen Qianyu Ch'en.
        # This roster is the original game's six-star operators.
        return False
    if character['name']=='Touch' and character['game']=='明日方舟':
        # Amiya's official outfit is "Touch the Stars". Its clothing token is
        # not the temporary elite operator Touch, even with word boundaries.
        raw_title=re.sub(r'\btouch[\s_]+the[\s_]+stars\b','',raw_title,flags=re.I)
    title = normalized(raw_title)
    aliases = [character["name"], character["english"], *character["aliases"]]
    words = " " + word_text(raw_title) + " "
    hits = [alias for alias in aliases if (" " + word_text(alias) + " ") in words and len(normalized(alias)) >= 3]
    # Single Chinese glyph names need exact boundaries in the unnormalized title.
    if len(character["name"]) <= 2:
        pattern = r"(?<![a-zA-Z\u3400-\u9fff])" + re.escape(character["name"]) + r"(?![a-zA-Z\u3400-\u9fff])"
        if re.search(pattern, raw_title, re.I): hits.append(character["name"])
    if not hits:
        return False
    game = "arknights" if character["game"] == "明日方舟" else "genshin"
    source_text = normalized(" ".join([model["name"], *model.get("tags", []), *identity_tags]))
    collab = {"结城理":"persona", "丰川祥子":"mujica", "灰烬":"rainbow", "艾拉":"rainbow", "医生":"rainbow", "双月":"rainbow", "玛露西尔":"dungeon"}
    if game not in source_text and normalized(character["game"]) not in source_text and collab.get(character["name"],"__unmatched__") not in source_text:
        return False
    longest = max(len(normalized(a)) for a in hits)
    # A longer matching identity in this game supersedes a prefix such as Skadi.
    for other in all_characters:
        if other["id"] == character["id"] or other["game"] != character["game"]:
            continue
        for alias in [other["name"], other["english"], *other["aliases"]]:
            norm = normalized(alias)
            if len(norm) > longest and (" " + word_text(alias) + " ") in words:
                return False
    return True


def rank_version(model, version, file):
    stats = version.get("stats") or model.get("stats", {})
    up = stats.get("thumbsUpCount", stats.get("ratingCount", 0))
    down = stats.get("thumbsDownCount", 0)
    downloads = stats.get("downloadCount", 0)
    # Wilson lower bound limits a new model's single perfect vote.
    votes = up + down
    z = 1.96
    reputation = ((up / votes + z*z/(2*votes) - z * math.sqrt((up/votes)*(1-up/votes)/votes + z*z/(4*votes*votes))) / (1+z*z/votes)) if votes else 0
    examples = version.get("images", [])
    with_prompts = sum(bool(im.get("meta", {}).get("prompt")) for im in examples if im.get("meta"))
    family_score = {"Illustrious": 30, "NoobAI": 26, "SDXL 1.0": 20, "Pony": 8}.get(version["baseModel"], 14)
    # An inspectable character demonstration, lineage, approval and usage are proxies,
    # not a claim of visual human scoring or guaranteed likeness on every checkpoint.
    score = family_score + 30*reputation + min(18, math.log1p(downloads)*2) + min(8, with_prompts*2) + min(5,len(examples))
    title = (model["name"] + " " + version["name"]).casefold()
    if any(t in title for t in ("chibi", "pixel", "style", "3d", "cosplay", "all characters")): score -= 35
    return round(score, 3), {"base_fit": family_score, "approval_lower_bound": round(reputation,4),
                           "thumbs_up": up, "thumbs_down": down, "downloads": downloads,
                           "examples": len(examples), "examples_with_prompt": with_prompts,
                           "visual_review": "not_individually_reviewed", "note": "公开数据代理评分；实际画质和还原度仍需出图审核"}


def search_character(character, all_characters, refresh=False, supplemental=False):
    queries = [character["english"], *character["aliases"], character["name"]]
    candidates, seen = [], set()
    rejected_path=ROOT/'data/lora-curation/rejected-files.json'
    rejected={f['sha256'] for f in e.read(rejected_path)} if rejected_path.exists() else set()
    errors = []
    # Query both official languages; aliases are fallbacks when no identity matches.
    search_queries = [queries[0], queries[-1]]
    if supplemental:
        search_queries += [*character["aliases"], character["english"] + " " + ("Arknights" if character["game"] == "明日方舟" else "Genshin")]
    for q in dict.fromkeys(search_queries):
        try:
            params = urllib.parse.urlencode({"query": q, "types": "LORA", "limit": 100, "nsfw": "true", "sort": "Most Downloaded"})
            if supplemental:
                params += "&" + urllib.parse.urlencode({"baseModels": sorted(FAMILIES)},doseq=True).replace("baseModels=", "baseModels%5B%5D=")
            data = fetch_json("https://civitai.com/api/v1/models?" + params, ("supplement-" if supplemental else "search-") + hashlib.sha256(q.encode()).hexdigest()[:20], refresh)
            for model in data.get("items", []):
                for version in model.get("modelVersions", []):
                    if not matches(model, character, all_characters, version): continue
                    if version.get("baseModel") not in FAMILIES or version.get("availability", "Public") != "Public": continue
                    files = [f for f in version.get("files", []) if f.get("type") == "Model" and f["name"].endswith(".safetensors")]
                    files.sort(key=lambda f: not f.get("primary", False))
                    if not files: continue
                    f = files[0]
                    sha = f.get("hashes", {}).get("SHA256", "").lower()
                    if len(sha) != 64 or sha in rejected or version["id"] in seen: continue
                    seen.add(version["id"])
                    score, evidence = rank_version(model, version, f)
                    candidates.append({"model_id":model["id"], "model":model["name"], "version_id":version["id"],
                        "version":version["name"], "base_model":version["baseModel"], "original_name":f["name"],
                        "file_id":f["id"], "sha256":sha, "bytes":round(f["sizeKB"]*1024), "url":f["downloadUrl"],
                        "page":f"https://civitai.com/models/{model['id']}?modelVersionId={version['id']}",
                        "triggers":version.get("trainedWords", []), "score":score,"evidence":evidence})
        except Exception as ex:
            errors.append(str(ex))
    candidates.sort(key=lambda c:(c["score"], c["version_id"]),reverse=True)
    row = {"character":character,"candidates":candidates,"selected":candidates[0] if candidates else None,
           "status":"selected" if candidates else "no_public_sdxl_found", "errors":errors}
    if row["selected"]:
        row["selected"]["relative_path"] = "sdxl/" + character["game"] + "/" + character["name"] + "_" + row["selected"]["original_name"]
    e.save(ROOT / "data/lora-curation/characters" / (character["game"] + "-" + character["id"] + ".json"), row)
    return row


def fill_gaps():
    path=ROOT / "data/lora-curation/selection.json"
    selection=e.read(path); roster=e.read(ROOT / "data/character-roster.json")["characters"]
    gaps=[(i,r) for i,r in enumerate(selection["characters"]) if not r["selected"]]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures={pool.submit(search_character,r["character"],roster,False,True):i for i,r in gaps}
        for future in concurrent.futures.as_completed(futures):
            row=future.result();selection["characters"][futures[future]]=row
            print(row['character']['name'],len(row['candidates']),flush=True)
    e.save(path,selection)


def verified_fallbacks():
    """Use documented alter versions and explicit training folders, never names alone."""
    import struct
    import os
    path=ROOT/"data/lora-curation/selection.json";selection=e.read(path)
    explicit={"耀骑士临光":(1420066,1605310)}
    catalog=e.read(ROOT/"Chenkin_LoRA/catalog.json")["items"]
    bundles=[]
    for model in catalog:
        if model['id']!=462775:continue
        for v in model['modelVersions']:
            for f in v['files']:
                local=ROOT/'models/loras/sdxl/明日方舟'/('明日方舟全角色_'+f['name'])
                if not local.exists():continue
                with local.open('rb') as stream:
                    size=struct.unpack('<Q',stream.read(8))[0];metadata=json.loads(stream.read(size)).get('__metadata__',{})
                datasets=json.loads(metadata.get('ss_dataset_dirs','{}'))
                bundles.append((model,v,f,local,datasets))
    for row in selection['characters']:
        if row['selected']:continue
        c=row['character'];choices=[]
        if c['name'] in explicit:
            mid,vid=explicit[c['name']];model=fetch_json(f'https://civitai.com/api/v1/models/{mid}',f'model-{mid}')
            v=next(v for v in model['modelVersions'] if v['id']==vid);f=next(f for f in v['files'] if f['type']=='Model' and f.get('primary',True))
            choices.append((model,v,f,None,{'documented_identity':c['english'],'description_source':f'https://civitai.com/models/{mid}','version_name':v['name']}))
        elif c['game']=='明日方舟':
            aliases=[word_text(c['english']),*(word_text(a) for a in c['aliases'])]
            for model,v,f,local,datasets in bundles:
                matches=[k for k in datasets if re.sub(r'^\d+\s*','',word_text(k)).removesuffix(' arknights').strip() in aliases]
                if matches:choices.append((model,v,f,local,{'explicit_training_folders':matches,'dataset_info':{k:datasets[k] for k in matches},'fallback':'无专用公开 SDXL 版本；已证实训练此角色的合集权重'}))
        for model,v,f,local,proof in choices:
            score,evidence=rank_version(model,v,f)
            # A confirmed per-character training folder overrides the collection
            # penalty only when no dedicated compatible model was found.
            if local:score+=35
            candidate={'model_id':model['id'],'model':model['name'],'version_id':v['id'],'version':v['name'],'base_model':v['baseModel'],
              'original_name':f['name'],'file_id':f['id'],'sha256':f['hashes']['SHA256'].lower(),'bytes':round(f['sizeKB']*1024),'url':f['downloadUrl'],
              'page':f"https://civitai.com/models/{model['id']}?modelVersionId={v['id']}",'triggers':v.get('trainedWords',[]),'score':score,'evidence':{**evidence,**proof}}
            if local:
                tag=re.sub(r'^\d+\s*_?\s*','',proof['explicit_training_folders'][0]).strip()
                candidate['triggers']=[tag.replace('(','\\(').replace(')','\\)')]
                candidate['reuse_local']=str(local)
            candidate['relative_path']='sdxl/'+c['game']+'/'+c['name']+'_'+f['name'];row['candidates'].append(candidate)
        if row['candidates']:
            row['candidates'].sort(key=lambda x:x['score'],reverse=True);row['selected']=row['candidates'][0];row['status']='selected_verified_fallback'
            selected=row['selected']
            if selected.get('reuse_local'):
                dest=ROOT/'models/loras'/selected['relative_path'];dest.parent.mkdir(parents=True,exist_ok=True)
                if not dest.exists():os.link(selected['reuse_local'],dest)
            print(c['name'],row['selected']['version'],flush=True)
    e.save(path,selection)


def curate(refresh=False):
    characters = make_roster(refresh)
    targets = [c for c in characters if c["target"]]
    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(search_character,c,characters,refresh):c for c in targets}
        for future in concurrent.futures.as_completed(futures):
            row = future.result(); rows.append(row)
            print(f"选型 {len(rows)}/{len(targets)} {row['character']['name']}: {len(row['candidates'])} 个 SDXL 候选",flush=True)
    rows.sort(key=lambda r:(r["character"]["game"],r["character"]["id"]))
    e.save(ROOT / "data/lora-curation/selection.json", {"as_of":"2026-10-07", "policy":"WAI/Illustrious 优先；Wilson 口碑下界、下载量、可检查示例综合代理评分", "characters":rows})
    print("选型完成：",len([r for r in rows if r['selected']]),"/",len(rows),flush=True)


def recheck():
    path=ROOT/'data/lora-curation/selection.json';selection=e.read(path)
    roster=e.read(ROOT/'data/character-roster.json')['characters'];changes=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        futures={pool.submit(search_character,r['character'],roster):i for i,r in enumerate(selection['characters'])}
        for done,future in enumerate(concurrent.futures.as_completed(futures),1):
            i=futures[future];fresh=future.result();old=selection['characters'][i]
            if fresh['selected'] and (not old['selected'] or fresh['selected']['score']>old['selected']['score']):
                changes.append({'name':old['character']['name'],'previous':old['selected'],'selected':fresh['selected']})
                selection['characters'][i]=fresh
            if done%30==0:print('复核',done,'/270',flush=True)
    e.save(path,selection);e.save(ROOT/'data/lora-curation/recheck-changes.json',changes)
    print('更新更优版本',len(changes),flush=True)


def download():
    spec = importlib.util.spec_from_file_location("chenkin_downloader", ROOT / "Chenkin_LoRA/download.py")
    downloader = importlib.util.module_from_spec(spec); spec.loader.exec_module(downloader)
    # Reuse the verified authenticated-CDN and aria2 retry code with a new destination.
    downloader.ROOT = ROOT / "models/loras"
    downloader.ARIA_EXE = ROOT / "Chenkin_LoRA/tools/aria2-1.36.0/aria2c.exe"
    rows = e.read(ROOT / "data/lora-curation/selection.json")["characters"]
    entries = [dict(r["selected"], category=r["character"]["game"]) for r in rows if r["selected"]]
    token = (ROOT / "Chenkin_LoRA/civitai-token.txt").read_text().strip() if (ROOT / "Chenkin_LoRA/civitai-token.txt").exists() else ""
    proxy = urllib.request.getproxies().get("https") or urllib.request.getproxies().get("http")
    args = argparse.Namespace(jobs=3,connections=8)
    pending = downloader.pending_files(entries)
    for entry in entries: (downloader.ROOT / entry["relative_path"]).parent.mkdir(parents=True,exist_ok=True)
    if pending:
        result, failures = downloader.download_with_retries(entries,pending,token,args,proxy)
    else: result,failures=0,[]
    e.save(ROOT / "data/lora-curation/download-status.json", {"selected":len(entries),"verified":len(entries)-len(failures),"unfinished":failures,"exit_code":result})
    finalize_library()
    return result


def finalize_library():
    """Publish only hash-verified, structurally compatible weights and one outfit."""
    from comic_workflow.lora_inspection import inspect_lora
    from comic_workflow.characters import CharacterLibrary
    path=ROOT/'data/lora-labels.json';labels=e.read(path) if path.exists() else {}
    cards={c['name']:c for c in CharacterLibrary().listing()}
    selection=e.read(ROOT/'data/lora-curation/selection.json')['characters'];results=[]
    gap_path=ROOT/'data/lora-curation/missing-downloads.json'
    gaps={r['name']:r for r in e.read(gap_path)['characters']} if gap_path.exists() else {}
    for row in selection:
        chosen=row['selected'];c=row['character']
        if not chosen:
            results.append({'name':c['name'],'game':c['game'],'status':'missing_verified_weight',
                            'curation_source':'Civitai API','curation_search_status':row.get('status'),
                            **{k:v for k,v in gaps.get(c['name'],{}).items() if k not in ('name','game')}});continue
        local=ROOT/'models/loras'/chosen['relative_path']
        # Civitai sometimes rounds sizeKB to an integer. SHA256, not rounded
        # public sizes, is authoritative for integrity.
        if not local.is_file() or Path(str(local)+'.aria2').exists() or Path(str(local)+'.aria2__temp').exists():
            results.append({'name':c['name'],'game':c['game'],'status':'unfinished','file':chosen['relative_path']});continue
        with local.open('rb') as stream:sha=hashlib.file_digest(stream,'sha256').hexdigest()
        if sha!=chosen['sha256']:
            results.append({'name':c['name'],'game':c['game'],'status':'hash_mismatch','file':chosen['relative_path']});continue
        inspection=inspect_lora(local)
        if inspection['structurally_compatible'] is not True:
            results.append({'name':c['name'],'game':c['game'],'status':'incompatible','inspection':inspection});continue
        # These identities are explicitly present in their own verified training
        # headers, while the public version omitted trainedWords entirely.
        overrides={'甘雨':'g4n7u','奇偶·男性':'manekin','陈':r"ch'en_\(arknights\)",
                   '伊芙利特':'arknights_ifrit','夕':'dusk (arknights), duskdef'}
        if not chosen['triggers'] and c['name'] in overrides:
            import struct
            with local.open('rb') as stream:
                size=struct.unpack('<Q',stream.read(8))[0]
                metadata=json.loads(stream.read(size)).get('__metadata__',{})
            frequency=json.loads(metadata.get('ss_tag_frequency','{}'))
            tags={tag for dataset in frequency.values() for tag in dataset}
            tokens=overrides[c['name']].split(', ')
            e.need(all(token in tags for token in tokens),'缺少训练记录中的角色触发词：'+c['name'])
            chosen['triggers']=[overrides[c['name']]]
            chosen['evidence']['trigger_source']={'type':'verified_safetensors_training_header',
                                                 'sha256':sha,'tokens':tokens}
            e.save(ROOT/'data/lora-curation/characters'/(c['game']+'-'+c['id']+'.json'),row)
        label=labels.setdefault(chosen['relative_path'],{})
        label.update(name=c['name'],character=c['name'],trigger_words=(chosen['triggers'][0] if chosen['triggers'] else ''),source_page=chosen['page'],curated=True)
        label.setdefault('weight',.7)
        if 'character_id' not in label and c['name'] in cards:label['character_id']=cards[c['name']]['id']
        verified={'name':c['name'],'game':c['game'],'status':'verified','file':chosen['relative_path'],'sha256':sha,'actual_bytes':local.stat().st_size,'base_model':chosen['base_model'],'inspection':inspection}
        if chosen.get('catalog_sha256'):
            # Keep the observed full-file identity and its documented exception
            # distinct from a successful website-catalog checksum comparison.
            verified.update(catalog_sha256=chosen['catalog_sha256'],
                            catalog_checksum_match=sha==chosen['catalog_sha256'],
                            integrity_exception=chosen['evidence']['integrity_exception'])
        results.append(verified)
    e.save(path,labels)
    manifest=e.read(ROOT/'data/lora-curation/selection.json');manifest['characters']=selection
    e.save(ROOT/'data/lora-curation/selection.json',manifest)
    e.save(ROOT/'data/lora-curation/library-audit.json',{'as_of':'2026-10-07','characters':results,'verified':sum(r['status']=='verified' for r in results),'total':len(results),
        'quality_review':'not_complete','ranking_note':'候选排序综合下载量、评价及模型家族适配；尚未逐角色完成画质与还原度的实图比较，不能声明全部为最佳版本。'})
    download_path=ROOT/'data/lora-curation/download-status.json'
    if download_path.exists():
        download=e.read(download_path)
        download.update(selected=sum(bool(row['selected']) for row in selection),
                        verified=sum(r['status']=='verified' for r in results),
                        roster_total=len(results),coverage_complete=all(r['status']=='verified' for r in results),
                        missing_characters=[r['name'] for r in results if r['status']!='verified'])
        e.save(download_path,download)
    print('库校验',sum(r['status']=='verified' for r in results),'/',len(results),flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8",errors="replace")
    parser=argparse.ArgumentParser()
    parser.add_argument("--download",action="store_true")
    parser.add_argument("--refresh",action="store_true")
    parser.add_argument("--gaps",action="store_true")
    parser.add_argument("--verified-fallbacks",action="store_true")
    parser.add_argument("--audit",action="store_true")
    parser.add_argument("--recheck",action="store_true")
    args=parser.parse_args()
    if args.download: raise SystemExit(download())
    if args.gaps: fill_gaps();raise SystemExit(0)
    if args.verified_fallbacks:verified_fallbacks();raise SystemExit(0)
    if args.audit:finalize_library();raise SystemExit(0)
    if args.recheck:recheck();raise SystemExit(0)
    curate(args.refresh)
