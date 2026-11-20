"""Published SDXL inpaint graph for a user-assigned hand, with exact preservation.

Uses existing Fooocus weights, the existing hand LoRA and existing depth
ControlNet. This remains an opt-in recipe until actual studio image acceptance.
"""
import hashlib
import json
from pathlib import Path
import subprocess

from PIL import Image

from .installed_assets import models as model_directory
from . import engine as e
from .paths import ROOT, DATA

AUTHOR = 'https://github.com/Acly/comfyui-inpaint-nodes'
REVISION = '5b7681746fe19e563113d27366ba85236bc1ba5b'
WEIGHTS = {
    'fooocus_inpaint_head.pth': '32f7f838e0c6d8f13437ba8411e77a4688d77a2e34df8857e4ef4d51f6b97692',
    'inpaint_v26.fooocus.patch': 'f8657a025104e22d70f9c060635d8e8c2196f433871a2f68dc40abd2171f0d59',
}


def prepare_crop(repair):
    inputs = (DATA / 'comfy-input').resolve()
    paths = {name: (inputs / repair[name]).resolve() for name in ('original', 'mask', 'depth')}
    for path in paths.values():
        e.need(path.is_relative_to(inputs) and path.is_file(), '手部补绘素材缺失或路径无效')
    folder = paths['original'].parent
    e.need(all(path.parent == folder for path in paths.values()), '手部补绘素材来源不一致')
    with Image.open(paths['original']) as original, Image.open(paths['mask']) as mask, Image.open(paths['depth']) as depth:
        e.need(original.size == mask.size == depth.size, '手部补绘素材画幅不一致')
        bounds = mask.convert('L').getbbox()
        e.need(bounds is not None, '手部描边为空')
        crop = (max(0, bounds[0] - 100), max(0, bounds[1] - 120),
                min(original.width, bounds[2] + 100), min(original.height, bounds[3] + 120))
        width, height = crop[2] - crop[0], crop[3] - crop[1]
        size = (768, max(64, round(768 * height / width / 64) * 64))
        # A very thin selection must not produce an unbounded neural canvas.
        e.need(size[1] <= 1536, '修复区域过于狭长，请重新选择整只手')
        for name, picture in (('crop', original), ('mask', mask), ('depth', depth)):
            method = Image.Resampling.LANCZOS if name == 'crop' else Image.Resampling.BILINEAR
            picture.crop(crop).convert('RGB').resize(size, method).save(folder / f'fooocus-{name}.png')
        mask.crop(crop).convert('RGB').save(folder / 'fooocus-exact-mask.png')
    record = {'crop': list(crop), 'neural_size': list(size), 'original_size': repair['placement']['size'],
              'crop_mask_box': list(bounds), 'author': AUTHOR, 'author_revision': REVISION,
              'composite_outside_exact_user_mask': False, 'neural_gpu_only': True}
    e.save(folder / 'fooocus-crop.json', record)
    prefix = folder.relative_to(inputs).as_posix()
    return crop, size, prefix


def workflow(checkpoint, seed, repair, cfg, control, positive, negative):
    revision = subprocess.check_output(['git', '-C', str(ROOT / 'runtime/custom_nodes/comfyui-inpaint-nodes'),
                                        'rev-parse', 'HEAD'], text=True).strip()
    e.need(revision == REVISION, '手部补绘组件版本未核对')
    for name, digest in WEIGHTS.items():
        path = model_directory(ROOT) / "inpaint" / name
        e.need(path.is_file(), '既有手部补绘模型缺失')
        with path.open('rb') as stream:
            e.need(hashlib.file_digest(stream, 'sha256').hexdigest() == digest, '既有手部补绘模型校验失败')
    pose_only=cfg.get('pose_only',False)
    e.need(not cfg.get('pose_control') or pose_only, '此补绘路径暂不支持同时叠加深度和骨架控制')
    crop, size, prefix = prepare_crop(repair)
    width, height = crop[2] - crop[0], crop[3] - crop[1]
    lora = cfg['hand_lora']
    graph = {
        '1': {'class_type': 'CheckpointLoaderSimple', 'inputs': {'ckpt_name': checkpoint}},
        '32': {'class_type': 'LoraLoader', 'inputs': {'model': ['1', 0], 'clip': ['1', 1],
            'lora_name': lora['file'], 'strength_model': lora['weight'], 'strength_clip': lora['weight']}},
        '30': {'class_type': 'LocalDrawGpuHooks', 'inputs': {'model': ['32', 0]}},
        '31': {'class_type': 'LocalDrawGpuClip', 'inputs': {'clip': ['32', 1]}},
        '34': {'class_type': 'CLIPTextEncode', 'inputs': {'clip': ['31', 0], 'text': positive}},
        '35': {'class_type': 'CLIPTextEncode', 'inputs': {'clip': ['31', 0], 'text': negative}},
        '10': {'class_type': 'LoadImage', 'inputs': {'image': prefix + '/fooocus-crop.png'}},
        '11': {'class_type': 'LoadImage', 'inputs': {'image': prefix + '/fooocus-mask.png'}},
        '12': {'class_type': 'ImageToMask', 'inputs': {'image': ['11', 0], 'channel': 'red'}},
        '13': {'class_type': 'INPAINT_LoadFooocusInpaint', 'inputs': {'head': 'fooocus_inpaint_head.pth', 'patch': 'inpaint_v26.fooocus.patch'}},
        '14': {'class_type': 'LocalDrawGpuFooocusGuard', 'inputs': {'patch': ['13', 0]}},
        '15': {'class_type': 'INPAINT_VAEEncodeInpaintConditioning', 'inputs': {'positive': ['34', 0],
            'negative': ['35', 0], 'vae': ['1', 2], 'pixels': ['10', 0], 'mask': ['12', 0]}},
        '16': {'class_type': 'INPAINT_ApplyFooocusInpaint', 'inputs': {'model': ['30', 0], 'patch': ['14', 0], 'latent': ['15', 2]}},
        '20': {'class_type': 'ControlNetLoader', 'inputs': {'control_net_name': control['name']}},
        '21': {'class_type': 'SetUnionControlNetType', 'inputs': {'control_net': ['20', 0], 'type': 'depth'}},
        '22': {'class_type': 'LoadImage', 'inputs': {'image': prefix + '/fooocus-depth.png'}},
        '23': {'class_type': 'ControlNetApplyAdvanced', 'inputs': {'positive': ['15', 0], 'negative': ['15', 1],
            'control_net': ['21', 0], 'image': ['22', 0], 'strength': .55, 'start_percent': 0., 'end_percent': 1., 'vae': ['1', 2]}},
        '17': {'class_type': 'KSampler', 'inputs': {'model': ['16', 0], 'positive': ['23', 0], 'negative': ['23', 1],
            'latent_image': ['15', 3], 'seed': seed, 'steps': 24, 'cfg': 5., 'sampler_name': 'dpmpp_2m_sde_gpu',
            'scheduler': 'karras', 'denoise': 1.}},
        '18': {'class_type': 'VAEDecode', 'inputs': {'samples': ['17', 0], 'vae': ['1', 2]}},
        '42': {'class_type': 'INPAINT_ColorMatch', 'inputs': {'target': ['18', 0], 'reference': ['10', 0], 'exclude_mask': ['12', 0], 'strength': 1.}},
        '43': {'class_type': 'INPAINT_ShrinkMask', 'inputs': {'mask': ['12', 0], 'shrink': 6, 'blur': 11, 'blur_type': 'gaussian'}},
        '44': {'class_type': 'MaskComposite', 'inputs': {'destination': ['12', 0], 'source': ['43', 0], 'x': 0, 'y': 0, 'operation': 'multiply'}},
        '45': {'class_type': 'ImageCompositeMasked', 'inputs': {'destination': ['10', 0], 'source': ['42', 0], 'x': 0, 'y': 0, 'resize_source': False, 'mask': ['44', 0]}},
        '46': {'class_type': 'ImageScale', 'inputs': {'image': ['45', 0], 'upscale_method': 'lanczos', 'width': width, 'height': height, 'crop': 'disabled'}},
        '47': {'class_type': 'LoadImage', 'inputs': {'image': repair['original']}},
        '48': {'class_type': 'LoadImage', 'inputs': {'image': prefix + '/fooocus-exact-mask.png'}},
        '49': {'class_type': 'ImageToMask', 'inputs': {'image': ['48', 0], 'channel': 'red'}},
        '90': {'class_type': 'ImageCompositeMasked', 'inputs': {'destination': ['47', 0], 'source': ['46', 0],
            'x': crop[0], 'y': crop[1], 'resize_source': False, 'mask': ['49', 0]}},
        '7': {'class_type': 'SaveImage', 'inputs': {'images': ['90', 0], 'filename_prefix': 'comic-studio/hand-repair'}},
    }
    from . import guided_hand
    model,clip=['32',0],['32',1]
    for index,style in enumerate(guided_hand.source_style_loras(repair,cfg)):
        key='hand-source-style-'+str(index)
        graph[key]={'class_type':'LoraLoader','inputs':{'model':model,'clip':clip,
            'lora_name':style['file'],'strength_model':style['weight'],'strength_clip':style['clip_weight']}}
        model,clip=[key,0],[key,1]
    graph['30']['inputs']['model']=model
    graph['31']['inputs']['clip']=clip
    if pose_only:
        pose_control=e.read(ROOT/'config/multi-person-control.json')
        path=model_directory(ROOT) / "controlnet"/pose_control['name']
        with path.open('rb') as stream:
            e.need(hashlib.file_digest(stream,'sha256').hexdigest()==pose_control['sha256'],
                   '既有手指骨架控制模型校验失败')
        with Image.open(ROOT/cfg['depth_file']) as template:
            pose=guided_hand.pose_from_depth(cfg['projected_joints'],repair['placement']['size'],
                                            repair['placement']['placement'],template.width)
        # The sampler sees the resized crop. Project the same complete joints
        # into that crop; global-canvas coordinates would shift the control.
        for person in pose['people']:
            for key,points in person.items():
                if key.endswith('keypoints_2d'):
                    for i in range(0,len(points),3):
                        if points[i+2]>0:
                            points[i]=(points[i]-crop[0])*size[0]/width
                            points[i+1]=(points[i+1]-crop[1])*size[1]/height
        pose.update(canvas_width=size[0],canvas_height=size[1])
        graph['20']['inputs']['control_net_name']=pose_control['name']
        graph.pop('21')
        graph['22']={'class_type':'LocalDrawPublishedPoseRender','inputs':{'pose_json':json.dumps(pose),
            'render_body':False,'render_hand':True,'render_face':False}}
        graph['23']['inputs'].update(control_net=['20',0],strength=.8)
    return graph
