"""Review a real repair target with its original studio project and references.

All language/vision inference is on GPU. Observations support direct visual
inspection and never automatically adopt a candidate or change the source.
"""
import argparse
from pathlib import Path
import sys
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from comic_workflow import creative, engine as e, framing
from comic_workflow.character_binding import panel_characters
from comic_workflow.characters import CharacterLibrary
from comic_workflow.repository import Repository
from verify_guided_hand_studio_gpu import VerifiedApplication


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--candidate', type=Path, action='append', default=[])
    args = parser.parse_args()
    source = args.source.resolve()
    request = e.read(args.request)
    out = ROOT / 'data/test-results/targeted-hand-review-gpu' / e.stamp()
    out.mkdir(parents=True)
    repo = Repository(source / 'projects')
    pid = next(p.name for p in repo.root.iterdir() if p.is_dir())
    project = repo.get(pid)
    panel = project['pages']['P001']['panels'][0]
    owner = next(c for c in project['canon']['characters']
                 if c['id'] == request['hand_guidance']['owner'])
    target = {**request['hand_guidance'], 'character_name': owner['name'],
              'rect': request['rect']}
    refs = panel_characters(project, panel)
    app = VerifiedApplication(repo, out)
    if (source / 'characters').exists():
        app.characters = CharacterLibrary(source / 'characters')
    cases = [('source', source / 'result.png')]
    cases.extend((f'candidate_{index + 1}', image.resolve())
                 for index, image in enumerate(args.candidate))
    e.save(out / 'provenance.json', {
        'source_project': source.relative_to(ROOT).as_posix(),
        'request': args.request.as_posix(), 'repair_target': target,
        'original_project_untouched': True, 'production_adoption': False})
    try:
        e.need(not app.model.status()['ready'], 'Existing language service is busy')
        print('Loading existing studio vision model on GPU.', flush=True)
        app.prepare_language(vision=True, update=lambda _: None)
        results = []
        for label, image in cases:
            e.need(image.is_file(), 'Review image is missing')
            folder = out / label
            folder.mkdir()
            print('Reviewing actual target:', label, flush=True)
            with patch.object(creative, 'CharacterLibrary', return_value=app.characters):
                review = creative.review_image(
                    panel, panel['drawing_description'], image,
                    framing.source_text(project, panel), refs,
                    folder / 'production-review', lambda _: None,
                    repair_target=target)
            e.save(folder / 'review.json', review)
            # No character references here: independently enumerate the actual
            # target's visible fingertips before judging its geometry.
            with Image.open(image) as pic:
                box = tuple(round(request['rect'][i] *
                                  (pic.width if i % 2 == 0 else pic.height))
                            for i in range(4))
                close = pic.crop(box)
                scale = 640 / max(close.size)
                close = close.resize((round(close.width * scale),
                                      round(close.height * scale)), Image.Resampling.LANCZOS)
                crop = folder / 'target-only.png'
                close.save(crop)
                crop_size = list(close.size)
            integer = {'type': 'integer'}
            schema = creative.obj({
                'visible_fingers': creative.arr(creative.obj({
                    'tip_x': integer, 'tip_y': integer,
                    'connected_to_palm': creative.enum('yes', 'no', 'uncertain'),
                    'observation': creative.S})),
                'actual_glove_color': creative.S,
                'thumb_observation': creative.S,
                'wrist_observation': creative.S,
                'shape_verdict': creative.enum('pass', 'reject', 'uncertain'),
                'reason': creative.S})
            evidence, metrics = creative.structured(
                '只检查这张唯一局部图中手部可见事实，不猜角色姓名或归属。'
                '逐个列出真实可见指尖，坐标原点在图左上角。每个真实指尖只列一次。'
                '袖口、衣服、装饰、手指高光不能另算手指。不要预设指尖数量。'
                '说明每根可见手指是否连续连接到同一手掌，拇指所在侧、关节弯曲方向和腕口是否连续。'
                '描述实际可见的手套颜色。看不清的部分写uncertain，不能编造正常形状。'
                '存在融合、多余手掌或不合关节的弯折则reject；只有形状可确认合理才pass。',
                {'crop_width': crop_size[0], 'crop_height': crop_size[1]},
                schema, folder / 'fingertip-review', lambda _: None,
                [('唯一待核对的实际手部局部', crop)], 1400)
            invalid = [i for i, finger in enumerate(evidence['visible_fingers'])
                       if not (0 <= finger['tip_x'] < crop_size[0] and
                               0 <= finger['tip_y'] < crop_size[1])]
            e.save(folder / 'finger-evidence.json', {
                'evidence': evidence, 'metrics': metrics, 'gpu_only': True,
                'coordinate_bounds_valid': not invalid, 'invalid_tip_indices': invalid,
                'automatic_acceptance': False})
            results.append({
                'case': label, 'image': image.relative_to(ROOT).as_posix(),
                'production_verdict': review['verdict'],
                'interaction_anatomy': review.get('interaction_anatomy'),
                'hand_repair_target': review.get('hand_repair_target'),
                'finger_evidence': evidence,
                'coordinate_bounds_valid': not invalid})
            e.save(out / 'results.json', {
                'gpu_only': True, 'results': results,
                'production_adoption': False, 'awaiting_direct_inspection': True})
            print(label, review['verdict'], evidence['shape_verdict'], flush=True)
        e.save(out.parent / 'latest.json', {'path': str(out), 'completed': True})
        print(out.relative_to(ROOT).as_posix(), flush=True)
    finally:
        app.model.stop()


if __name__ == '__main__':
    main()
