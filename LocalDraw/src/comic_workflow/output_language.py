"""Shared language policy for generated creative content."""

DEFAULT_OUTPUT_POLICY = (
    "By default, write comic dialogue, narration, sound effects and every lettering.text in "
    "Simplified Chinese, preserving meaning, speaker voice and story details. Use another "
    "lettering language only when the user explicitly requests it. Do not add bilingual text. "
    "Write all newly generated natural-language content in English, including story prose, "
    "scene headings, titles, character and scene descriptions, "
    "storyboard and drawing descriptions, visual plans, assumptions, changes, and review notes. "
    "The Chinese dialogue, narration and sound-effects rule above is the exception to this English rule. "
    "Keep positive and negative image-generation prompts in English. Preserve every source detail, "
    "speaker, action, motivation, causal link, prop state, and setup/payoff. "
    "Keep machine identifiers, schema keys, enum values, existing state keys, and saved character "
    "lookup names unchanged; these are references, not prose to translate. "
    "Copy explicitly fixed continuity states exactly."
)
