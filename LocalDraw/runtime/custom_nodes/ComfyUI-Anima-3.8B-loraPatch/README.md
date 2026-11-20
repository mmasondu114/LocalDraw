# ComfyUI-Anima-3.8B-loraPatch

Applies 28-block Anima LoRAs (anima-base-v1.0 / anima-preview and its finetunes)
and 40-block Anima-2.9B LoRAs to the 52-block **Anima-3.8B** model by remapping
their block indices, at load time and without converting files. A sibling of
[ComfyUI-Anima-2.9B-loraPatch](https://github.com/sparklingcoffee777/ComfyUI-Anima-2.9B-loraPatch);
both can be installed together.

## Installation

1. Clone this folder into `ComfyUI/custom_nodes/`:

   ```
   cd ComfyUI/custom_nodes
   git clone https://github.com/KitagawaCoding/ComfyUI-Anima-3.8B-loraPatch.git
   ```

   Portable Windows install: `ComfyUI_windows_portable\ComfyUI\custom_nodes\`

   Or download the ZIP from GitHub and extract it there. The folder name should
   stay `ComfyUI-Anima-3.8B-loraPatch`.

2. Also install [comfyui-anima-3-8B](https://github.com/GumGum10/comfyui-anima-3-8B)
   (or any loader that sets `num_blocks = 52`). Anima-3.8B must load all 52
   blocks; this patch cannot remap a model that was already truncated.

3. Restart ComfyUI.

4. Load Anima-3.8B and apply LoRAs with the normal LoRA Loader nodes. Remapping
   is automatic. **Nothing appears in the Add Node menu** — the only confirmation
   is the startup log line below.

No extra pip packages. No workflow changes.

To uninstall, delete the folder or rename it with a `.disabled` suffix.

The [2.9B LoRA patch](https://github.com/sparklingcoffee777/ComfyUI-Anima-2.9B-loraPatch)
can sit in `custom_nodes` at the same time. Each patch gates on model block
count, so they do not fight.

## Why this is needed

Anima-3.8B is a block-expansion (depth upscale) of Anima-2.9B, not a retrain:

- Anima-2.9B (40 blocks) contains the 28 anima-base blocks bit-identically, with
  12 blocks inserted at 2, 5, 8, 11, 14, 17, 21, 24, 27, 30, 33, 36.
- Anima-3.8B (52 blocks) contains all 40 Anima-2.9B blocks -- and therefore the
  28 base blocks -- **bit-identically** (verified by sha256 over each block's
  tensors, plus cosine cross-checks through WAI-ANIMA-base-1.0, a finetune of
  anima-base), with 12 new trained blocks inserted at
  **3, 7, 11, 15, 19, 23, 27, 31, 35, 39, 43, 47**.

So base block `j` no longer sits at index `j`:

```
base  0  1  2  3  4  5  6  7  8  9 10 11 12 13 ...
3.8B  0  1  4  5  8  9 12 13 16 17 20 21 24 25 ...
```

A LoRA trained on the 28-block base is therefore applied to the wrong layer from
block 2 onward -- and **silently**, because blocks 2..27 do exist in the 52-block
model, so no "lora key not loaded" warning is ever emitted. Users see this as
"the LoRA doesn't work" or as strength increases degrading output instead of
intensifying the style.

## How the fix works

The custom node monkey-patches `comfy.lora.load_lora` at ComfyUI startup,
rewriting block indices inside the key_map (the LoRA-key → model-key table).
Both kohya-style keys (`lora_unet_blocks_5_...`) and peft/AI-Toolkit keys
(`diffusion_model.blocks.5...`) route through the same table, so one rewrite
covers both formats.

Remapping only triggers when the model is Anima (its key_map references
`diffusion_model.llm_adapter.*`), the model has exactly blocks 0..51, and the
LoRA covers exactly blocks 0..27 (base) or 0..39 (2.9B). Everything else passes
through untouched, as do text-encoder (`lora_te_*`) and llm_adapter keys.

Set `ANIMA_38B_LORA_REMAP=0` to disable at runtime; startup logs confirm
activation. No nodes appear in the Add Node menu -- it activates automatically.

## Verifying it works

On startup:

```
[Anima 3.8B LoRA Patch] installed (base block j -> 3.8B block [0, 1, 4, 5, 8, 9]...). Set ANIMA_38B_LORA_REMAP=0 to disable.
```

When a 28-block (or 40-block) LoRA is applied to 3.8B:

```
[Anima 3.8B LoRA Patch] 28-block LoRA on 52-block Anima-3.8B: remapped 1040 target keys to the expanded block layout.
```

(The key count varies per LoRA. A 40-block LoRA logs `40-block` instead.)

If that install line is missing, ComfyUI did not load the folder — check the
path and restart. If LoRAs still look wrong, confirm the 3.8B loader is
installed and the model is actually 52 blocks.

## Requirements & caveats

- The model must load all 52 blocks (see Installation step 2). This patch
  cannot help a truncated 28- or 40-block load.
- Even with correct layer mapping, behavior differs from the base model since
  the residual stream passes through 12 new trained blocks -- strength 0.5–0.7
  is a good starting point, as with the 2.9B patch.
- The measured block files: `anima29B_v10.safetensors`, `anima-3.8b.safetensors`
  (both from Civitai). If a future 3.8B revision changes the insertion layout,
  this patch stays out of the way rather than guessing.

## How the mapping was measured

Per block, sha256 was computed over `mlp.layer1.weight`, `self_attn.q_proj.weight`
and `cross_attn.q_proj.weight` for every block of both checkpoints. All 40
Anima-2.9B blocks matched exactly one Anima-3.8B block each; the 12 remaining
3.8B blocks are the inserted ones. The 28 base blocks were taken from the 2.9B
checkpoint (they are bit-identical to anima-base there, per the 2.9B patch's
tensor-by-tensor verification), and the composed base → 3.8B map was
independently confirmed by cosine-matching WAI-ANIMA-base-1.0 against every
3.8B block (agreement 28/28, best-match cosines ≈ 0.998 with wide margins).

Structure and credits follow sparklingcoffee777's ComfyUI-Anima-2.9B-loraPatch.

---

This was a vibe-coded project using [Z.ai](https://z.ai) GLM 5.3.
