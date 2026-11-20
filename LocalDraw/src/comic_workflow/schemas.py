"""JSON schemas shared by constrained generation and local validation."""

S = {"type": "string"}
N = {"type": "integer", "minimum": 1}


def arr(x, minimum=0):
    return {"type": "array", "items": x, "minItems": minimum}


def obj(p):
    return {
        "type": "object",
        "properties": p,
        "required": list(p),
        "additionalProperties": False,
    }


def enum(*x):
    return {"type": "string", "enum": list(x)}


nullable = {"type": ["string", "null"]}
STATE = {"type": "object", "additionalProperties": S}
CHARACTER = obj(
    {
        "id": S,
        "name": S,
        "origin": enum("original", "existing_work"),
        "source_notes": arr(S),
        "verified_facts": arr(S),
        "adaptation_choices": arr(S),
        "unknowns": arr(S),
        "appearance": S,
        "voice": S,
        "sample_lines": arr(S),
        "motivation": S,
        "taboos": arr(S),
    }
)
SCENE = obj(
    {
        "id": S,
        "name": S,
        "layout": S,
        "landmarks": arr(S),
        "lighting": S,
        "axis_rule": S,
    }
)
CANON = obj(
    {
        "characters": arr(CHARACTER, 1),
        "scenes": arr(SCENE, 1),
        "props": arr(obj({"id": S, "description": S})),
        "world_rules": arr(S),
        "initial_state": STATE,
    }
)
BRIEF = obj(
    {
        "title": S,
        "genre": S,
        "direction": S,
        "page_count": N,
        "panels_per_page": N,
        "reading_direction": enum("LTR", "RTL"),
        "page_mode": enum("single_page", "print_spreads"),
        "first_story_page_side": enum("right", "left"),
        "must_include": arr(S),
        "avoid": arr(S),
        "character_material": S,
    }
)
# Optional additions preserve old project files without an implicit migration.
BRIEF["properties"]["panel_mode"] = enum("fixed", "flexible")
BRIEF["properties"]["character_refs"] = arr({"type": "object"})
OPTIONS = obj(
    {
        "options": arr(
            obj(
                {
                    "id": S,
                    "title": S,
                    "logline": S,
                    "beats": arr(S),
                    "ending": S,
                    "tradeoff": S,
                }
            ),
            3,
        )
    }
)
OUTLINE = obj(
    {
        "selected_option": S,
        "premise": S,
        "beats": arr(S),
        "ending": S,
        "must_include_mapping": arr(S),
        "unknowns": arr(S),
    }
)
PLAN = obj(
    {
        "pages": arr(
            obj(
                {
                    "id": S,
                    "number": N,
                    "scene_ids": arr(S, 1),
                    "character_ids": arr(S),
                    "panel_count": N,
                    "purpose": S,
                    "beat": S,
                    "hook": S,
                    "reveal": S,
                    "turn_after": {"type": "boolean"},
                    "constraints": arr(S),
                }
            ),
            1,
        )
    }
)
PANEL = obj(
    {
        "id": S,
        "page_id": S,
        "number": N,
        "purpose": S,
        "layout": S,
        "aspect_ratio": {"type": "string", "pattern": "^[1-9][0-9]{0,3}:[1-9][0-9]{0,3}$"},
        "shot": S,
        "angle": S,
        "composition": S,
        "scene_id": S,
        "characters": arr(
            obj(
                {
                    "character_id": S,
                    "position": S,
                    "gaze": S,
                    "action": S,
                    "expression": S,
                }
            )
        ),
        "main_instant": S,
        "background": S,
        "props": arr(obj({"prop_id": S, "position": S, "state": S})),
        "transition_in": S,
        "transition_out": S,
        "state_after": STATE,
        "drawing_description": S,
    }
)
TEXT = obj(
    {
        "id": S,
        "panel_id": S,
        "kind": enum("dialogue", "narration", "sfx"),
        "speaker_id": nullable,
        "text": S,
        "bubble_position": S,
        "order": N,
    }
)
PAGE = obj(
    {
        "id": S,
        "number": N,
        "reading_direction": enum("LTR", "RTL"),
        "layout": S,
        "page_turn_note": S,
        "state_in": STATE,
        "panels": arr(PANEL, 1),
        "lettering": arr(TEXT),
        "state_out": STATE,
    }
)
PAGE["properties"]["layout_rows"] = arr(
    obj(
        {
            "weight": {"type": "number", "minimum": 0.5, "maximum": 4},
            "panels": arr(
                obj(
                    {
                        "panel_id": S,
                        "weight": {"type": "number", "minimum": 0.5, "maximum": 4},
                    }
                ),
                1,
            ),
        }
    ),
    1,
)
PAGE["properties"]["layout_rows"]["maxItems"] = 5
PAGE["properties"]["layout_rows"]["items"]["properties"]["panels"]["maxItems"] = 3
PATCH = obj({"panel": PANEL, "lettering": arr(TEXT)})
AUDIT = obj(
    {
        "verdict": enum("pass", "needs_revision"),
        "issues": arr(
            obj(
                {
                    "severity": enum("error", "warning"),
                    "page_id": S,
                    "panel_id": nullable,
                    "category": S,
                    "evidence": S,
                    "suggestion": S,
                }
            )
        ),
        "unknown_canon": arr(S),
        "reviewed_scope": S,
    }
)
SCHEMAS = {
    "canon": CANON,
    "options": OPTIONS,
    "outline": OUTLINE,
    "plan": PLAN,
    "page": PAGE,
    "panel": PATCH,
    "audit": AUDIT,
    "brief": BRIEF,
}
SCHEMAS["drawing_prompt"] = obj(
    {
        "panel_id": S,
        "positive": S,
        "negative": S,
        "composition_variants": {**arr(S, 3), "maxItems": 6},
        "character_anchors": arr(obj({"character_id": S, "tags": S})),
        "adaptation_notes": S,
    }
)

# Optional for old histories; required by the current generator.
# Defined here to avoid importing engine while schemas is initializing.
SCHEMAS["drawing_prompt"]["properties"]["framing"] = {"type": "object"}

VISUAL_PLAN = obj({
    "version": {"type": "integer", "const": 1},
    "detailed_scene": {"type": "string", "minLength": 1, "maxLength": 1800},
    "must_show": {**arr(S, 1), "maxItems": 12},
    "relationships": {**arr(S), "maxItems": 8},
    "uncertain": {**arr(S), "maxItems": 8},
})
# Optional in historical payloads, required for new model generation.
SCHEMAS["drawing_prompt"]["properties"]["visual_plan"] = VISUAL_PLAN
