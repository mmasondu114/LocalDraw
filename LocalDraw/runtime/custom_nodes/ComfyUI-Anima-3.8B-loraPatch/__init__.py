"""
ComfyUI-Anima-3.8B-loraPatch

Applies 28-block Anima LoRAs (anima-base-v1.0 / anima-preview and its finetunes)
-- and 40-block Anima-2.9B LoRAs -- to the 52-block Anima-3.8B model by remapping
their block indices.

Why this is needed
------------------
Anima-3.8B is a block-expansion (depth upscale) of Anima-2.9B, not a retrain.
Verified by direct weight comparison against anima29B_v10.safetensors and
anima-3.8b.safetensors (sha256 over each block's probe tensors, plus cosine
cross-checks via WAI-ANIMA-base-1.0): all 40 Anima-2.9B blocks -- which include
the 28 anima-base blocks bit-identically -- are present in 3.8B bit-identically
(max|diff| == 0), as are x_embedder, llm_adapter and final_layer. Twelve new
trained blocks were inserted at indices 3, 7, 11, 15, 19, 23, 27, 31, 35, 39,
43, 47.

So base block j no longer lives at index j:

    base  0  1  2  3  4  5  6  7  8  9 10 11 12 13 ...
    3.8B  0  1  4  5  8  9 12 13 16 17 20 21 24 25 ...

and likewise for the 2.9B layout:

    2.9B  0  1  2  3  4  5  6  7  8  9 10 11 12 13 ...
    3.8B  0  1  2  4  5  6  8  9 10 12 13 14 16 17 ...

A LoRA trained on the 28-block base (or the 40-block 2.9B) is therefore applied
to the wrong layer from some block onward -- and silently, because those block
indices do exist in the 52-block model, so no "lora key not loaded" warning is
ever emitted.

What this does
--------------
Wraps comfy.lora.load_lora and rewrites its key_map so LoRA block j targets the
block that actually holds the original weights. The key_map already contains
both the kohya (lora_unet_blocks_5_...) and the peft (diffusion_model.blocks.5....)
spellings pointing at the same model key, so rewriting the map values fixes both
LoRA formats at once. Text-encoder keys (lora_te_*) go through model_lora_keys_clip
and are left untouched.

Remapping is applied only when all of the following hold:
  - the model is Anima (its key_map references diffusion_model.llm_adapter.*)
  - the model has exactly blocks 0..51 (the layout this mapping was measured on)
  - the LoRA covers exactly blocks 0..27 (base LoRA) or 0..39 (2.9B LoRA)
Anything else -- a 28-block model, a 40-block model (handled by the 2.9B patch),
a native 52-block LoRA, a non-Anima model, an unknown block layout -- is passed
through unchanged. Coexists safely with ComfyUI-Anima-2.9B-loraPatch.

Set the environment variable ANIMA_38B_LORA_REMAP=0 to disable at runtime.
"""

import logging
import os
import re

import comfy.lora

LOG_PREFIX = "[Anima 3.8B LoRA Patch]"

# Blocks inserted by each depth expansion, in ascending order.
INSERTED_BLOCKS_29B = (2, 5, 8, 11, 14, 17, 21, 24, 27, 30, 33, 36)
INSERTED_BLOCKS_38B = (3, 7, 11, 15, 19, 23, 27, 31, 35, 39, 43, 47)

NUM_BLOCKS_38B = 52
NUM_BLOCKS_29B = 40
NUM_BLOCKS_BASE = 28


def _build_block_map(inserted, total):
    # The blocks kept from the previous model are the ones not inserted, in order.
    kept = [i for i in range(total) if i not in inserted]
    return {j: i for j, i in enumerate(kept)}


# Anima-2.9B block index -> Anima-3.8B block index
TO29B_TO_38B = _build_block_map(INSERTED_BLOCKS_38B, NUM_BLOCKS_38B)
assert len(TO29B_TO_38B) == NUM_BLOCKS_29B

# anima-base block index -> Anima-3.8B block index (base -> 2.9B -> 3.8B)
BASE_TO_38B = {
    j: TO29B_TO_38B[i]
    for j, i in _build_block_map(INSERTED_BLOCKS_29B, NUM_BLOCKS_29B).items()
}
assert len(BASE_TO_38B) == NUM_BLOCKS_BASE
assert sorted(BASE_TO_38B.values()) == [0, 1, 4, 5, 8, 9, 12, 13, 16, 17, 20, 21, 24, 25, 26,
                                        29, 30, 33, 34, 37, 38, 41, 42, 45, 46, 49, 50, 51]

# Matches a model-side (key_map value) diffusion block key.
# Anchored so that diffusion_model.llm_adapter.blocks.N.* is NOT matched.
MODEL_BLOCK_RE = re.compile(r"^(diffusion_model\.blocks\.)(\d+)(\..*)$")

# Matches a LoRA-side key belonging to a diffusion block, in the formats seen in
# the wild for Anima. llm_adapter and text-encoder keys do not match these.
LORA_BLOCK_RES = (
    re.compile(r"^lora_unet_blocks_(\d+)_"),           # kohya  (networks.lora_anima)
    re.compile(r"^diffusion_model\.blocks\.(\d+)\."),  # peft / generic
    re.compile(r"^blocks\.(\d+)\."),                   # bare
)


def _enabled():
    return os.environ.get("ANIMA_38B_LORA_REMAP", "1").strip().lower() not in ("0", "off", "false", "no")


def _model_block_indices(to_load):
    out = set()
    for value in to_load.values():
        if not isinstance(value, str):
            continue
        m = MODEL_BLOCK_RE.match(value)
        if m is not None:
            out.add(int(m.group(2)))
    return out


def _lora_block_indices(lora):
    out = set()
    for key in lora.keys():
        if not isinstance(key, str):
            continue
        for rex in LORA_BLOCK_RES:
            m = rex.match(key)
            if m is not None:
                out.add(int(m.group(1)))
                break
    return out


def _is_anima(to_load):
    for value in to_load.values():
        if isinstance(value, str) and value.startswith("diffusion_model.llm_adapter."):
            return True
    return False


def _remap_key_map(to_load, block_map):
    """Return a copy of to_load with diffusion block targets shifted to the 3.8B layout."""
    out = {}
    remapped = 0
    for key, value in to_load.items():
        m = MODEL_BLOCK_RE.match(value) if isinstance(value, str) else None
        if m is None:
            out[key] = value
            continue

        src = int(m.group(2))
        dst = block_map.get(src)
        if dst is None:
            # Blocks beyond the LoRA's source model are unreachable. Dropping these
            # entries keeps the map a clean bijection onto real target blocks.
            continue

        out[key] = "{}{}{}".format(m.group(1), dst, m.group(3))
        if dst != src:
            remapped += 1
    return out, remapped


def _needs_remap(lora, to_load):
    if not _enabled():
        return None
    if not _is_anima(to_load):
        return None

    model_blocks = _model_block_indices(to_load)
    if model_blocks != set(range(NUM_BLOCKS_38B)):
        # 28-block Anima or 40-block Anima-2.9B (handled by the 2.9B patch), or an
        # unknown layout this map was not measured against -- stay out of the way.
        if len(model_blocks) not in (0, NUM_BLOCKS_BASE, NUM_BLOCKS_29B):
            logging.info(
                "{} unknown Anima block layout ({} blocks); leaving LoRA untouched.".format(
                    LOG_PREFIX, len(model_blocks)
                )
            )
        return None

    lora_blocks = _lora_block_indices(lora)
    if lora_blocks == set(range(NUM_BLOCKS_BASE)):
        return BASE_TO_38B
    if lora_blocks == set(range(NUM_BLOCKS_29B)):
        return TO29B_TO_38B

    if lora_blocks and lora_blocks != set(range(NUM_BLOCKS_38B)):
        logging.info(
            "{} LoRA covers {} diffusion blocks (max {}); not a 28- or 40-block Anima LoRA, "
            "leaving it untouched.".format(LOG_PREFIX, len(lora_blocks), max(lora_blocks))
        )
    return None


_orig_load_lora = getattr(comfy.lora, "load_lora", None)


def patched_load_lora(lora, to_load, *args, **kwargs):
    block_map = None
    try:
        block_map = _needs_remap(lora, to_load)
        if block_map is not None:
            new_to_load, remapped = _remap_key_map(to_load, block_map)
            logging.info(
                "{} {}-block LoRA on 52-block Anima-3.8B: remapped {} target keys "
                "to the expanded block layout.".format(
                    LOG_PREFIX, NUM_BLOCKS_BASE if block_map is BASE_TO_38B else NUM_BLOCKS_29B, remapped
                )
            )
            to_load = new_to_load
    except Exception as err:
        # Never let this patch break LoRA loading -- fall through untouched.
        logging.warning("{} remap skipped due to error: {}".format(LOG_PREFIX, err))

    return _orig_load_lora(lora, to_load, *args, **kwargs)


patched_load_lora._anima38b_lora_patch = True

if _orig_load_lora is None:
    logging.warning(
        "{} comfy.lora.load_lora not found; cannot install. This ComfyUI version is "
        "not supported by this patch.".format(LOG_PREFIX)
    )
elif getattr(_orig_load_lora, "_anima38b_lora_patch", False):
    logging.info("{} already installed; skipping.".format(LOG_PREFIX))
else:
    comfy.lora.load_lora = patched_load_lora
    logging.info(
        "{} installed (base block j -> 3.8B block {}...). Set ANIMA_38B_LORA_REMAP=0 to disable.".format(
            LOG_PREFIX, [BASE_TO_38B[j] for j in range(6)]
        )
    )

# No workflow nodes: this is a load-time patch only.
NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}
