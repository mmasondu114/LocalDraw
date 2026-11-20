"""Assemble ComfyUI's published full-canvas masked LoRA conditioning.

https://blog.comfy.org/p/masking-and-scheduling-lora-and-model-weights
The pose branches use the existing author's OpenPose templates.
"""
import json
import re

from . import engine as e, interactions, interaction_masks, regional_lora, regional_prompt
from .framing import explicit_full_body, explicit_upper_body
from .paths import ROOT


def apply(graph, passes, cfg, negative, add_triggers=True, characters=None, global_loras=None):
    owned = {role['name']: role for role in passes}
    roles = characters or [dict(name=r['name'], prompt=r['positive'], region=r['region']) for r in passes]
    regional_prompt.validate(roles)
    scene = graph['2']['inputs']['text']
    for role in passes:
        scene = scene.replace(role['positive'], '')
    context = re.sub(r'\b(?:[1-9]\s*(?:people|girls?|boys?|others?)|solo|side\s+by\s+side)\b', '', scene, flags=re.I)
    interaction = interactions.kind(scene, len(roles)) if cfg.get('regional_interactions') else None
    shared_scene = scene if interaction else context
    if interaction:
        context = interactions.regional_context(context)
    if interaction == 'hugging' and cfg.get('regional_hug_shared_contact'):
        ordered = sorted(roles, key=lambda r: sum(r['region'][::2]))
        descriptions = [re.sub(r'\b1girl\b', '', r['prompt'], flags=re.I) for r in ordered]
        shared_scene += ', left girl: ' + descriptions[0] + ', right girl: ' + descriptions[1]
    # Keep global style in every hook chain. Static global weights plus local
    # hook backups can retain a second checkpoint and exceed GPU memory.
    for index in range(len(global_loras or [])):
        graph.pop(str(8 + index), None)
    graph['299'] = {'class_type': 'LocalDrawGpuClip', 'inputs': {'clip': ['1', 1]}}
    graph['295'] = {'class_type': 'SolidMask', 'inputs': {'value': 0., 'width': cfg['width'], 'height': cfg['height']}}
    global_hooks = None
    for index, lora in enumerate(global_loras or []):
        inputs = {'lora_name': lora['file'], 'strength_model': lora['weight'], 'strength_clip': lora['clip_weight']}
        if global_hooks:
            inputs['prev_hooks'] = global_hooks
        key = str(200 + index)
        graph[key] = {'class_type': 'CreateHookLora', 'inputs': inputs}
        global_hooks = [key, 0]
    clip = ['299', 0]
    if global_hooks:
        graph['296'] = {'class_type': 'SetClipHooks', 'inputs': {
            'clip': clip, 'hooks': global_hooks, 'apply_to_conds': True, 'schedule_clip': False}}
        clip = ['296', 0]
    graph['2']['inputs'].update(clip=clip, text=shared_scene)
    graph['3']['inputs']['clip'] = clip
    combined_text = scene + ', ' + ', '.join(role['prompt'] for role in roles)
    full_body = explicit_full_body(scene)
    standing = (not interaction and cfg.get('regional_auto_pose') and full_body
                and re.search(r'\bstanding\b', scene, re.I)
                and not re.search(r'\b(sitting|seated|kneeling|crouching|running|jumping)\b', combined_text, re.I))
    seated = (not interaction and cfg.get('regional_auto_pose') and full_body
              and re.search(r'\b(sitting|seated)\b', scene, re.I)
              and not re.search(r'\b(standing|kneeling|crouching|running|jumping|crossed legs)\b', combined_text, re.I))
    use_pose = standing or seated
    coupled_pose = bool(interaction and cfg.get('regional_interaction_pose'))
    pose_renderer = ('LocalDrawPublishedPoseRender'
                     if coupled_pose and interaction == 'hugging' and cfg.get('regional_hug_pose_renderer') == 'published'
                     else 'OpenPoseStudio')
    scene_share = cfg.get('regional_scene_share', 0.)
    if interaction == 'hugging':
        scene_share = cfg.get('regional_hug_scene_share', scene_share)
    maximum_share = .8 if interaction == 'hugging' else .5
    e.need(type(scene_share) in (int, float) and 0. <= scene_share <= maximum_share,
           '共同场景权重超出允许范围')
    # The native default-conditioning combiner receives the remaining weight.
    # Leave its neutral scene branch active during joint sampling, instead of
    # fully covering it with character branches. The reviewed seated recipe
    # and other framings retain their existing conditioning weights.
    e.need(not interaction or scene_share > 0., '双人互动需要有效的共同场景权重')
    scene_share = scene_share if interaction or (standing and len(roles) >= 3) else 0.
    if interaction and full_body:
        context += ', head to toe, all feet visible'
        graph['2']['inputs']['text'] += ', head to toe, all feet visible'
        graph['3']['inputs']['text'] += ', cropped, cropped feet, cropped head, out of frame'
    if seated and len(roles) == 2:
        # Native camera terms and the published ControlNet author's cropped
        # exclusion keep both complete seated skeletons inside the requested shot.
        # The controlled standing scenes do not receive this seated-only recipe.
        context += ', full body, head to toe, all feet visible, distant view, wide shot'
        graph['2']['inputs']['text'] = context
        graph['3']['inputs']['text'] += ', cropped, cropped feet, cropped head, out of frame, close-up'
    if use_pose or coupled_pose:
        control = e.read(ROOT / 'config/multi-person-control.json')
        path = ROOT / 'models/controlnet' / control['name']
        e.need(path.is_file() and path.stat().st_size == control['size']
               and not path.with_suffix(path.suffix + '.aria2').exists(), '多人姿势控制模型未安装完整')
        pose = (interactions.published_pose(interaction, roles, cfg,
                    upper_body=bool(cfg.get('regional_camera_pose_fit')) and explicit_upper_body(scene)) if coupled_pose else
                regional_lora.standing_pose(roles, cfg) if standing else regional_lora.seated_pose(roles, cfg))
        if coupled_pose and interaction != 'hugging':
            pose_strength, pose_end = control['strength'], control['end_percent']
        else:
            pose_strength = cfg.get('regional_pose_strength', 1.)
            pose_end = cfg.get('regional_pose_end', 1.)
        graph['281'] = {'class_type': 'ControlNetLoader', 'inputs': {'control_net_name': control['name']}}
    contact_masks = (interaction_masks.graph_masks(graph, roles, pose, cfg)
                     if coupled_pose and interaction == 'hugging' and (cfg.get('regional_contact_masks') or cfg.get('regional_hug_shared_contact')) else {})
    hand_bodies,hand_regions={},[]
    if coupled_pose and interaction=='hugging' and cfg.get('regional_hug_hand_conditioning'):
        from . import lora_names
        e.need(cfg.get('regional_contact_masks') and not cfg.get('regional_hug_shared_contact'),
               '手形区域需要已确认的角色归属')
        e.need(any(lora_names.display_name(item['file'])=='手部修复' for item in (global_loras or [])),
               '请启用现有手部模型')
        hand_bodies,hand_regions=interaction_masks.graph_hand_masks(graph,roles,pose,cfg)
    positive = negative_cond = None
    for index, role in enumerate(roles):
        base = 300 + index * 40
        def node(offset, kind, **inputs):
            key = str(base + offset)
            graph[key] = {'class_type': kind, 'inputs': inputs}
            return [key, 0]
        local = owned.get(role['name'])
        hooks = global_hooks
        for offset, lora in enumerate(local['loras'] if local else []):
            inputs = {'lora_name': lora['file'], 'strength_model': lora['weight'], 'strength_clip': lora['clip_weight']}
            if hooks:
                inputs['prev_hooks'] = hooks
            hooks = node(offset, 'CreateHookLora', **inputs)
        bound_clip = node(8, 'SetClipHooks', clip=['299', 0], hooks=hooks,
                          apply_to_conds=True, schedule_clip=False) if hooks else ['299', 0]
        text = role['prompt']
        if add_triggers and local:
            text = ', '.join([lora.get('trigger_words', '') for lora in local['loras']] + [text])
        gesture = interactions.local_action(interaction, role, roles)
        if interaction == 'hugging' and cfg.get('regional_hug_local_gesture'):
            gesture = ', hugging, bent arms, relaxed gloved hands'
        pos = node(10, 'CLIPTextEncode', clip=bound_clip,
                   text=text + gesture + ', ' + context)
        neg = node(11, 'CLIPTextEncode', clip=bound_clip, text=graph['3']['inputs']['text'])
        x1, y1, x2, y2 = role['region']
        left, top = round(x1 * cfg['width']), round(y1 * cfg['height'])
        right, bottom = round(x2 * cfg['width']), round(y2 * cfg['height'])
        solid = node(12, 'SolidMask', value=1., width=max(1, right-left), height=max(1, bottom-top))
        mask = node(13, 'MaskComposite', destination=['295', 0], source=solid, x=left, y=top, operation='add')
        mask = contact_masks.get(role['name'], mask)
        mask = hand_bodies.get(role['name'],mask)
        # Keep the whole latent visible to each branch; masks select its
        # contribution. Cropping the conditioning area changes scene scale.
        pos = node(14, 'ConditioningSetMask', conditioning=pos, mask=mask, strength=1. - scene_share, set_cond_area='default')
        neg = node(15, 'ConditioningSetMask', conditioning=neg, mask=mask, strength=1. - scene_share, set_cond_area='default')
        if use_pose or coupled_pose:
            hint_pose = (pose if coupled_pose and interaction == 'hugging' and cfg.get('regional_hug_shared_pose')
                         else {**pose, 'people': [pose['people'][index]]})
            hint = node(20, pose_renderer, pose_json=json.dumps(hint_pose),
                        render_body=True, render_hand=True, render_face=True)
            pos = node(21, 'ControlNetApplyAdvanced', positive=pos, negative=neg, control_net=['281', 0],
                       image=hint, strength=pose_strength, start_percent=0., end_percent=pose_end)
            neg = [pos[0], 1]
        positive = node(17, 'ConditioningCombine', conditioning_1=positive, conditioning_2=pos) if positive else pos
        negative_cond = node(18, 'ConditioningCombine', conditioning_1=negative_cond, conditioning_2=neg) if negative_cond else neg
    for hand in hand_regions:
        from .guided_hand import NEGATIVE as hand_negative
        owner_index,side=hand['owner_index'],hand['side']
        prefix='contact-hand-'+str(owner_index)+'-'+side
        clothing=re.search(r'\b(black|purple|white|blue|red|brown) gloves?\b',
                           roles[owner_index]['prompt'].replace('_',' '),re.I)
        text=('masterpiece, best quality, Japanese anime, soft cel shading, thin lineart, '
              'one '+side+' hand, natural hand anatomy, naturally relaxed fingers')
        if clothing:text+=', plain '+clothing.group(1).lower()+' glove'
        for label,description in (('positive',text),('negative',hand_negative)):
            key=prefix+'-'+label
            graph[key]={'class_type':'CLIPTextEncode','inputs':{'clip':clip,'text':description}}
            graph[key+'-mask']={'class_type':'ConditioningSetMask','inputs':{
                'conditioning':[key,0],'mask':hand['mask'],'strength':1.-scene_share,'set_cond_area':'default'}}
        control_key=prefix+'-control'
        graph[control_key]={'class_type':'ControlNetApplyAdvanced','inputs':{
            'positive':[prefix+'-positive-mask',0],'negative':[prefix+'-negative-mask',0],
            'control_net':['281',0],'image':[str(320+owner_index*40),0],
            'strength':pose_strength,'start_percent':0.,'end_percent':pose_end}}
        graph[prefix+'-combine-positive']={'class_type':'ConditioningCombine','inputs':{
            'conditioning_1':positive,'conditioning_2':[control_key,0]}}
        graph[prefix+'-combine-negative']={'class_type':'ConditioningCombine','inputs':{
            'conditioning_1':negative_cond,'conditioning_2':[control_key,1]}}
        positive,negative_cond=[prefix+'-combine-positive',0],[prefix+'-combine-negative',0]
    # The official default node fills only uncovered pixels; it does not
    # duplicate a global subject branch over the already covered regions.
    graph['297'] = {'class_type': 'PairConditioningSetDefaultCombine', 'inputs': {
        'positive': positive, 'negative': negative_cond,
        'positive_DEFAULT': ['2', 0], 'negative_DEFAULT': ['3', 0]}}
    graph['290'] = {'class_type': 'LocalDrawGpuHooks', 'inputs': {'model': ['1', 0]}}
    graph['5']['inputs'].update(model=['290', 0], positive=['297', 0], negative=['297', 1])
    if coupled_pose:
        graph['280'] = {'class_type': pose_renderer, 'inputs': {
            'pose_json': json.dumps(pose), 'render_body': True, 'render_hand': True, 'render_face': True}}
        graph['283'] = {'class_type': 'ControlNetApplyAdvanced', 'inputs': {
            'positive': ['2', 0], 'negative': ['3', 0], 'control_net': ['281', 0],
            'image': ['280', 0], 'strength': pose_strength, 'start_percent': 0., 'end_percent': pose_end}}
        graph['297']['inputs'].update(positive_DEFAULT=['283', 0], negative_DEFAULT=['283', 1])
    graph['290']['_meta'] = {'title': '日漫多人共同画面', 'characters': [r['name'] for r in roles],
                             'pose': 'seated' if seated else 'standing' if standing else None,
                             'scene_share': scene_share, 'interaction': interaction,
                             'coupled_pose': coupled_pose, 'contact_masks': contact_masks,
                             'hand_conditioning':hand_regions}
