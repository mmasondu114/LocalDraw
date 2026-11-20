from .nodes_lora import DeltaMixQ35LoraLoader

NODE_CLASS_MAPPINGS = {
    "DeltaMixQ35LoraLoader": DeltaMixQ35LoraLoader,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "DeltaMixQ35LoraLoader": "DeltaMix Q35 LoRA",
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
