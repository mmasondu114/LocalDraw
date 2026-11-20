"""Frozen character identity shared by drawing adaptation and visual review."""

import copy


def panel_characters(project, panel):
    ids = {c["character_id"] for c in panel["characters"]}
    saved = {
        c["name"].lstrip("/").casefold(): c
        for c in project["brief"].get("character_refs", [])
    }
    result = []
    for character in project["canon"]["characters"]:
        if character["id"] not in ids:
            continue
        item = copy.deepcopy(character)
        card = saved.get(character["name"].lstrip("/").casefold())
        if card:
            item["saved_character"] = {
                "id": card["id"],
                "revision": card["revision"],
                "name": card["name"],
                "profile": copy.deepcopy(card["profile"]),
                "reference_image_id": card.get("image_id"),
            }
        result.append(item)
    return result


def identity_hash(project, panel):
    from .engine import digest

    return digest({"version": 1, "characters": panel_characters(project, panel)})


def with_character_anchors(positive, anchors):
    # Always carry each character's full anchor into the actual CLIP input.
    # Group by person rather than flattening their features into one character.
    tags = [anchor["tags"].strip() for anchor in anchors]
    missing = [tag for tag in tags if tag and tag.casefold() not in positive.casefold()]
    return "; ".join(missing + [positive])
