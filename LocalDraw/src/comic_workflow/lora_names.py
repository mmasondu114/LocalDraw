"""Searchable character identities are independent of opaque weight filenames."""
import json
import unicodedata
from pathlib import PurePosixPath
from .paths import DATA

EXTRA_IDENTITIES = {
    ('原神','派蒙'): {'english':'Paimon'},
    ('原神','留云借风真君'): {'english':'Cloud Retainer'},
    ('原神','罗诺瓦'): {'english':'Ronova'},
    ('原神','伊斯塔露'): {'english':'Istaroth'},
    ('原神','芙宁娜与芙卡洛斯'): {'english':'Furina and Focalors','aliases':['Furina','Focalors']},
    ('原神','荧与空'): {'english':'Lumine and Aether','aliases':['Lumine','Aether','Travelers']},
    ('明日方舟','特蕾西娅'): {'english':'Theresa'},
    ('明日方舟','博士'): {'english':'Doctor'},
    ('明日方舟','佩丽卡'): {'english':'Perlica'},
    ('明日方舟','陈千语'): {'english':'Chen Qianyu'},
    ('明日方舟','明日方舟全角色'): {'english':'Arknights All Characters','is_style':True},
    ('原神','动画截图风格'): {'english':'Anime Screencap Style','is_style':True},
    ('原神','原神全角色'): {'english':'Genshin All Characters','is_style':True},
    ('原神','原神风格'): {'english':'Genshin Art Style','is_style':True},
    ('原神','手部修复'): {'english':'Hand Repair','is_style':True},
    ('明日方舟','明日方舟风格'): {'english':'Arknights Art Style','is_style':True},
    ('明日方舟','彩色线条'): {'english':'Colored Lineart','is_style':True},
    ('明日方舟','立绘风格'): {'english':'Character Illustration Style','is_style':True},
    ('明日方舟','线条风格'): {'english':'Lineart Style','aliases':['Outline'],'is_style':True},
}


def normalize_search(value):
    return "".join(c for c in unicodedata.normalize("NFKD", value.casefold())
                   if c.isalnum() and not unicodedata.combining(c)).replace("ł", "l")


def display_name(filename):
    path = PurePosixPath(filename.replace("\\", "/"))
    if len(path.parts) == 2 and path.parts[0] in {"anime", "sdxl", "krea2", "moss_image", "qwen_edit"}:
        return path.stem
    return path.stem.split("_", 1)[0]


def identities():
    path = DATA / "character-roster.json"
    rows = json.loads(path.read_text(encoding="utf-8"))["characters"] if path.exists() else []
    return {(c["game"], c["name"]): c for c in rows}


def search_identity(filename, roster=None):
    portable = filename.replace("\\", "/")
    name = display_name(portable)
    game = portable.split("/", 1)[0] if "/" in portable else ""
    if game in {"Anima", "anime", "sdxl"} and portable.count("/") >= 2:
        game = portable.split("/")[1]
    character = (roster if roster is not None else identities()).get((game, name), EXTRA_IDENTITIES.get((game,name),{}))
    aliases = list(dict.fromkeys([character.get("english", ""), *character.get("aliases", [])]))
    aliases = [a for a in aliases if a]
    pinyin = character.get("pinyin", "")
    if not pinyin:
        from pypinyin import lazy_pinyin
        pinyin = " ".join(lazy_pinyin(name))
    return {"display_name": name, "pinyin": pinyin, "english_aliases": aliases,
            "search_terms": [name, pinyin, *aliases], "is_character": bool(character) and not character.get('is_style')}


def card_search_identity(card, roster=None):
    """Use the card's game to distinguish characters sharing a Chinese name."""
    roster = {**EXTRA_IDENTITIES, **(roster if roster is not None else identities())}
    name = card["name"]
    game = card.get("source", {}).get("game", "").split("：", 1)[0]
    key = (game, name)
    if not game:
        candidates = [key for key in roster if key[1] == name]
        key = candidates[0] if len(candidates) == 1 else ("", name)
    character = roster.get(key, {})
    aliases = list(dict.fromkeys(filter(None, [character.get("english", ""), *character.get("aliases", [])])))
    pinyin = character.get("pinyin")
    if not pinyin:
        from pypinyin import lazy_pinyin
        pinyin = " ".join(lazy_pinyin(name))
    return {"pinyin": pinyin, "english_aliases": aliases, "search_terms": [name, pinyin, *aliases]}


def matches_query(filename, query, roster=None):
    q = normalize_search(query)
    return not q or any(q in normalize_search(term)
                       for term in search_identity(filename, roster)["search_terms"])
