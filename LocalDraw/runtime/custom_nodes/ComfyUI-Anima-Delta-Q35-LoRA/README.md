# ComfyUI-Anima-Delta-Q35-LoRA

Standalone LoRA adapter for Anima 3.8B v1.1 / Delta Mix.

Does not replace `ComfyUI-Anima-Delta-Q35`. Install next to it.

Canvas: **DeltaMix Q35 LoRA** (`DeltaMixQ35LoraLoader`)
Menu: **Anima Delta Mix**

```text
UNETLoader → DeltaMix Q35 LoRA → DeltaMix Q35 Attach → Prompt
```

28 / 40 / 52 Anima LoRA → 52-block addresses on the historical floor map.
Drops Connector, llm_adapter, Gen-2, and watch slots [7, 15, 23, 31] by default.
CLIP optional, Qwen 0.6B only. Do not connect Qwen 3.5 4B.

Defaults: strength_model 0.65, skip_gen2 on, skip_watch on, strength_clip 0.
