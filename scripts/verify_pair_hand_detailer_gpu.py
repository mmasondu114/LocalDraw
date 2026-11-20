"""Evaluate existing author hand detailer on manually reviewed crossed hands."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from comic_workflow import comfyui, engine as e


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--seed', type=int, default=71008)
    parser.add_argument('--front-hug', action='store_true')
    parser.add_argument('--controlled-hug', action='store_true')
    parser.add_argument('--control', choices=('current', 'union', 'openpose'), default='current')
    parser.add_argument('--hand-only', action='store_true')
    parser.add_argument('--depth-hand', action='store_true')
    parser.add_argument('--pose-hand',action='store_true')
    parser.add_argument('--hand-weight',type=float)
    parser.add_argument('--style-weight',type=float)
    parser.add_argument('--first-denoise',type=float)
    parser.add_argument('--second-denoise',type=float)
    parser.add_argument('--control-strength',type=float)
    parser.add_argument('--depth-reference',type=Path)
    parser.add_argument('--depth-orientation',choices=['original','mirrored'],default='original')
    parser.add_argument('--depth-mirror',action='store_true')
    parser.add_argument('--target-middle',type=float,nargs=2,default=(1049.,603.))
    parser.add_argument('--hand-mask-box',type=int,nargs=4)
    parser.add_argument('--hand-prompt')
    args = parser.parse_args()
    for value in (args.hand_weight,args.style_weight,args.first_denoise,args.second_denoise,args.control_strength):
        e.need(value is None or 0 <= value <= 1,'局部手部试验参数必须在 0 到 1 之间')
    e.need(not args.depth_hand or (args.controlled_hug and args.hand_only and args.control == 'union'), '深度手部试验仅用于单手既有 Union 控制分支')
    e.need(not args.pose_hand or (args.controlled_hug and args.hand_only and not args.depth_hand), '五指骨架对照需要独立的单手提示分支')
    e.need(not (args.front_hug and args.controlled_hug), '手部对照底图不能同时选择两种')
    e.need(not args.hand_mask_box or args.depth_hand, '自选手部遮罩仅用于已核对的深度手部试验')
    e.need(not args.hand_prompt or args.hand_only, '局部手部描述只用于单手提示分支')
    source = ROOT/('data/test-results/native-studio-batch/20261007-234949-400872' if args.controlled_hug else
                  'data/test-results/native-studio-batch/20261007-225319-886988' if args.front_hug else
                  'data/test-results/native-studio-batch/20261007-215611-800990')
    source_graph = e.read(next(source.glob('projects/*/images/*/workflow-1.json')))
    hand = e.read(ROOT/'data/lora-curation/research/hand-lora-author-verified.json')[1]
    e.need(hashlib.sha256((ROOT/hand['file']).read_bytes()).hexdigest() == hand['sha256'], '既有手部 LoRA 校验失败')
    revision = subprocess.check_output(['git', '-C', str(ROOT/'runtime/custom_nodes/comfyui-impact-pack'), 'rev-parse', 'HEAD'], text=True).strip()
    e.need(revision == 'b22aa90cf4a419a8ba9881f90827fbb35dad2612', '作者手部修复版本未核对')
    out = ROOT/'data/test-results/pair-hand-detailer'/e.stamp()
    out.mkdir(parents=True)
    inp = ROOT/'data/comfy-input/pair-hand-detailer'/out.name
    inp.mkdir(parents=True)
    shutil.copyfile(source/'result.png', out/'source.png')
    shutil.copyfile(source/'result.png', inp/'source.png')
    prefix = 'pair-hand-detailer/'+out.name+'/'
    with Image.open(source/'result.png') as image:
        size = image.size
    e.need(size == (1824, 1248), '手部审核区域与底图尺寸不符')
    # These two boxes are visually assigned by the forearm and cuff, never by
    # the old left/right split or the detector's false positives on ornaments.
    regions = [('甘雨', (1052, 327, 1146, 438), '300',
                'black glove, hand resting on a purple clothed shoulder, back of hand, gently bent fingers, relaxed thumb'),
               ('刻晴', (683, 587, 793, 741), '340',
                'purple glove, hand resting against a waist, gently curved fingers, relaxed thumb')]
    if args.front_hug:
        regions = [('甘雨', (928, 540, 1045, 633), '300',
                    'black glove, white sleeve cuff at left, back of hand, hand gently holding an upper arm, '
                    'four gently curved separated fingers pointing left, naturally bent thumb')]
    hand_pose = None
    depth_report = None
    if args.controlled_hug:
        regions = [('甘雨', (994, 544, 1102, 685), '300',
                    'black glove, back of hand, relaxed five fingers, gently touching a waist, '
                    'wrist connected to a blue and white sleeve cuff')]
        if args.hand_only:
            regions[0] = ('甘雨', regions[0][1], '300',
                          'one hand, plain black glove, back of hand, wrist emerging from a blue and white sleeve cuff at the left, '
                          'hand resting on a waist, thumb above four gently bent fingers, relaxed fingers')
        human = e.read(ROOT/'data/test-results/dwpose-hands/20261007-212708-989400/keypoints.json')
        hand_points = list(human['people'][0]['hand_right_keypoints_2d'])
        anchor, middle = hand_points[:2], hand_points[27:29]
        target, target_middle = (1018., 593.), tuple(args.target_middle)
        dx, dy = middle[0]-anchor[0], middle[1]-anchor[1]
        tx, ty = target_middle[0]-target[0], target_middle[1]-target[1]
        scale = math.hypot(tx, ty)/math.hypot(dx, dy)
        angle = math.atan2(ty, tx)-math.atan2(dy, dx)
        cosine, sine = math.cos(angle), math.sin(angle)
        for j in range(0, len(hand_points), 3):
            if hand_points[j+2] > 0:
                x, y = hand_points[j]-anchor[0], hand_points[j+1]-anchor[1]
                hand_points[j] = target[0]+scale*(cosine*x-sine*y)
                hand_points[j+1] = target[1]+scale*(sine*x+cosine*y)
        hand_pose = {'canvas_width':size[0], 'canvas_height':size[1], 'people':[{
            'pose_keypoints_2d':[0]*54, 'hand_right_keypoints_2d':hand_points, 'hand_left_keypoints_2d':[0]*63}]}
        e.save(out/'hand-pose.json', hand_pose)
        if args.depth_hand:
            reference = args.depth_reference.resolve() if args.depth_reference else ROOT/'data/test-results/reference-hand-mesh/20261008-004953-387672'
            depth_report = e.read(reference/'report.json')
            mesh_result = next(r for r in depth_report['results'] if r['label']==args.depth_orientation)
            e.need(mesh_result['valid'],'参考网格没有可用的深度')
            projected = [list(p) for p in mesh_result['projected_joints']]
            depth_file = reference/(args.depth_orientation+'-depth.png')
            with Image.open(depth_file) as pic:
                source_depth = pic.copy()
            if args.depth_mirror:
                source_depth = source_depth.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
                for p in projected:
                    p[0] = source_depth.width-1-p[0]
            ax, ay = projected[0]
            dx, dy = projected[9][0]-ax, projected[9][1]-ay
            scale = math.hypot(tx,ty)/math.hypot(dx,dy)
            angle = math.atan2(ty,tx)-math.atan2(dy,dx)
            c, s = math.cos(angle), math.sin(angle)
            # Inverse affine from full-frame target pixels to the unchanged
            # author mesh depth. This transforms geometry, never photo pixels.
            u, v = c/scale, s/scale
            depth = source_depth.transform(size, Image.Transform.AFFINE,
                (u,v,ax-u*target[0]-v*target[1],-v,u,ay+v*target[0]-u*target[1]),
                resample=Image.Resampling.BILINEAR).convert('RGB')
            depth.save(out/'hand-depth.png')
            depth.save(inp/'hand-depth.png')
            mask_box=tuple(args.hand_mask_box) if args.hand_mask_box else (1002,568,1084,657)
            e.need(0<=mask_box[0]<mask_box[2]<=size[0] and 0<=mask_box[1]<mask_box[3]<=size[1], '手部遮罩超出图像范围')
            regions[0] = ('甘雨', mask_box, '300',
                'one hand, plain black glove, back of hand, wrist emerging from a blue and white sleeve cuff at the left, '
                'hand resting gently on a waist, relaxed gently curved fingers, clearly separated fingers')
    if args.hand_prompt:
        name,box,char_key,_=regions[0]
        regions[0]=(name,box,char_key,args.hand_prompt)
    g = {'1': source_graph['1'],
         '10': {'class_type': 'LoadImage', 'inputs': {'image': prefix+'source.png'}},
         '290': {'class_type': 'LocalDrawGpuHooks', 'inputs': {'model': ['1', 0]}},
         '299': {'class_type': 'LocalDrawGpuClip', 'inputs': {'clip': ['1', 1]}}}
    if hand_pose and (not args.hand_only or args.depth_hand or args.pose_hand):
        control = e.read(ROOT/('config/control-model.json' if args.control == 'union' else
                              'data/lora-curation/research/xinsir-openpose-download.json' if args.control == 'openpose' else
                              'config/multi-person-control.json'))
        e.need(hashlib.sha256((ROOT/'models/controlnet'/control['name']).read_bytes()).hexdigest() == control['sha256'], '既有姿势控制模型文件校验失败')
        g['6'] = {'class_type':'ControlNetLoader', 'inputs':{'control_net_name':control['name']}}
        control_ref = ['6',0]
        if args.control == 'union':
            g['8'] = {'class_type':'SetUnionControlNetType', 'inputs':{'control_net':['6',0], 'type':'depth' if args.depth_hand else 'openpose'}}
            control_ref = ['8',0]
        if args.depth_hand:
            g['7'] = {'class_type':'LoadImage', 'inputs':{'image':prefix+'hand-depth.png'}}
        else:
            g['7'] = {'class_type':'LocalDrawPublishedPoseRender', 'inputs':{
                'pose_json':json.dumps(hand_pose), 'render_body':False, 'render_hand':True, 'render_face':False}}
    branches = []
    for index, (name, box, char_key, description) in enumerate(regions):
        base = 300+40*index
        def node(offset, kind, **inputs):
            key = str(base+offset)
            g[key] = {'class_type': kind, 'inputs': inputs}
            return [key, 0]
        mask = Image.new('RGB', size, 0)
        ImageDraw.Draw(mask).rounded_rectangle(box, radius=8, fill='white')
        filename = f'{index}-mask.png'
        mask.save(out/filename)
        mask.save(inp/filename)
        loaded = node(1, 'LoadImage', image=prefix+filename)
        masked = node(2, 'ImageToMask', image=loaded, channel='red')
        segs = node(3, 'MaskToSEGS', mask=masked, combined=False, crop_factor=3. if args.front_hug or args.controlled_hug else 5., bbox_fill=False, drop_size=10, contour_fill=False)
        if hand_pose and (not args.hand_only or args.depth_hand or args.pose_hand):
            segs = node(12, 'ImpactControlNetApplyAdvancedSEGS', segs=segs, control_net=control_ref, strength=args.control_strength if args.control_strength is not None else .65 if args.depth_hand else .9,
                        start_percent=0., end_percent=1., control_image=['7',0], vae=['1',2])
        style = dict(source_graph['200']['inputs'])
        if args.style_weight is not None:
            style.update(strength_model=args.style_weight,strength_clip=args.style_weight)
        hooks = node(4, 'CreateHookLora', **{k:style[k] for k in ('lora_name','strength_model','strength_clip')})
        hand_weight = args.hand_weight if args.hand_weight is not None else .7 if args.hand_only else .35
        hooks = node(5, 'CreateHookLora', lora_name=hand['diagnostic_relative'], strength_model=hand_weight, strength_clip=hand_weight, prev_hooks=hooks)
        char = source_graph[char_key]['inputs']
        if not args.hand_only:
            hooks = node(6, 'CreateHookLora', lora_name=char['lora_name'], strength_model=.7, strength_clip=0., prev_hooks=hooks)
        clip = node(8, 'SetClipHooks', clip=['299', 0], hooks=hooks, apply_to_conds=True, schedule_clip=False)
        # A hand-only custom prompt avoids asking a local hand patch to create
        # a character portrait. This is the author's per-region prompt method.
        pos = node(10, 'CLIPTextEncode', clip=clip, text='masterpiece, best quality, Japanese anime, soft cel shading, thin lineart, natural hand pose, '+description)
        neg = node(11, 'CLIPTextEncode', clip=clip, text='bad hands, extra fingers, fused fingers, malformed fingers, broken wrist, extra limbs, face, portrait, text, photorealistic'
                   + (', patterned glove, ornate glove, hand jewelry' if args.hand_only else ''))
        branches.append({'name': name, 'segs': segs, 'clip': clip, 'positive': pos, 'negative': neg, 'box': box})
    image = ['10', 0]
    schedule = ((320, .85), (384, .4)) if args.front_hug or args.controlled_hug else ((256, .6), (360, .4))
    if args.hand_only:
        schedule = ((320, .6), (384, .4))
    schedule = tuple((guide, args.first_denoise if stage == 0 and args.first_denoise is not None else
                     args.second_denoise if stage == 1 and args.second_denoise is not None else denoise)
                     for stage,(guide,denoise) in enumerate(schedule))
    for stage, (guide, denoise) in enumerate(schedule, 1):
        for index, branch in enumerate(branches):
            key = str(600+stage*100+index*10)
            g[key] = {'class_type': 'DetailerForEachDebug', 'inputs': {
                'image': image, 'segs': branch['segs'], 'model': ['290', 0], 'clip': branch['clip'], 'vae': ['1', 2],
                'guide_size': guide, 'guide_size_for': True, 'max_size': 768, 'seed': args.seed+stage-1,
                'steps': 20, 'cfg': 5.5, 'sampler_name': 'euler_ancestral', 'scheduler': 'normal',
                'positive': branch['positive'], 'negative': branch['negative'], 'denoise': denoise,
                'feather': 5, 'noise_mask': True, 'force_inpaint': True, 'wildcard': '', 'cycle': 1,
                'inpaint_model': False, 'noise_mask_feather': 0, 'tiled_encode': False, 'tiled_decode': False}}
            image = [key, 0]
            for offset, label, output_index in ((1, 'whole', 0), (2, 'crop-before', 1), (3, 'crop-after', 2)):
                g[str(int(key)+offset)] = {'class_type': 'SaveImage', 'inputs': {'images': [key, output_index],
                    'filename_prefix': f'pair-hand-detailer/{out.name}/{stage}-{index}-{label}'}}
    g['999'] = {'class_type': 'SaveImage', 'inputs': {'images': image, 'filename_prefix': 'pair-hand-detailer/'+out.name+'/result'}}
    e.save(out/'source-provenance.json', {'source_image': str(source/'result.png'), 'source_sha256': hashlib.sha256((source/'result.png').read_bytes()).hexdigest(),
        'tutorial': 'https://github.com/ltdrdata/ComfyUI-extension-tutorials/blob/Main/ComfyUI-Impact-Pack/workflow/dwpose-segs-handfix.png',
        'node_revision': revision, 'existing_hand_lora': hand, 'hand_weight': hand_weight,
        'style_weight':style['strength_model'], 'branches': branches,
        'method': 'Author two-pass local hand Detailer with reviewed manual boxes, hand-only per-region prompts and corresponding character model hooks. Entire people/background were jointly generated before hand repair.',
        'gpu_only': True, 'production_adoption': False, 'acceptance': 'pending_visual_review'})
    if hand_pose and not args.hand_only:
        provenance = e.read(out/'source-provenance.json')
        provenance.update(hand_pose_source='data/test-results/dwpose-hands/20261007-212708-989400/keypoints.json: person0 right hand',
            reference_attribution='Sandro Halank, Wikimedia Commons, CC BY-SA 4.0; source photo and credit recorded in data/lora-curation/research/photo-hug-references.',
            transform='Uniform rotation/scale around locked wrist using source middle MCP and manually reviewed target wrist/palm. Source body joints are not used.',
            control='Published Impact ControlNetApply (SEGS) crops the full control image to the same region as the hand Detailer; all neural inference is GPU-only.')
        provenance['control_model'] = control
        e.save(out/'source-provenance.json', provenance)
    if args.depth_hand:
        provenance = e.read(out/'source-provenance.json')
        provenance.update(control='Existing Xinsir Union depth; original author mesh from reviewed photo hand, uniformly rotated/scaled around wrist and middle MCP. No source photo pixels used.',
                          control_strength=g['312']['inputs']['strength'],
                          depth_file=str(depth_file),depth_orientation=args.depth_orientation,depth_horizontal_mirror=args.depth_mirror,
                          target_wrist=target,target_middle=target_middle,
                          hand_mesh_reference=depth_report, control_model=control,
                          tutorial='https://github.com/wenquanlu/HandRefiner', production_adoption=False)
        e.save(out/'source-provenance.json',provenance)
    if args.pose_hand:
        provenance=e.read(out/'source-provenance.json')
        provenance.update(control='Existing pose ControlNet through author Impact SEGS crop, hand-only prompt without character hooks.',
                          control_model=control,control_strength=g['312']['inputs']['strength'],
                          hand_pose_reference='data/test-results/dwpose-hands/20261007-212708-989400/keypoints.json: visually reviewed right-hand 21 points',
                          source_credit='Sandro Halank, Wikimedia Commons, CC BY-SA 4.0; source metadata retained',
                          production_adoption=False)
        e.save(out/'source-provenance.json',provenance)
    if args.hand_only:
        provenance = e.read(out/'source-provenance.json')
        provenance.update(character_hooks_in_patch=False, hand_only=True,
            control=None, schedule=schedule, method='Author two-pass masked hand Detailer with existing hand/style weights, hand-only prompt and reviewed target region. Character weights are already present in the joint-generated base, but do not compete in the hand patch.')
        e.save(out/'source-provenance.json', provenance)
    manager = comfyui.ComfyManager()
    try:
        manager.start({'impact_hand_trial': True, 'diagnostic_loras_directory': str(ROOT/'data/comfy-lora-diagnostic/hand-author')})
        stats = comfyui.api('/system_stats')
        e.need('--gpu-only' in stats['system']['argv'], '禁止 CPU 模型回退')
        e.save(out/'gpu-system.json', stats)
        available = comfyui.discover()['lora_files']
        for node in g.values():
            if node['class_type'] == 'CreateHookLora':
                key = node['inputs']['lora_name'].replace('\\', '/')
                node['inputs']['lora_name'] = available[key]
        e.save(out/'workflow.json', g)
        job = comfyui.api('/prompt', {'prompt': g})
        e.save(out/'job.json', job)
        e.need(not job.get('node_errors') and job.get('prompt_id'), '作者手部修复节点校验失败')
        deadline = time.monotonic()+600
        while time.monotonic() < deadline:
            history = comfyui.api('/history/'+job['prompt_id']).get(job['prompt_id'])
            if history:
                break
            time.sleep(.5)
        else:
            raise TimeoutError('手部修复未完成；任务编号已保存，不重复提交')
        e.save(out/'history.json', history)
        e.need(history.get('status', {}).get('status_str') == 'success', '作者手部修复失败')
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for key, value in history['outputs'].items():
            for index, desc in enumerate(value.get('images', []), 1):
                with opener.open(comfyui.config()['endpoint']+'/view?'+urllib.parse.urlencode(desc), timeout=30) as response:
                    (out/(f'{key}-{index}.png' if key != '999' else 'result.png')).write_bytes(response.read())
        e.save(out.parent/'latest.json', {'path': str(out), 'acceptance': 'pending_visual_review'})
        print('双人接触处已完成既有手部模型的 GPU 局部验证。', flush=True)
    finally:
        manager.stop()


if __name__ == '__main__':
    main()
