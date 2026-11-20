"""Integrate the author's LoRA Pipeline with native, GPU-only LoRA hooks.

Reference: https://github.com/andreszs/ComfyUI-Lora-Pipeline
"""
import json
import math
import re
import shutil
from .installed_assets import models as model_directory
from . import engine as e
from .paths import ROOT


def install():
    destination = ROOT / 'runtime/custom_nodes/local_draw_hooks'
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / 'scripts/comfy_gpu_hooks_node.py', destination / '__init__.py')
    for name in ('ComfyUI-Lora-Pipeline', 'ComfyUI-OpenPose-Studio', 'ComfyUI-Styler-Pipeline'):
        e.need((ROOT/'runtime/custom_nodes'/name/'__init__.py').exists(),
               '多人绘图组件未安装：'+name)


def apply(graph, passes, cfg, negative, add_triggers=True):
    scene = graph['2']['inputs']['text']
    for role in passes:
        scene = scene.replace(role['positive'], '')
    common_pos, common_neg = graph['5']['inputs']['positive'], graph['5']['inputs']['negative']
    pipeline = None
    clip = graph['2']['inputs']['clip']
    graph['299']={'class_type':'LocalDrawGpuClip','inputs':{'clip':clip}}
    for n, role in enumerate(passes):
        base = 300+n*40
        def node(offset, kind, **inputs):
            key = str(base+offset)
            graph[key] = {'class_type':kind,'inputs':inputs}
            return [key,0]
        hooks = None
        for i, lora in enumerate(role['loras']):
            args = {'lora_name':lora['file'],'strength_model':lora['weight'],
                    'strength_clip':lora['clip_weight']}
            if hooks: args['prev_hooks'] = hooks
            hooks = node(i,'CreateHookLora',**args)
        bound_clip = node(8,'SetClipHooks',clip=['299',0],hooks=hooks,
                          apply_to_conds=True,schedule_clip=False)
        context = re.sub(r'\b(?:[1-9]\s*(?:people|girls?|boys?|others?)|solo)\b', '', scene, flags=re.I)
        features=role['positive']
        if cfg.get('regional_common_cast'):
            context=scene
            features=re.sub(r'\b(?:1girl|1boy|solo)\b','',features,flags=re.I)
        text = features + ', ' + context
        if add_triggers:
            text = ', '.join([l.get('trigger_words','') for l in role['loras']] + [text])
        pos = node(10,'CLIPTextEncode',clip=bound_clip,text=text)
        x1,y1,x2,y2 = role['region']
        args = dict(conditioning=pos, width=x2-x1, height=y2-y1,x=x1,y=y1,
                    strength=cfg.get('regional_strength',.75))
        if pipeline: args['pipeline_in'] = pipeline
        pipeline = node(16,'ConditioningPipelineSetArea',**args)
    graph['297'] = {'class_type':'ConditioningPipelineCombine','inputs':{
        'pipeline':pipeline,'global_positive':common_pos,'global_negative':common_neg,
        'global_strength':cfg.get('regional_global_strength',.3),'fast_mode':False}}
    graph['290'] = {'class_type':'LocalDrawGpuHooks','inputs':{'model':graph['5']['inputs']['model']}}
    graph['5']['inputs'].update(model=['290',0],positive=['297',0],negative=['297',1])
    if cfg.get('regional_style'):
        graph['298'] = {'class_type':'DynamicStylerPipeline','inputs':{
            'positive':['297',0],'negative':['297',1], 'clip':clip,
            'strength':1.0,'redundancy':1,
            'selected_styles_json':json.dumps(cfg['regional_style'])}}
        graph['5']['inputs'].update(positive=['298',0],negative=['298',1])


def apply_pose(graph, pose, cfg):
    """Use the author's OpenPose node and the example's Illustrious ControlNet."""
    control = e.read(ROOT/'config/multi-person-control.json')
    path = model_directory(ROOT) / "controlnet"/control['name']
    e.need(path.is_file() and path.stat().st_size==control['size']
           and not path.with_suffix(path.suffix+'.aria2').exists(),
           '多人姿势控制模型未安装完整')
    graph['280'] = {'class_type':'OpenPoseStudio','inputs':{
        'pose_json':json.dumps(pose),'render_body':True,'render_hand':True,
        'render_face':True,'areas':['297',2]}}
    graph['281'] = {'class_type':'ControlNetLoader','inputs':{'control_net_name':control['name']}}
    graph['282'] = {'class_type':'ControlNetApplyAdvanced','inputs':{
        'positive':graph['5']['inputs']['positive'],'negative':graph['5']['inputs']['negative'],
        'control_net':['281',0],'image':['280',0], 'strength':control['strength'],
        'start_percent':0.0,'end_percent':control['end_percent']}}
    graph['5']['inputs'].update(positive=['282',0],negative=['282',1])


def standing_pose(roles, cfg):
    """Place the author's standing pose into the user's explicit subject areas."""
    # The misc standing preset omits ankles. The hands-on-hips preset has
    # extremely short shins. Use the author's complete OpenPose example.
    template=e.read(ROOT/'runtime/custom_nodes/ComfyUI-OpenPose-Studio/poses/dev/stickman_openpose.json')
    points=template['people'][0]['pose_keypoints_2d']
    e.need(all(points[i*3+2]>0 for i in (10,13)),'站姿模板缺少脚踝，不能用于全身构图')
    for hip,knee,ankle in ((8,9,10),(11,12,13)):
        e.need(all(points[i*3+2]>0 for i in (hip,knee,ankle)), '站姿模板腿部关节不完整')
        thigh=math.dist(points[hip*3:hip*3+2],points[knee*3:knee*3+2])
        shin=math.dist(points[knee*3:knee*3+2],points[ankle*3:ankle*3+2])
        e.need(thigh>0 and .45<=shin/thigh<=1.6,
               '站姿模板腿部比例异常，不能用于全身构图')
    return place_pose(points, roles, cfg)


def seated_pose(roles, cfg):
    """Use the author's unmodified seated template for an explicit seated scene."""
    templates=e.read(ROOT/'runtime/custom_nodes/ComfyUI-OpenPose-Studio/poses/Sitting.json')
    points=templates['sitting back support']['people'][0]['pose_keypoints_2d']
    e.need(all(points[i*3+2]>0 for i in (8,9,10,11,12,13)), '坐姿模板缺少腿部关节')
    return place_pose(points, roles, cfg)


def place_pose(points, roles, cfg):
    """Scale the published skeleton into explicit regions without cropping bodies."""
    valid=[points[i:i+3] for i in range(0,len(points),3) if points[i+2]>0]
    left,top=min(p[0] for p in valid),min(p[1] for p in valid)
    right,bottom=max(p[0] for p in valid),max(p[1] for p in valid)
    result={'canvas_width':cfg['width'],'canvas_height':cfg['height'],'people':[]}
    margin=float(cfg.get('regional_pose_margin',.07))
    e.need(.05<=margin<=.25,'全身姿势边距应在画面高度的 5% 至 25% 之间')
    for role in roles:
        x1,y1,x2,y2=role['region']
        width,height=(x2-x1)*cfg['width'],(y2-y1)*cfg['height']
        scale=min(width*.82/(right-left),height*(1-2*margin)/(bottom-top))
        transformed=[]
        for i in range(0,len(points),3):
            x,y,confidence=points[i:i+3]
            transformed.extend([round((x-(left+right)/2)*scale+(x1+x2)*cfg['width']/2),
                                round((y-top)*scale+y1*cfg['height']+height*margin),confidence]
                               if confidence else [0,0,0])
        result['people'].append({'pose_keypoints_2d':transformed})
    return result
