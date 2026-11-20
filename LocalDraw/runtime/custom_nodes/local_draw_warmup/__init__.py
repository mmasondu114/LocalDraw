from comfy import model_management


class LocalDrawWarmModel:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"model": ("MODEL",)}}

    RETURN_TYPES = ()
    FUNCTION = "load"
    OUTPUT_NODE = True
    CATEGORY = "LocalDraw"

    @classmethod
    def IS_CHANGED(cls, model):
        return float("nan")  # Recheck placement even if an earlier warm-up was cached.

    def load(self, model):
        model_management.load_models_gpu([model])
        return {"ui": {"loaded": [True]}, "result": ()}


NODE_CLASS_MAPPINGS = {"LocalDrawWarmModel": LocalDrawWarmModel}
