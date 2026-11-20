"""Resolve optional card outfits before sending plain tags to an image model."""

import re

VISUAL_FIELDS = ["identity", "face", "hair", "eyes", "body", "clothing", "accessories", "palette", "marks"]
# Shared with the browser so its preview and the submitted prompt agree.
PATTERN = (
    r"\b(?:outfits?|clothes|clothing|costumes?|uniforms?|dresses|dress|skirts?|"
    r"shirts?|t[ -]?shirts?|blouses?|(?:tank|crop|tube|halter|bikini|strapless)[ -]tops?|jackets?|coats?|hoodies?|sweaters?|"
    r"cardigans?|vests?|blazers?|suits?|tuxedos?|robes?|capes?|capelets?|cloaks?|ponchos?|haori|"
    r"pants|trousers|shorts|jeans|leggings|tights|stockings?|thighhighs?|"
    r"socks?|boots?|shoes?|loafers?|heels|sandals?|slippers?|sneakers?|headwear|miniskirts?|"
    r"gloves?|mittens?|sleeves?|sleeveless|hoods?|hooded|collars?|neckties?|"
    r"ties|bowties?|belts?|corsets?|aprons?|scarves|scarf|hats?|caps?|berets?|"
    r"helmets?|armor|armour|bikinis?|swimsuits?|swimwear|leotards?|bodysuits?|"
    r"underwear|lingerie|bras?|panties|pajamas?|pyjamas?|nightgowns?|"
    r"kimonos?|yukatas?|qipaos?|cheongsams?|hanfu|hakama|sailor|maid|"
    r"nude|naked|topless|shirtless|bottomless|barefoot|unclothed)\b"
    r"|衣服|衣着|服装|穿着|换装|上衣|外套|衬衫|T恤|卫衣|毛衣|风衣|大衣|"
    r"裙|裤|袜|鞋|靴|手套|袖|兜帽|披风|斗篷|围巾|领带|腰带|帽子|头盔|"
    r"制服|校服|西装|礼服|婚纱|女仆装|泳装|泳衣|比基尼|睡衣|内衣|"
    r"汉服|和服|浴衣|旗袍|盔甲|铠甲|裸身|裸体|全裸|赤裸|不穿|没穿|光脚|赤脚"
)
UNKNOWN_PATTERN = (r"^(?:未知|unknown\b)|来源资料未核实|本地LoRA来源档案|"
                   r"保持既有模型设定|不自动补造|please refer to|prompts? for this lora")
RULES = {"pattern": PATTERN, "visual_fields": VISUAL_FIELDS,
         "unknown_pattern": UNKNOWN_PATTERN}


def has_clothing(text):
    return bool(re.search(PATTERN, text.replace("_", " "), re.I | re.ASCII))


def parts(text):
    result, start, depth = [], 0, 0
    for i, char in enumerate(text):
        if char in "([":
            depth += 1
        elif char in ")]":
            depth = max(0, depth - 1)
        elif char in ",，;；\n" and depth == 0:
            result.append(text[start:i].strip())
            start = i + 1
    result.append(text[start:].strip())
    return [p for p in result if p]


def known(value):
    return bool(value and not re.search(UNKNOWN_PATTERN, value, re.I))


def normalized(text):
    return re.sub(r"\s+", " ", text.replace("_", " ").strip()).lower()


def aliases(card):
    values = [card["name"]]
    values += [re.sub(r"\s*\([^)]*\)", "", s).strip()
               for s in parts(card.get("profile", {}).get("identity", ""))]
    return [v for v in dict.fromkeys(values) if known(v) and len(v) > 0]


def scene_clothing(text, card, cards):
    """Unqualified outfits apply to all; named clauses apply to their subjects."""
    owners = {}
    for other in cards:
        for alias in aliases(other):
            owners.setdefault(alias.lower(), set()).add(other["name"])
    if not owners:
        return has_clothing(text)
    pattern = "|".join(
        (r"(?<![A-Za-z0-9_])" if re.match(r"[A-Za-z0-9_]", a) else "")
        + re.escape(a)
        + (r"(?![A-Za-z0-9_])" if re.search(r"[A-Za-z0-9_]$", a) else "")
        for a in sorted(owners, key=len, reverse=True)
    )
    for sentence in re.split(r"[;；。!！?？\n]|\.(?!\d)", text):
        mentions = list(re.finditer(pattern, sentence, re.I))
        if not mentions:
            if has_clothing(sentence):
                return True
            continue
        if has_clothing(sentence[:mentions[0].start()]):
            return True
        subjects = set()
        for i, match in enumerate(mentions):
            subjects.update(owners[match.group().lower()])
            end = mentions[i + 1].start() if i + 1 < len(mentions) else len(sentence)
            clause = sentence[match.end():end]
            if card["name"] in subjects and has_clothing(clause):
                return True
            if not re.fullmatch(r"[\s,/、]*(?:(?:and|和|与|及)[\s,/、]*)?", clause, re.I):
                subjects = set()
    return False


def resolve_prompt(text, prompt, card, cards):
    profile = card.get("profile", {})
    defaults = parts(profile.get("clothing", "")) if known(profile.get("clothing", "")) else []
    defaults += [p for p in parts(profile.get("accessories", "")) if has_clothing(p)]
    default_keys = {normalized(p) for p in defaults}
    tags = parts(prompt)
    # Identity triggers such as "coat (game)" are not garments.
    identity = {normalized(p) for p in parts(profile.get("identity", ""))}
    is_outfit = lambda p: normalized(p) not in identity and (normalized(p) in default_keys or has_clothing(p))
    override = scene_clothing(text, card, cards)
    custom = any(is_outfit(p) and normalized(p) not in default_keys for p in tags)
    if override:
        return ", ".join(p for p in tags if not is_outfit(p))
    if custom:
        return ", ".join(p for p in tags if normalized(p) not in default_keys)
    missing = [p for p in defaults if normalized(p) not in {normalized(t) for t in tags}]
    return prompt + ((", " if prompt.strip() else "") + ", ".join(missing) if missing else "")


def resolve_selections(text, selections, cards):
    return [{**s, "prompt": resolve_prompt(text, s["prompt"], c, cards)}
            for s, c in zip(selections, cards)]
