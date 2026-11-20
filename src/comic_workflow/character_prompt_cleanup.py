"""Conservative cleanup of imported drawing tags; never translates model tokens."""

import re
from .clothing_policy import VISUAL_FIELDS, has_clothing, parts

NON_ENGLISH = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")
BOILERPLATE = re.compile(
    r"来源资料未核实|本地LoRA来源档案|未知|尚未核对|"
    r"\b(?:unknown|please|thank you|refer to|trigger words?|download|"
    r"recommended settings|use uploaded image|keep (?:the )?(?:original|existing)|"
    r"source not verified|not verified|not specified|prompts? for this lora|promts for this lora)\b|"
    r"^(?:character/anime\*?|face/head:|clothing:|default outfit|default)$", re.I)
SCENE_TAGS = re.compile(
    r"^(?:solo|solo focus|looking at (?:the )?viewer|looking away|"
    r"upper body|full body|cowboy shot|portrait|close-up|from (?:above|below|behind)|"
    r"indoors|outdoors|simple background|white background|blurry(?: background)?|"
    r"anime screencap|official art|masterpiece|best quality|high quality|"
    r"smile|smiling|open mouth|standing|sitting)$", re.I)


def clean_tags(text, *, appearance=False):
    text = re.sub(r"<lora:[^>]*>", "", str(text or ""), flags=re.I)
    text = text.replace("，", ",").replace("；", ";").replace("｜", "|")
    # A pipe within one authored entry separates tag groups, not a loader.
    text = text.replace(" | ", ", ")
    result, seen = [], set()
    for token in parts(text):
        token = re.sub(r"\s+", " ", token).strip(" ,;|")
        if not token or NON_ENGLISH.search(token) or BOILERPLATE.search(token):
            continue
        if re.search(r"https?://|www\.|\.safetensors|👍", token, re.I):
            continue
        if appearance and SCENE_TAGS.fullmatch(token):
            continue
        key = token.casefold()
        if key not in seen:
            result.append(token)
            seen.add(key)
    return ", ".join(result)


def default_trigger(words):
    """Keep tag lists intact; never concatenate separate costume descriptions.

    Single-token arrays are one tag list. For paragraph arrays, only append a
    clothing-only second entry to an unclothed identity block. All alternatives
    remain in source_trained_words for explicit selection and auditing.
    """
    words = [str(w).strip() for w in words or [] if str(w).strip()]
    if not words:
        return ""
    # Some authors use headings instead of alternatives. Keep the documented
    # identity/head/clothes sections, discarding the headings themselves.
    if any(w.lower() == 'face/head:' for w in words) and any(w.lower() == 'clothing:' for w in words):
        return clean_tags(', '.join(w for w in words if w.lower() not in ('character/anime*', 'cosplay, alternate_costume,')))
    words = [w for w in words if clean_tags(w)]
    if not words:
        return ''
    if all(len(parts(w)) == 1 and len(w) < 100 for w in words):
        return clean_tags(", ".join(words))
    first = clean_tags(words[0])
    if len(words) > 1 and first and not has_clothing(first):
        second = clean_tags(words[1])
        tags = parts(second)
        # A name/activation token or new hair/eye block means a separate variant.
        if tags and has_clothing(tags[0]) and not re.search(r"\b(?:hair|eyes|cosplay)\b", second, re.I):
            first = clean_tags(first + ", " + second)
    return first


def visual_profile(prompt):
    """Distribute existing English tags, without inferring new appearance facts."""
    fields = {k: [] for k in VISUAL_FIELDS}
    for i, tag in enumerate(parts(clean_tags(prompt, appearance=True))):
        t = tag.replace("_", " ")
        if re.search(r"\([^)]*(?:impact|arknights|azur|fate|re:zero)[^)]*\)", t, re.I):
            key = "identity"
        elif re.search(r"\b(?:hair ornament|hair flower|hair ribbon|hair bow|hairclip|jewelry|earrings?|necklace|brooch|ribbon|bow|choker|piercing)\b", t, re.I):
            key = "accessories"
        elif re.search(r"\b(?:hair|bangs|sidelocks|braid|braids|twintails|ponytail|ahoge|bun|updo)\b", t, re.I):
            key = "hair"
        elif re.search(r"\b(?:eyes|pupils|heterochromia)\b", t, re.I):
            key = "eyes"
        elif has_clothing(t):
            key = "clothing"
        elif re.search(r"\b(?:1girl|1boy|1other|female|male|breasts|height|muscular|slender|petite|skin|elf)\b", t, re.I):
            key = "body"
        elif re.search(r"\b(?:mole|freckles|facial|lips)\b", t, re.I):
            key = "face"
        elif re.search(r"\b(?:ears|horns|tail|wings|halo|tattoo|mark)\b", t, re.I):
            key = "marks"
        else:
            key = "identity" if i < 3 else "marks"
        fields[key].append(tag)
    return {k: ", ".join(v) or "unknown" for k, v in fields.items()}


def default_visual_prompt(profile):
    tags = []
    for key in VISUAL_FIELDS:
        if key == "clothing":
            continue
        for tag in parts(clean_tags(profile.get(key, ""), appearance=True)):
            if key == "identity" or not has_clothing(tag):
                tags.append(tag)
    return clean_tags(", ".join(tags), appearance=True)
