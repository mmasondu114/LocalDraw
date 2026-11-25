"""Reusable, versioned character cards and local normalized reference images."""

import base64
import copy
import io
import re
import uuid
from pathlib import Path

from PIL import Image, ImageOps
from . import engine as e
from .paths import DATA
from .repository import LOCK, safe_id
from .schemas import obj, arr, S

DETAILS = {
    "identity": "身份与年龄设定",
    "face": "脸型与五官",
    "hair": "发型与发色",
    "eyes": "眼睛与眼神",
    "body": "体型与身高比例",
    "clothing": "默认服装与鞋履（提示词未指定时使用）",
    "accessories": "配饰与随身物品",
    "palette": "主要配色",
    "marks": "辨识特征",
    "personality": "性格与行为习惯",
    "voice": "说话方式与台词样例",
    "motivation": "愿望、动机与弱点",
    "background": "经历与背景",
    "relationships": "人物关系",
    "constraints": "不可随意改变的设定",
}
CARD = obj(
    {
        **{k: S for k in DETAILS},
        "observed": arr(S),
        "suggested": arr(S),
        "unknowns": arr(S),
    }
)
NAME = re.compile(r"[\w\-·・\u3400-\u9fff]{1,32}", re.UNICODE)
# Spaces or punctuation end a mention. The UI picker inserts a trailing space.
MENTION = re.compile(
    r"(?<![A-Za-z0-9_/:])/(?P<name>[\w\-·・\u3400-\u9fff]+)",
    re.UNICODE,
)


def valid_name(name):
    e.need(
        isinstance(name, str) and NAME.fullmatch(name),
        "角色名限 1–32 个中文、字母、数字、间隔号、下划线或短横线，不含空格或斜杠",
    )
    return name


class CharacterLibrary:
    def __init__(self, root=None):
        self.root = Path(root) if root else DATA / "characters"
        self.root.mkdir(parents=True, exist_ok=True)
        self._cache_key = None
        self._cache_cards = None
        self._cache_searchable = None

    def _cards_signature(self):
        cards_dir = self.root / "cards"
        if not cards_dir.is_dir():
            return ()
        files = sorted(cards_dir.glob("*/current.json"))
        return tuple((p.parent.name, p.stat().st_mtime_ns, p.stat().st_size) for p in files)

    def listing(self):
        sig = self._cards_signature()
        if sig != self._cache_key or self._cache_cards is None:
            cards_dir = self.root / "cards"
            cards = sorted(
                [e.read(cards_dir / cid / "current.json") for cid, _, _ in sig],
                key=lambda x: x["name"],
            )
            self._cache_key = sig
            self._cache_cards = cards
            self._cache_searchable = None
        return copy.deepcopy(self._cache_cards)

    def searchable_listing(self):
        sig = self._cards_signature()
        if sig != self._cache_key or self._cache_searchable is None:
            cards = self.listing()
            from .lora_names import card_search_identity, identities
            roster = identities()
            self._cache_searchable = [{**card, **card_search_identity(card, roster)} for card in cards]
        return copy.deepcopy(self._cache_searchable)

    def get(self, name):
        valid_name(name)
        match = next(
            (x for x in self.listing() if name.casefold() in
             {x['name'].casefold(), *(a.casefold() for a in x.get('source',{}).get('aliases',[]))}), None
        )
        e.need(
            match is not None, f"尚未保存角色 /{name}；请先建档，引用后用空格或标点分隔"
        )
        return match

    def history(self, name):
        c = self.get(name)
        return [
            e.read(p)
            for p in sorted(
                (self.root / "cards" / c["id"] / "versions").glob("*.json"),
                reverse=True,
            )
        ]

    def save(self, name, profile, source, image_id=None, base_revision=0):
        valid_name(name)
        e.jsonschema.validate(profile, CARD)
        for key in DETAILS:
            e.need(
                profile[key].strip(), f"请填写{DETAILS[key]}；不清楚的内容可明确写未知"
            )
        e.need(
            sum(len(profile[k]) for k in DETAILS) >= 120,
            "角色描述过于简略，请补充外观、性格、口吻和背景（合计至少 120 字符）",
        )
        e.need(source.get("mode") in ["text", "image"], "请选择文字建档或图片建档")
        e.need(len(e.encoded(source)) < 60000, "来源资料过长")
        if source["mode"] == "image":
            e.need(
                image_id and self.image_path(image_id).is_file(),
                "图片建档需要有效参考图",
            )
            e.need(str(source.get("personality", "")).strip(), "请提供角色性格")
        with LOCK:
            old = next(
                (x for x in self.listing() if x["name"].casefold() == name.casefold()),
                None,
            )
            e.need(
                (old["revision"] if old else 0) == base_revision,
                "此角色已存在或版本已变化，请从角色库打开最新版再编辑",
            )
            item = {
                "id": old["id"] if old else uuid.uuid4().hex,
                "name": old["name"] if old else name,
                "revision": base_revision + 1,
                "profile": copy.deepcopy(profile),
                "source": copy.deepcopy(source),
                "image_id": image_id,
                "saved_at": e.stamp(),
            }
            folder = self.root / "cards" / item["id"]
            e.save(folder / "versions" / f"{item['revision']:06}.json", item)
            e.save(folder / "current.json", item)
            self._cache_key = None
            return item

    def upload(self, data_url):
        e.need(
            isinstance(data_url, str) and len(data_url) <= 14_000_000,
            "图片不能超过 10 MB",
        )
        e.need(
            data_url.startswith(
                (
                    "data:image/png;base64,",
                    "data:image/jpeg;base64,",
                    "data:image/webp;base64,",
                )
            ),
            "只支持 PNG、JPG、WebP 图片",
        )
        raw = base64.b64decode(data_url.split(",", 1)[1], validate=True)
        e.need(len(raw) <= 10_000_000, "图片不能超过 10 MB")
        with Image.open(io.BytesIO(raw)) as im:
            e.need(
                im.width * im.height <= 24_000_000,
                "图片尺寸过大，请缩小到 2400 万像素以内",
            )
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((1024, 1024))
            buffer = io.BytesIO()
            im.save(
                buffer, format="PNG"
            )  # Strip EXIF, GPS and arbitrary embedded metadata.
        image_id = uuid.uuid4().hex
        path = self.image_path(image_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(buffer.getvalue())
        return {"image_id": image_id, "width": im.width, "height": im.height}

    def image_path(self, image_id):
        return self.root / "images" / (safe_id(image_id) + ".png")

    def resolve(self, text, frozen=()):
        matches = list(dict.fromkeys(m.group("name") for m in MENTION.finditer(text)))
        current = self.listing()
        cards = {a.casefold(): c for c in current for a in c.get('source',{}).get('aliases',[])}
        cards.update({c["name"].casefold(): c for c in current})
        cards.update({c["name"].casefold(): c for c in frozen})
        result = []
        for token in matches:
            key = token.casefold()
            if key not in cards:
                # Support Chinese prose such as “让/林晴走进书店”; longest saved name wins.
                names = [
                    n
                    for n in cards
                    if key.startswith(n)
                    and re.fullmatch(r"[\u3400-\u9fff]+", key[len(n) :])
                ]
                e.need(names, f"尚未保存角色 /{token}；请选择角色库中的名字")
                key = max(names, key=len)
            if cards[key]["name"] not in [c["name"] for c in result]:
                result.append(copy.deepcopy(cards[key]))
        return result

    def freeze_brief(self, brief):
        result = copy.deepcopy(brief)
        # Read references from all creative text, not the already embedded snapshots.
        text = e.encoded(
            {
                k: result.get(k, "")
                for k in ["character_material", "direction", "must_include"]
            }
        )
        existing = result.get("character_refs", [])
        refs = self.resolve(text, existing)
        if refs:
            result["character_refs"] = refs
        return result
