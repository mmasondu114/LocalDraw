"""Exercise the real studio owner-scoped repair without changing the source project."""
import argparse
import copy
import shutil
import sys
from pathlib import Path
from unittest.mock import patch
from PIL import Image, ImageChops, ImageOps

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from comic_workflow import comfyui, creative, drawing_tools, engine as e, lora_inspection
from comic_workflow.repository import Repository
from verify_guided_hand_studio_gpu import VerifiedApplication


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--owner', required=True)
    parser.add_argument('--box', type=int, nargs=4, required=True)
    parser.add_argument('--instruction', required=True)
    parser.add_argument('--seed', type=int, default=71011)
    parser.add_argument('--outline', type=Path)
    args = parser.parse_args()
    source = args.source.resolve()
    out = ROOT / 'data/test-results/owned-local-repair' / e.stamp()
    out.mkdir(parents=True)
    shutil.copytree(source / 'projects', out / 'projects')
    if (source / 'characters').exists(): shutil.copytree(source / 'characters', out / 'characters')
    repo = Repository(out / 'projects')
    pid = next(p.name for p in repo.root.iterdir() if p.is_dir())
    project = repo.get(pid)
    state = comfyui.drawing_state(repo, pid)
    batch = next(b for b in state['batches'] if b.get('images'))
    image = batch['images'][0]
    original = repo.folder(pid) / 'images' / image['file']
    with Image.open(original) as pic: width, height = pic.size
    owner = next(c['id'] for c in project['canon']['characters'] if c['name'] == args.owner)
    request = {'base_hash': e.digest(project), 'image': image['id'], 'owner': owner,
        'rect': [args.box[i] / (width if i % 2 == 0 else height) for i in range(4)],
        'instruction': args.instruction}
    if args.outline:
        request['outline'] = [[x/width,y/height] for x,y in e.read(args.outline)]
    e.save(out / 'request.json', request)
    source_loras = copy.deepcopy(batch['loras'])
    structural = {}
    for item in batch['loras']:
        path = ROOT / 'models/loras' / item['file']
        if not path.is_file(): path = ROOT / 'data/comfy-lora-diagnostic' / item['file']
        inspection = lora_inspection.inspect_lora(path)
        e.need(inspection['structurally_compatible'] is True, 'Existing source LoRA must be structurally SDXL compatible')
        structural[item['file']] = inspection
        # Compatibility evidence only; this isolated test grants no visual approval.
        item.update(confirmed_compatibility=True, compatible_checkpoint=batch['checkpoint'])
    e.save(repo.folder(pid) / 'images/index.json', state)
    e.save(out / 'source-provenance.json', {'source': source.relative_to(ROOT).as_posix(),
        'source_loras': source_loras, 'structural_checks': structural,
        'scope': 'Actual local_repair and GPU instruction translation/render/review; copied source cards and project. Source weights/seed retained, only compatibility metadata confirmed in the isolated copy.',
        'production_defaults_changed': False, 'new_weights': False})
    labels = {l['file']: {k: l[k] for k in ('character_id', 'character') if k in l} for l in batch['loras']}
    app = VerifiedApplication(repo, out)
    original_start = app.comfy.start
    def verified_start(options=None):
        result = original_start({**(options or {}), 'diagnostic_loras_directory': str(ROOT / 'data/comfy-lora-diagnostic')})
        stats = comfyui.api('/system_stats')
        e.need(any(d.get('type') == 'cuda' for d in stats['devices']), 'Drawing GPU proof missing')
        e.need('--gpu-only' in stats['system']['argv'], 'CPU fallback forbidden')
        e.save(out / 'drawing-gpu-proof.json', stats)
        return result
    app.comfy.start = verified_start
    cfg = {**comfyui.config(), 'checkpoint': batch['checkpoint']}
    try:
        print('Running actual owner-scoped local repair, translation and review on GPU.', flush=True)
        with patch.object(comfyui, 'config', return_value=cfg), \
             patch.object(comfyui, 'lora_labels', return_value=labels), \
             patch.object(comfyui.secrets, 'randbelow', return_value=args.seed), \
             patch.object(creative, 'CharacterLibrary', return_value=app.characters):
            result = drawing_tools.local_repair(app, pid, request, lambda _: None, 'owned-repair-' + out.name)
        e.save(out / 'entry-result.json', result)
        repaired = next(b for b in comfyui.drawing_state(repo, pid)['batches'] if b['id'] == result['batch'])
        output = repo.folder(pid) / 'images' / repaired['images'][0]['file']
        shutil.copyfile(original, out / 'source.png'); shutil.copyfile(output, out / 'result.png')
        with Image.open(original) as a, Image.open(output) as b:
            a = a.convert('RGB'); b = b.convert('RGB')
            e.need(a.size == b.size, 'Repair output dimensions changed')
            diff = ImageChops.difference(a, b)
            mask = Image.open(ROOT / 'data/comfy-input/repairs' / ('owned-repair-' + out.name) / 'mask.png').convert('L')
            e.need(ImageChops.multiply(diff, ImageOps.invert(mask).convert('RGB')).getbbox() is None, 'Pixels outside selection changed')
            e.need(diff.getbbox() is not None, 'Repair changed no pixels')
            proof = {'size': list(a.size), 'changed_bbox': list(diff.getbbox()), 'outside_selection_changed': False}
            crop = (max(0,args.box[0]-120), max(0,args.box[1]-120), min(width,args.box[2]+120), min(height,args.box[3]+120))
            a.crop(crop).resize(((crop[2]-crop[0])*2, (crop[3]-crop[1])*2)).save(out / 'source-cuff.png')
            b.crop(crop).resize(((crop[2]-crop[0])*2, (crop[3]-crop[1])*2)).save(out / 'result-cuff.png')
        e.save(out / 'pixel-preservation.json', proof)
        e.save(out / 'batch.json', repaired)
        e.save(out / 'report.json', {'status': 'rendered_reviewed_awaiting_direct_image_inspection',
            'gpu_only': True, 'entry': 'drawing_tools.local_repair', 'original_project_untouched': True,
            'pixel_preservation': proof, 'model_review': repaired['images'][0]['review'], 'whole_goal_complete': False})
        e.save(out.parent / 'latest.json', {'path': str(out), 'acceptance': 'pending_visual_review'})
        print(out.relative_to(ROOT).as_posix(), flush=True)
    finally:
        app.model.stop(); app.comfy.stop()


if __name__ == '__main__': main()
