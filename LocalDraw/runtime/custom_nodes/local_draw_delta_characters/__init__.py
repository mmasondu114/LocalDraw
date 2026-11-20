"""Curated local Anima characters routed through the Delta Mix author's adapter."""
import json
from pathlib import Path

CATALOG = json.loads((Path(__file__).parent / 'catalog.json').read_text(encoding='utf-8'))
CHARACTERS = {row['label']: row for row in CATALOG}


class DeltaMixCharacterLoRA:
    CATEGORY = 'Delta Mix'
    FUNCTION = 'apply'
    RETURN_TYPES = ('MODEL', 'CLIP', 'STRING', 'STRING')
    RETURN_NAMES = ('model', 'clip', 'positive_prompt', 'details')

    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {
            'model': ('MODEL',), 'clip': ('CLIP',),
            'character': (list(CHARACTERS),),
            'strength': ('FLOAT', {'default': 0.65, 'min': 0.0, 'max': 1.5, 'step': 0.05}),
            'scene': ('STRING', {'multiline': True, 'default':
                'solo, upper body, standing in a peaceful garden, soft daylight, detailed background'}),
            'use_outfit_tags': ('BOOLEAN', {'default': True}),
        }}

    def apply(self, model, clip, character, strength, scene, use_outfit_tags):
        import nodes
        row = CHARACTERS[character]
        if len(model.model.diffusion_model.blocks) != 52:
            raise ValueError('This character workflow requires the 52-block Delta Mix model.')
        words = row['triggers'] if use_outfit_tags else row['triggers'][:1]
        prompt = ', '.join(['anime, masterpiece, best quality', *words, scene])
        if strength == 0:
            return model, clip, prompt, 'LoRA disabled for comparison: ' + character
        loader = nodes.NODE_CLASS_MAPPINGS.get('DeltaMixQ35LoraLoader')
        if loader is None:
            raise RuntimeError('Enable ComfyUI-Anima-Delta-Q35-LoRA with the Delta Mix launcher.')
        model_out, clip_out, log = loader().apply(
            model, row['lora_name'], strength, skip_gen2=True, skip_watch=True,
            clip=clip, strength_clip=0.0)
        return model_out, clip_out, prompt, character + '\n' + log


NODE_CLASS_MAPPINGS = {'DeltaMixCharacterLoRA': DeltaMixCharacterLoRA}
NODE_DISPLAY_NAME_MAPPINGS = {'DeltaMixCharacterLoRA': 'Delta Mix 角色 LoRA'}
