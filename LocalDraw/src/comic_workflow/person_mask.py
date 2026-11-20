"""Install the local, hash-pinned GPU-only person mask node at drawing startup."""
import shutil
from .installed_assets import read_path
from . import engine as e
from .paths import ROOT


def install():
    cfg = e.read(ROOT / 'config/person-mask-sam.json')
    model = read_path(ROOT, cfg['model'])
    e.need(model.is_file(), '人物分割模型缺失，无法保护多人背景')
    destination = ROOT / 'runtime/custom_nodes/local_draw_masks'
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / 'scripts/comfy_person_mask_node.py', destination / '__init__.py')
    anime = e.read(ROOT / 'config/anime-person-mask.json')
    anime_model = read_path(ROOT, anime['model'])
    e.need(anime_model.is_file(), '动漫人物分割模型缺失，无法合成多人合照')
    shutil.copyfile(ROOT / 'scripts/comfy_anime_mask.py', destination / 'anime_mask.py')
    shutil.copyfile(ROOT / 'scripts/vendor/anime_seg/isnet.py', destination / 'anime_isnet.py')
    shutil.copyfile(ROOT / 'scripts/vendor/anime_seg/LICENSE', destination / 'anime_isnet.LICENSE')
    face = e.read(ROOT / 'config/face-layout.json')
    face_model = read_path(ROOT, face['model'])
    e.need(face_model.is_file(), '人物脸部检测模型缺失，无法校正多人合照比例')
    e.save(destination / 'settings.json', {**cfg, 'model': str(model),
           'anime': {**anime, 'model': str(anime_model)},
           'face': {**face, 'model': str(face_model)}})
