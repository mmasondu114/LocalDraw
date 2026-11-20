"""Model-specific LoRA directories, with support for pre-migration references."""

def is_anima(checkpoint):
    from .prompt_models import MODELS
    return MODELS.get(checkpoint, (None, None))[1] in {"anima38", "anima", "delta"}


def directory(checkpoint):
    from .prompt_models import MODELS
    family = MODELS.get(checkpoint, (None, None))[1]
    return family if family in {"krea2", "moss_image", "qwen_edit"} else "anime" if is_anima(checkpoint) else "sdxl"


def matches(filename, checkpoint):
    first = filename.replace("\\", "/").split("/", 1)[0]
    if first in {"anime", "sdxl", "krea2", "moss_image", "qwen_edit"}:
        return first == directory(checkpoint)
    # Existing installations can resolve their old paths before being migrated.
    return directory(checkpoint) not in {"krea2", "moss_image", "qwen_edit"} and (first == "Anima") == is_anima(checkpoint)
