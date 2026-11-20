"""Automatic Delta Edit conditioning using the frozen character reference."""
import hashlib
from PIL import Image
from . import engine as e
from .paths import ROOT, DATA
from .character_binding import panel_characters
from .characters import CharacterLibrary
from .comfyui import lora_file_key

CHECKPOINT = 'animaDeltaMix38BQwen0_deltaMixV60.safetensors'
LORA = lora_file_key('anime/edit/AnimeEditV2.safetensors')


def selected_character_reference(project, panel, checkpoint, prefs, *, library=None):
    """Read-only resolution of an explicitly chosen character, including group shots."""
    key = (prefs or {}).get('reference_character_id')
    if not key or key not in {c['character_id'] for c in panel['characters']}:
        return None
    e.need(checkpoint == CHECKPOINT, '所选角色参考图需要 Anima Delta v6')
    e.need(prefs.get('consistency_lora') is True, '请开启一致性参考后使用所选角色参考图')
    e.need((ROOT/'models/loras'/LORA).is_file(), '角色参考所需的 Anima Edit v2 文件未就绪')
    character = next((c for c in panel_characters(project, panel) if c['id'] == key), None)
    e.need(character is not None, '参考图角色不在当前作品中')
    image_id = character.get('saved_character', {}).get('reference_image_id')
    e.need(image_id, character['name'] + ' 尚未绑定参考图')
    library = library or CharacterLibrary()
    source = library.image_path(image_id)
    e.need(source.is_file(), character['name'] + ' 的参考图不存在')
    return {'character_id': key, 'character': character['name'], 'source_image_id': image_id,
            'source': source}


def freeze_character_reference(selection):
    """Freeze original pixels, not a rewritten prompt or an inferred crop."""
    source = selection['source']
    content = source.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    with Image.open(source) as im:
        im.verify()
    with Image.open(source) as im:
        width, height = im.size
    target = DATA/'comfy-input'/'consistency'/(digest + source.suffix.lower())
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists() or target.read_bytes() != content:
        target.write_bytes(content)
    return {k: v for k, v in selection.items() if k != 'source'} | {
        'reference': 'consistency/' + target.name, 'reference_sha256': digest,
        'reference_width': width, 'reference_height': height,
        'reference_mode': 'selected_character_original'}


def freeze_reference(repo, pid, image_id):
    """Resolve a project image ID once; resumed jobs keep these exact pixels."""
    e.need(isinstance(image_id, str), '一致性参考须为作品候选图编号')
    folder = repo.folder(pid) / 'images'
    index = e.read(folder / 'index.json') if (folder / 'index.json').exists() else {}
    image = next((im for b in index.get('batches', []) for im in b.get('images', [])
                  if im.get('id') == image_id), None)
    e.need(image is not None, '一致性参考图不属于当前作品或已移除')
    source = (folder / image['file']).resolve()
    e.need(source.is_relative_to(folder.resolve()) and source.is_file(), '一致性参考图文件不存在')
    content = source.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    with Image.open(source) as im:
        im.verify()
    with Image.open(source) as im:
        width, height = im.size
    target = DATA / 'comfy-input' / 'consistency' / (digest + source.suffix.lower())
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists() or target.read_bytes() != content:
        target.write_bytes(content)
    return {'reference': 'consistency/' + target.name, 'reference_sha256': digest,
            'source_image_id': image_id, 'reference_width': width, 'reference_height': height,
            'reference_mode': 'fixed_project_image'}


def prepare(project, panel, checkpoint, cfg, options, lora_files, *, library=None):
    enabled = options.get('consistency_lora', cfg.get('consistency_lora', False))
    e.need(type(enabled) is bool, '一致性 LoRA 选项无效')
    def skipped(reason):
        return {'enabled': False, 'reason': reason}
    if not enabled:
        return skipped('已关闭')
    if checkpoint != CHECKPOINT:
        return skipped('当前自动一致性仅适用于 Delta v6')
    fixed = options.get('consistency_reference')
    if (options.get('external_prompt') and not fixed and options.get('consistency_lora') is not True) or options.get('local_repair') or (
        options.get('diagnostic') and not project.get('quick_sample')
    ):
        return skipped('本次独立诊断或专用流程保留原设置')
    weight = cfg.get('consistency_lora_weight', 0.8)
    e.need(type(weight) in (int, float) and 0 < weight <= 2, '一致性 LoRA 强度须大于 0 且不超过 2')
    e.need(LORA in lora_files and (ROOT/'models/loras'/LORA).is_file(),
           '一致性 LoRA 文件未就绪，请检查 Anima Edit v2 或关闭自动一致性')
    record = {'enabled': True, 'lora': LORA, 'lora_file': lora_files[LORA], 'weight': weight,
              'reference_mode': 'none', 'note': '已加载 LoRA；没有参考图时不锁定具体角色外观'}
    if options.get('external_prompt') and not fixed:
        # Explicitly enabled imported prompts use the LoRA without silently
        # adding image conditioning or changing their literal prompt text.
        return record
    if fixed:
        if fixed.get('reference_mode') == 'selected_character_original':
            e.need(fixed.get('character_id') in {c['character_id'] for c in panel['characters']},
                   '所选参考角色未出镜，不能套用其参考图')
        source = (DATA / 'comfy-input' / fixed['reference']).resolve()
        e.need(source.is_relative_to((DATA / 'comfy-input' / 'consistency').resolve())
               and source.is_file(), '固定一致性参考图不存在')
        e.need(hashlib.sha256(source.read_bytes()).hexdigest() == fixed['reference_sha256'],
               '固定一致性参考图已变化，请重新选择')
        return {**record, **fixed, 'note': '复用固定参考图保持外观与道具；不保证细节完全一致'}
    # Prompt sessions keep character-library images for identity metadata even
    # when image conditioning is unchecked. Do not infer consent from the card.
    prompt_input = project.get('prompt_input', {})
    if project.get('quick_sample') and prompt_input.get('use_reference') is not True:
        record['reference_reason'] = '未启用角色参考图'
        return record
    characters = panel_characters(project, panel)
    if len(characters) != 1:
        record['reference_reason'] = ('未选择角色' if not characters else '多人画面不自动套用单人参考')
        return record
    character = characters[0]
    if project.get('quick_sample'):
        choice = next((item for item in prompt_input.get('characters', [])
                       if item['name'] == character['name']), {})
        if choice.get('use_reference') is not True:
            record['reference_reason'] = '未勾选该角色的参考图'
            return record
    image_id = character.get('saved_character', {}).get('reference_image_id')
    if not image_id:
        record['reference_reason'] = '所选角色没有参考图'
        return record
    library = library() if callable(library) else library or CharacterLibrary()
    source = library.image_path(image_id)
    e.need(source.is_file(), character['name'] + ' 的一致性参考图不存在')
    content = source.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    target = DATA/'comfy-input'/'consistency'/(digest + source.suffix.lower())
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists() or target.read_bytes() != content:
        target.write_bytes(content)
    return {**record,
            'reference': 'consistency/' + target.name, 'reference_sha256': digest,
            'character_id': character['id'], 'character': character['name'],
            'reference_mode': 'fixed_character_original',
            'note': '辅助保持外观；不保证袖章等细节一致或动作准确'}


def apply(graph, record, cfg):
    if not record['enabled']:
        return
    model = graph['5']['inputs']['model']
    existing = next((n for n in graph.values() if n['class_type'] in (
        'DeltaMixQ35LoraLoader', 'LoraLoader', 'LoraLoaderModelOnly') and
        n['inputs'].get('lora_name', '').replace('\\', '/') == LORA), None)
    if existing:
        record['weight'] = existing['inputs']['strength_model']
        record['loading'] = 'existing_manual_lora'
    else:
        graph['consistency-lora'] = {'class_type': 'DeltaMixQ35LoraLoader', 'inputs': {
            'model': model, 'clip': graph['2']['inputs']['clip'], 'lora_name': record['lora_file'],
            'strength_model': record['weight'], 'strength_clip': 0.0,
            'skip_gen2': True, 'skip_watch': True}}
        model = ['consistency-lora', 0]
        record['loading'] = 'automatic'
    graph['5']['inputs']['model'] = model
    if not record.get('reference'):
        return
    graph['consistency-image'] = {'class_type': 'LoadImage', 'inputs': {'image': record['reference']}}
    width, height = cfg['width'], cfg['height']
    fixed = record.get('reference_mode') in ('fixed_project_image', 'selected_character_original')
    if fixed:
        ratio = min(width / record['reference_width'], height / record['reference_height'])
        width, height = [max(8, int(v * ratio / 8) * 8)
                         for v in (record['reference_width'], record['reference_height'])]
    graph['consistency-scale'] = {'class_type': 'ImageScale', 'inputs': {
        'image': ['consistency-image', 0], 'upscale_method': 'lanczos',
        'width': width, 'height': height, 'crop': 'disabled' if fixed else 'center'}}
    pixels = ['consistency-scale', 0]
    if fixed and (width != cfg['width'] or height != cfg['height']):
        left, top = (cfg['width'] - width) // 2, (cfg['height'] - height) // 2
        graph['consistency-pad'] = {'class_type': 'ImagePadForOutpaint', 'inputs': {
            'image': pixels, 'left': left, 'top': top,
            'right': cfg['width'] - width - left, 'bottom': cfg['height'] - height - top,
            'feathering': 0}}
        pixels = ['consistency-pad', 0]
    record['reference_fit'] = 'contain_pad' if fixed else 'center_crop'
    graph['consistency-latent'] = {'class_type': 'VAEEncode', 'inputs': {
        'pixels': pixels, 'vae': ['12', 0]}}
    graph['consistency-reference'] = {'class_type': 'ApplyCosmosReferenceLatent', 'inputs': {
        'model': model, 'ref_latents.ref_latent_1': ['consistency-latent', 0]}}
    graph['5']['inputs']['model'] = ['consistency-reference', 0]
