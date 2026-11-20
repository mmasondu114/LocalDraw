"""Opt-out matte skin prompt preset for DeltaMix v6; no weight modification."""

CHECKPOINT = "animaDeltaMix38BQwen0_deltaMixV60.safetensors"
KEY = "delta_matte_skin"
POSITIVE = (
    "Matte anime illustration, (matte skin:1.25), soft diffuse skin tones, "
    "simple painted skin shadows, diffuse skin shading, broad soft light transitions, "
    "restrained skin highlights. Skin has a dry matte finish. "
    "Preserve eye catchlights and the scene's lighting."
)
NEGATIVE = (
    "shiny skin, glossy skin, oily skin, wet skin, sweaty skin, "
    "specular highlights on skin, white spots on skin, white nose highlight, "
    "shiny knees, shiny shoulders, plastic skin, airbrushed skin, "
    "(white dots on skin:1.5), (skin highlights:1.4), "
    "(specular skin reflection:1.4), (shiny skin:1.3), gleaming nose, waxy skin"
)


def apply(checkpoint, positive, negative, cfg):
    if checkpoint != CHECKPOINT or not cfg.get(KEY, False):
        return positive, negative
    # Reusing the recorded effective prompt must not accumulate the preset.
    if POSITIVE not in positive:
        positive = POSITIVE + "\n\n" + positive
    if NEGATIVE not in negative:
        negative = negative + (", " if negative.strip() else "") + NEGATIVE
    return positive, negative
