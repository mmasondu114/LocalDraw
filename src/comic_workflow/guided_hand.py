"""Reviewed hand-depth placement and the published two-pass Impact Detailer.

The user assigns the owner, wrist and middle-finger root. No detector guesses ownership
in an overlapping pair. This replaces only a relaxed hand with its back visible.
"""
import hashlib
import json
import math
import subprocess

from PIL import Image, ImageDraw

from . import engine as e
from .paths import ROOT

RENDER_STYLE_NAMES={'动画截图风格','原神风格','明日方舟风格','彩色线条','立绘风格','线条风格'}


def settings():
    return e.read(ROOT/'config/guided-hand.json')


def preview_reference():
    cfg=settings()
    path=ROOT/cfg['depth_file']
    e.need(hashlib.sha256(path.read_bytes()).hexdigest()==cfg['depth_sha256'],'手部参考校验失败')
    with Image.open(path) as depth:
        return {'width':depth.width,'joints':cfg['projected_joints'],'reference_side':'right'}


def validate_request(value, rect, owners):
    e.need(isinstance(value, dict), '请选择要修正的手')
    e.need(isinstance(value.get('owner'),str) and value['owner'] in owners, '请选择本画格中的角色')
    e.need(value.get('side') in ('right','left'), '请选择左手或右手')
    e.need(value.get('view')=='back' and value.get('pose')=='relaxed', '当前手形参考用于放松、手背朝外的手')
    e.need(value.get('glove') in ('black','purple','bare'), '请选择手套颜色或裸手')
    for name in ('wrist','palm'):
        point=value.get(name)
        e.need(isinstance(point,list) and len(point)==2 and
               all(type(x) in (int,float) and math.isfinite(x) and 0<=x<=1 for x in point),
               '请依次点手腕和中指根部')
        e.need(rect[0]<=point[0]<=rect[2] and rect[1]<=point[1]<=rect[3], '手腕和中指根部需要位于修复框内')
    e.need(value['wrist']!=value['palm'], '手腕与中指根部不能在同一点')
    result={k:value[k] for k in ('owner','side','view','pose','glove','wrist','palm')}
    if 'outline' in value:
        outline=value['outline']
        e.need(isinstance(outline,list) and 3<=len(outline)<=80, '手部描边至少需要三个点')
        for point in outline:
            e.need(isinstance(point,list) and len(point)==2 and
                all(type(x) in (int,float) and math.isfinite(x) for x in point) and
                rect[0]<=point[0]<=rect[2] and rect[1]<=point[1]<=rect[3], '手部描边需要位于红框内')
        area=abs(sum(a[0]*b[1]-a[1]*b[0] for a,b in zip(outline,outline[1:]+outline[:1])))/2
        e.need(area>1e-5, '请描出一块手部区域，不要只画一条线')
        result['outline']=outline
    return result


def edit_mask(size, rect, guidance, *, rounded=False):
    """A drawn hand mask preserves nearby cuffs inside the selection rectangle."""
    mask=Image.new('RGB',size,0)
    draw=ImageDraw.Draw(mask)
    if guidance.get('outline'):
        draw.polygon([(round(x*size[0]),round(y*size[1])) for x,y in guidance['outline']],fill='white')
    else:
        box=tuple(int(rect[i]*size[i%2]) for i in range(4))
        if rounded:
            draw.rounded_rectangle(box,radius=8,fill='white')
        else:
            draw.rectangle(box,fill='white')
    return mask


def prompt(glove, side=None):
    clothing={'black':'plain black glove','purple':'plain purple glove','bare':'bare hand'}[glove]
    e.need(side in (None,'right','left'), '请选择左手或右手')
    handedness=', '+side+' hand' if side else ''
    return 'masterpiece, best quality, Japanese anime, soft cel shading, thin lineart, natural hand pose, one hand'+handedness+', '+clothing+', back of hand, naturally relaxed fingers'


NEGATIVE='bad hands, extra fingers, fused fingers, malformed fingers, broken wrist, extra limbs, face, portrait, text, photorealistic, patterned glove, ornate glove, hand jewelry'


def source_style_loras(repair, cfg):
    """Retain explicitly catalogued rendering styles, without subject hooks."""
    if not cfg.get('retain_source_style_loras',False):
        return []
    from . import lora_names
    from .comfyui import lora_file_key
    result, seen = [], set()
    for item in repair.get('source_loras',[]):
        name=lora_file_key(str(item.get('file','')))
        parts=name.split('/')
        game=parts[1] if parts[0] in ('anime','sdxl') and len(parts)>2 else parts[0]
        if (game not in ('原神','明日方舟') or lora_names.display_name(name) not in RENDER_STYLE_NAMES
                or item.get('character_id') or item.get('target_character_ids') or name in seen):
            continue
        path=(ROOT/'models/loras'/name).resolve()
        e.need(path.is_relative_to((ROOT/'models/loras').resolve()) and path.is_file(), '原图画风模型缺失或路径无效')
        weight=item.get('weight');clip=item.get('clip_weight',weight)
        e.need(all(type(v) in (int,float) and math.isfinite(v) and 0<=v<=2 for v in (weight,clip)),
               '原图画风权重无效')
        result.append({'file':name,'weight':weight,'clip_weight':clip})
        seen.add(name)
    return result


def fit_depth(depth, joints, size, guidance):
    """Uniform scale/rotation around wrist; mirror the same mesh for a left hand."""
    points=[list(p) for p in joints]
    if guidance['side']=='left':
        depth=depth.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        for point in points:
            point[0]=depth.width-1-point[0]
    ax,ay=points[0]
    dx,dy=points[9][0]-ax,points[9][1]-ay
    target=tuple(guidance['wrist'][i]*size[i] for i in (0,1))
    middle=tuple(guidance['palm'][i]*size[i] for i in (0,1))
    tx,ty=middle[0]-target[0],middle[1]-target[1]
    distance=math.hypot(tx,ty)
    e.need(6<=distance<=max(size)*.15, '请把第二点放在中指与手掌连接处，保持原图中的手掌大小')
    e.need(math.hypot(dx,dy)>0, '手部参考缺少有效掌心方向')
    scale=distance/math.hypot(dx,dy)
    angle=math.atan2(ty,tx)-math.atan2(dy,dx)
    c,s=math.cos(angle),math.sin(angle)
    u,v=c/scale,s/scale
    result=depth.transform(size,Image.Transform.AFFINE,
        (u,v,ax-u*target[0]-v*target[1],-v,u,ay+v*target[0]-u*target[1]),
        resample=Image.Resampling.BILINEAR).convert('RGB')
    return result,{'wrist':list(target),'palm':list(middle),'scale':scale,'rotation':angle,
                   'side':guidance['side'],'reference_wrist':points[0],'reference_palm':points[9],
                   'second_anchor':'middle-finger MCP (joint 9); legacy API field palm'}


def prepare(folder, guidance, rect):
    cfg=settings()
    depth_file=ROOT/cfg['depth_file']
    e.need(hashlib.sha256(depth_file.read_bytes()).hexdigest()==cfg['depth_sha256'], '手部参考校验失败')
    with Image.open(folder/'original.png') as original,Image.open(depth_file) as depth:
        size=original.size
        fitted,placement=fit_depth(depth.convert('RGB'),cfg['projected_joints'],size,guidance)
        pose=pose_from_depth(cfg['projected_joints'],size,placement,depth.width)
    box=tuple(round(rect[i]*size[i%2]) for i in range(4))
    bbox=fitted.convert('L').point(lambda v:255 if v>5 else 0).getbbox()
    e.need(bbox is not None and box[0]<=bbox[0] and box[1]<=bbox[1] and bbox[2]<=box[2]+1 and bbox[3]<=box[3]+1,
           '修复框不足以放下这只手，请扩大框或调整掌心位置')
    fitted.save(folder/'hand-depth.png')
    noise_mask=edit_mask(size,rect,guidance,rounded=True)
    points=pose['people'][0]['hand_'+guidance['side']+'_keypoints_2d']
    covered=all(0<=round(points[i*3])<size[0] and 0<=round(points[i*3+1])<size[1] and
                noise_mask.getpixel((round(points[i*3]),round(points[i*3+1])))[0]>0
                for i in range(1,21))
    if guidance.get('outline'):
        e.need(covered,'手部描边没有包住五指参考，请扩大描边或调整手腕与中指根部')
    noise_mask.save(folder/'hand-noise-mask.png')
    record={'owner':guidance['owner'],'placement':placement,'guide_bounds':list(bbox),
            'mask_box':list(box),'mask_scope':'user_outline' if guidance.get('outline') else 'selection_rectangle',
            'all_finger_joints_inside_mask':covered,
            'size':list(size),'reference_sha256':cfg['depth_sha256'],
            'reference_credit':cfg['source_credit'],'method':cfg['method'],
            'detailer_method':cfg['detailer_method'],'gpu_only':True,'scope':'User-assigned relaxed back-of-hand; no automatic ownership inference.'}
    e.save(folder/'hand-guidance.json',record)
    return record


def pose_from_depth(joints, size, placement, reference_width):
    """Project the same reviewed mesh joints; retain its five original fingers."""
    points=[list(point) for point in joints]
    if placement['side']=='left':
        for point in points:
            point[0]=reference_width-1-point[0]
    anchor=points[0]
    c,s=math.cos(placement['rotation']),math.sin(placement['rotation'])
    scale=placement['scale']
    wrist=placement['wrist']
    hand=[]
    for x,y in points:
        dx,dy=x-anchor[0],y-anchor[1]
        hand.extend([wrist[0]+scale*(c*dx-s*dy),wrist[1]+scale*(s*dx+c*dy),1.])
    person={'pose_keypoints_2d':[0.]*54,'face_keypoints_2d':[],
            'hand_left_keypoints_2d':[], 'hand_right_keypoints_2d':[]}
    person['hand_'+placement['side']+'_keypoints_2d']=hand
    return {'canvas_width':size[0],'canvas_height':size[1],'people':[person]}


def workflow(checkpoint, seed, repair):
    cfg=settings()
    e.need(checkpoint=='waiIllustriousSDXL_v170.safetensors', '此手形修复目前使用已验证的日漫默认模型')
    from .comfyui import lora_file_key
    lora={**cfg['hand_lora'], 'file':lora_file_key(cfg['hand_lora']['file'])}
    path=ROOT/'models/loras'/lora['file']
    e.need(path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()==lora['sha256'], '现有手部模型校验失败')
    control=e.read(ROOT/'config/control-model.json')
    path=ROOT/'models/controlnet'/control['name']
    e.need(path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()==control['sha256'], '现有深度控制模型校验失败')
    backend=cfg.get('backend','impact')
    e.need(backend in ('impact','fooocus_depth'), '手部补绘方式无效')
    pose_only=cfg.get('pose_only',False)
    e.need(not pose_only or cfg.get('pose_control',False),
           '单独手指骨架对照需要完整骨架控制')
    if backend=='fooocus_depth':
        from . import fooocus_guided_hand
        return fooocus_guided_hand.workflow(checkpoint,seed,repair,cfg,control,
            prompt(repair['hand_guidance']['glove'],repair['hand_guidance']['side']),NEGATIVE)
    revision=subprocess.check_output(['git','-C',str(ROOT/'runtime/custom_nodes/comfyui-impact-pack'),'rev-parse','HEAD'],text=True).strip()
    e.need(revision==cfg['impact_revision'], '手部修复组件版本未核对')
    hand=repair['hand_guidance']
    depth=repair['depth']
    g={
        '1':{'class_type':'CheckpointLoaderSimple','inputs':{'ckpt_name':checkpoint}},
        '10':{'class_type':'LoadImage','inputs':{'image':repair['original']}},
        '11':{'class_type':'LoadImage','inputs':{'image':repair['mask']}},
        '12':{'class_type':'ImageToMask','inputs':{'image':['11',0],'channel':'red'}},
        '14':{'class_type':'LoadImage','inputs':{'image':repair['noise_mask']}},
        '15':{'class_type':'ImageToMask','inputs':{'image':['14',0],'channel':'red'}},
        '13':{'class_type':'MaskToSEGS','inputs':{'mask':['15',0],'combined':False,'crop_factor':3.,'bbox_fill':False,'drop_size':10,'contour_fill':False}},
        '20':{'class_type':'ControlNetLoader','inputs':{'control_net_name':control['name']}},
        '21':{'class_type':'SetUnionControlNetType','inputs':{'control_net':['20',0],'type':'depth'}},
        '22':{'class_type':'LoadImage','inputs':{'image':depth}},
        '23':{'class_type':'ImpactControlNetApplyAdvancedSEGS','inputs':{'segs':['13',0],'control_net':['21',0],
            'strength':cfg['control_strength'],'start_percent':0.,'end_percent':1.,'control_image':['22',0],'vae':['1',2]}},
        '30':{'class_type':'LocalDrawGpuHooks','inputs':{'model':['1',0]}},
        '31':{'class_type':'LocalDrawGpuClip','inputs':{'clip':['1',1]}},
        '32':{'class_type':'CreateHookLora','inputs':{'lora_name':lora['file'],'strength_model':lora['weight'],'strength_clip':lora['weight']}},
        '33':{'class_type':'SetClipHooks','inputs':{'clip':['31',0],'hooks':['32',0],'apply_to_conds':True,'schedule_clip':False}},
        '34':{'class_type':'CLIPTextEncode','inputs':{'clip':['33',0],'text':prompt(hand['glove'],hand['side'])}},
        '35':{'class_type':'CLIPTextEncode','inputs':{'clip':['33',0],'text':NEGATIVE}},
    }
    previous=None
    for index,style in enumerate(source_style_loras(repair,cfg)):
        key='hand-source-style-'+str(index)
        inputs={'lora_name':style['file'],'strength_model':style['weight'],'strength_clip':style['clip_weight']}
        if previous:
            inputs['prev_hooks']=previous
        g[key]={'class_type':'CreateHookLora','inputs':inputs}
        previous=[key,0]
    if previous:
        g['32']['inputs']['prev_hooks']=previous
    segs=['13',0] if pose_only else ['23',0]
    if pose_only:
        for key in ('20','21','22','23'):
            g.pop(key)
    if cfg.get('pose_control',False):
        # Optional QA path: the published SEGS wrapper recursively applies the
        # previous depth wrapper, so both controls survive both detailer passes.
        pose_control=e.read(ROOT/'config/multi-person-control.json')
        path=ROOT/'models/controlnet'/pose_control['name']
        e.need(path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()==pose_control['sha256'],
               '现有手指骨架控制模型校验失败')
        with Image.open(ROOT/cfg['depth_file']) as template:
            pose=pose_from_depth(cfg['projected_joints'],repair['placement']['size'],
                                 repair['placement']['placement'],template.width)
        g['24']={'class_type':'ControlNetLoader','inputs':{'control_net_name':pose_control['name']}}
        g['25']={'class_type':'LocalDrawPublishedPoseRender','inputs':{'pose_json':json.dumps(pose),
            'render_body':False,'render_hand':True,'render_face':False}}
        g['26']={'class_type':'ImpactControlNetApplyAdvancedSEGS','inputs':{'segs':segs,
            'control_net':['24',0],'control_image':['25',0],'strength':.8,
            'start_percent':0.,'end_percent':1.,'vae':['1',2]}}
        segs=['26',0]
    image=['10',0]
    for index,(key,guide,denoise) in enumerate((('70',320,cfg['first_denoise']),('80',384,cfg['second_denoise']))):
        g[key]={'class_type':'DetailerForEachDebug','inputs':{'image':image,'segs':segs,'model':['30',0],'clip':['33',0],'vae':['1',2],
            'guide_size':guide,'guide_size_for':True,'max_size':768,'seed':seed+index,'steps':cfg['steps'],'cfg':cfg['cfg'],
            'sampler_name':'euler_ancestral','scheduler':'normal','positive':['34',0],'negative':['35',0],'denoise':denoise,
            'feather':5,'noise_mask':True,'force_inpaint':True,'wildcard':'','cycle':1,'inpaint_model':False,
            'noise_mask_feather':0,'tiled_encode':False,'tiled_decode':False}}
        image=[key,0]
    # Impact feathers a few pixels beyond the region. Restore original pixels
    # outside the user's exact selection before saving a new candidate.
    g['90']={'class_type':'ImageCompositeMasked','inputs':{'destination':['10',0],'source':image,'x':0,'y':0,'resize_source':False,'mask':['12',0]}}
    g['7']={'class_type':'SaveImage','inputs':{'images':['90',0],'filename_prefix':'comic-studio/hand-repair'}}
    return g
