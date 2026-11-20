"""Run the real studio batch and references in an isolated GPU QA project."""
import base64
import argparse
import copy
import json
from pathlib import Path
import shutil
import sys
import urllib.parse
import urllib.request
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from comic_workflow import comfyui, engine as e, quick_sample, reference_images, lora_inspection
from comic_workflow.characters import CharacterLibrary, DETAILS
from comic_workflow.repository import Repository


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--hires', action='store_true')
    parser.add_argument('--hires-pose-control', action='store_true')
    parser.add_argument('--save-hires-stages',action='store_true',
                        help='Save base, pixel-upscaled and final images from the same sampling run; final studio history remains unchanged.')
    parser.add_argument('--no-hand-detail', action='store_true')
    parser.add_argument('--count', type=int, choices=[2,3,4], default=4)
    parser.add_argument('--seed', type=int, default=71008)
    parser.add_argument('--style-weight', type=float, default=.35)
    parser.add_argument('--sitting', action='store_true')
    parser.add_argument('--production-config', action='store_true')
    parser.add_argument('--scene-share', type=float, default=None)
    parser.add_argument('--action', choices=['hugging', 'mutual_pointing'], default=None)
    parser.add_argument('--interaction-routing', action='store_true')
    parser.add_argument('--interaction-pose', action='store_true')
    parser.add_argument('--no-interaction-pose', action='store_true')
    parser.add_argument('--hug-preset', choices=['face to face hug', 'close embrace back', 'sideways embrace', 'front hug', 'side hug leaning'], default=None)
    parser.add_argument('--aspect-ratio', choices=['3:2', '2:3', '1:1'], default='3:2')
    parser.add_argument('--cast', nargs='+', choices=['甘雨', '刻晴', '琴', '莫娜'], default=None)
    parser.add_argument('--hug-pose', default=None)
    parser.add_argument('--hug-contact', choices=['waist', 'upper_arm'], default='waist',
                        help='Match the observed contact surface of a reviewed custom side-hug reference; isolated QA only.')
    parser.add_argument('--contact-masks', action='store_true')
    parser.add_argument('--arm-mask-padding', type=int, default=0,
                        help='Isolated observed cuff-coverage dilation in base-canvas pixels; keeps original hand footprints.')
    parser.add_argument('--reference-start', type=float, default=None)
    parser.add_argument('--no-reference', action='store_true')
    parser.add_argument('--published-renderer', action='store_true')
    parser.add_argument('--hug-lora', type=float, default=None)
    parser.add_argument('--shared-pose', action='store_true')
    parser.add_argument('--pose-strength', type=float, default=None)
    parser.add_argument('--pose-end', type=float, default=None)
    parser.add_argument('--local-hug-gesture', action='store_true')
    parser.add_argument('--free-hug-arms', action='store_true')
    parser.add_argument('--hand-lora', type=float, default=None)
    parser.add_argument('--hug-hand-conditioning',action='store_true',
                        help='Compare confirmed hand regions using shared existing hand/style hooks without character portrait conditioning.')
    parser.add_argument('--hug-body-reference-masks',action='store_true',
                        help='Isolated comparison: keep character-image attention on the body partition, excluding confirmed hand conditioning regions.')
    parser.add_argument('--shared-contact', action='store_true')
    parser.add_argument('--shot', choices=['full body', 'waist up'], default='full body')
    parser.add_argument('--camera-pose-fit', action='store_true')
    args = parser.parse_args()
    e.need(not args.save_hires_stages or (args.hires and args.no_hand_detail and not args.production_config),
           '高清阶段对照需要隔离的整幅高清流程，并关闭后续手部补绘')
    e.need(not args.hug_body_reference_masks or (args.hug_hand_conditioning and args.save_hires_stages and not args.no_reference),
           '立绘参考避开手形的对照需要已确认手形区域、完整阶段记录及原有立绘参考')
    e.need(args.hug_contact == 'waist' or (args.action == 'hugging' and args.interaction_pose
           and args.hug_preset == 'sideways embrace' and args.hug_pose and not args.production_config),
           '上臂接触对照需要已检查的自定义侧抱姿势，不能用于生产配置验收')
    e.need(type(args.arm_mask_padding) is int and 0 <= args.arm_mask_padding <= 64
           and (not args.arm_mask_padding or (args.action == 'hugging' and args.interaction_pose
                and not args.production_config)), '袖口覆盖对照需要双人拥抱隔离试验')
    e.need(not args.hires_pose_control or (args.hires and args.interaction_pose and not args.production_config),
           '高清姿势控制目前仅用于开启高清和互动骨架的隔离复测')
    e.need(not (args.shared_contact and args.production_config), '共同接触区域属于隔离试验')
    e.need(args.cast is None or (len(args.cast) == args.count and len(set(args.cast)) == args.count),
           '验证角色必须与人数一致且不能重复')
    e.need(not args.action or (args.count == 2 and not args.sitting),
           '双人互动动作验证需要两个站立角色')
    source = ROOT / 'data/test-results/attention-couple/20261007-094429-736680'
    workflow = e.read(source / 'wai-4-people-71008-workflow.json')
    models = e.read(source / 'model-selection.json')
    refs = e.read(ROOT / 'data/lora-curation/research/qa-official-references.json')
    out = ROOT / 'data/test-results/native-studio-batch' / e.stamp()
    out.mkdir(parents=True)
    repo = Repository(out / 'projects')
    library = CharacterLibrary(out / 'characters')
    labels = copy.deepcopy(comfyui.lora_labels())
    cfg = {**comfyui.config(), 'checkpoint':'waiIllustriousSDXL_v170.safetensors',
           'regional_lora_backend':'native_masked', 'regional_lora_hooks':True,
           'regional_auto_pose':True, 'regional_pose_margin':.18, 'hires_fix':args.hires}
    if args.production_config:
        cfg = comfyui.config()
    e.need(not (args.production_config and args.camera_pose_fit), '半身姿势缩放属于隔离对照')
    if args.camera_pose_fit:
        cfg['regional_camera_pose_fit'] = True
    if args.hires_pose_control:
        cfg['hires_pose_control'] = True
    e.need(not (args.production_config and args.scene_share is not None),
           '实际生产配置验收不能叠加共同场景试验参数')
    if args.scene_share is not None:
        cfg['regional_scene_share'] = args.scene_share
    e.need(not (args.production_config and (args.interaction_routing or args.interaction_pose or args.no_interaction_pose or args.hug_preset or args.hug_pose or args.contact_masks or args.reference_start is not None or args.published_renderer or args.hug_lora is not None or args.shared_pose or args.pose_strength is not None or args.pose_end is not None or args.local_hug_gesture or args.free_hug_arms or args.hand_lora is not None)),
           '实际生产配置验收不能叠加互动试验参数')
    if args.interaction_routing:
        cfg['regional_interactions'] = True
    if args.interaction_pose:
        cfg['regional_interactions'] = True
        cfg['regional_interaction_pose'] = True
    if args.no_interaction_pose:
        e.need(not (args.interaction_pose or args.hug_pose or args.hug_preset or args.contact_masks or args.published_renderer or args.shared_pose),
               '无骨架对照不能同时设置骨架试验选项')
        cfg['regional_interaction_pose'] = False
    if args.hug_preset:
        e.need(args.action == 'hugging' and args.interaction_pose, '拥抱模板比较必须启用双人拥抱姿势试验')
        cfg['regional_hug_preset'] = args.hug_preset
    if args.hug_pose:
        e.need(args.action == 'hugging' and args.interaction_pose, '自定义拥抱姿势仅用于已声明的互动试验')
        cfg['regional_hug_pose'] = args.hug_pose
    if args.free_hug_arms:
        e.need(args.action == 'hugging' and args.interaction_pose and not (args.hug_pose or args.contact_masks),
               '释放手臂对照不能叠加手部关键点或按手臂划分蒙版')
        pose = copy.deepcopy(e.read(ROOT/'runtime/custom_nodes/ComfyUI-OpenPose-Studio/poses/hugging.json')[args.hug_preset or 'face to face hug'])
        for person in pose['people']:
            for joint in (3, 4, 6, 7):
                person['pose_keypoints_2d'][joint*3:joint*3+3] = [0, 0, 0]
            for key in ('hand_left_keypoints_2d', 'hand_right_keypoints_2d'):
                person[key] = [0]*len(person.get(key, [0]*63))
        e.save(out/'body-layout-only-pose.json', pose)
        cfg['regional_hug_pose'] = str(out/'body-layout-only-pose.json')
    if args.contact_masks:
        e.need(args.action == 'hugging' and args.interaction_pose, '接触区域蒙版仅用于双人拥抱试验')
        cfg['regional_contact_masks'] = True
    if args.arm_mask_padding:
        cfg['regional_arm_mask_padding'] = args.arm_mask_padding
    if args.reference_start is not None:
        e.need(args.action == 'hugging', '参考图时间试验仅用于拥抱')
        cfg['regional_hug_reference_start'] = args.reference_start
    if args.published_renderer:
        e.need(args.action == 'hugging' and args.interaction_pose, '作者渲染器仅用于拥抱对照试验')
        cfg['regional_hug_pose_renderer'] = 'published'
    if args.shared_pose:
        e.need(args.action == 'hugging' and args.interaction_pose, '整体姿势对照需要双人拥抱骨架')
        cfg['regional_hug_shared_pose'] = True
    if args.local_hug_gesture:
        e.need(args.action == 'hugging' and args.interaction_pose, '单人拥抱手势对照需要双人拥抱姿势')
        cfg['regional_hug_local_gesture'] = True
    if args.shared_contact:
        e.need(args.action == 'hugging' and args.interaction_pose and not args.contact_masks, '共同接触区域需要双人拥抱隔离试验')
        cfg['regional_hug_shared_contact'] = True
    for value, key in ((args.pose_strength, 'regional_pose_strength'), (args.pose_end, 'regional_pose_end')):
        if value is not None:
            e.need(args.action == 'hugging' and args.interaction_pose and 0. < value <= 1., '姿势控制对照需要有效的拥抱试验参数')
            cfg[key] = value
    action_lora = None
    if args.hug_lora is not None:
        import hashlib
        e.need(args.action == 'hugging' and 0. < args.hug_lora <= 1., '动作 LoRA 仅用于拥抱隔离试验')
        action_lora = e.read(ROOT/'data/lora-curation/research/hug-action-candidates/chosen.json')
        path = ROOT/action_lora['file']
        e.need(hashlib.sha256(path.read_bytes()).hexdigest() == action_lora['sha256'], '动作 LoRA 校验失败')
        cfg['diagnostic_loras_directory'] = str(ROOT/'data/comfy-lora-diagnostic/hug-action')
    hand_lora = None
    if args.hand_lora is not None:
        import hashlib
        e.need(args.action == 'hugging' and 0. < args.hand_lora <= .5, '现有手部模型整图对照需要有效的拥抱试验参数')
        hand_lora = e.read(ROOT/'data/lora-curation/research/hand-lora-author-verified.json')[1]
        e.need(hand_lora['base'] == 'Illustrious' and hashlib.sha256((ROOT/hand_lora['file']).read_bytes()).hexdigest() == hand_lora['sha256'], '既有手部权重必须通过作者底模和文件校验')
        hand_lora['relative_path'] = 'hand-author/'+hand_lora['diagnostic_relative']
        cfg['diagnostic_loras_directory'] = str(ROOT/'data/comfy-lora-diagnostic')
        if action_lora:
            action_lora['relative_path'] = 'hug-action/'+action_lora['relative_path']
    if args.hug_hand_conditioning:
        e.need(args.action=='hugging' and args.interaction_pose and args.hand_lora is not None
               and not (args.production_config or args.shared_contact),'手形区域对照需要隔离的拥抱参考及现有手部模型')
        cfg['regional_hug_hand_conditioning']=True
    use_hand_detail = not args.no_hand_detail and cfg.get('hand_detail', False) if args.production_config else not args.no_hand_detail
    scene = f'{args.count}girls, full body, ' + ('sitting side by side on a park bench' if args.sitting else 'standing side by side') + ', outdoors, park, daylight, Japanese anime, soft cel shading, thin lineart, masterpiece, best quality, amazing quality'
    if args.action:
        action = ('standing face to face, hugging each other, mutual embrace, '
                  'arms wrapped around each other, bodies touching' if args.action == 'hugging' else
                  'standing facing each other, pointing at each other, each extending one index finger '
                  'toward the other person, the other hand relaxed')
        if args.action == 'hugging' and args.hug_preset == 'close embrace back':
            action = 'hug from behind, one person standing behind the other, arms wrapped around waist, bodies touching'
        elif args.action == 'hugging' and args.hug_preset == 'sideways embrace':
            # The author's three visible wrists are at waist height. A shoulder
            # hug prompt contradicts this template and can add another hand.
            action = "standing side by side, mutual side hug, arms wrapped around each other's waist, bodies touching"
            if args.hug_contact == 'upper_arm':
                action = ("standing side by side, mutual side hug, arms wrapped around each other, "
                          "one hand resting gently on the other person's upper arm, bodies touching")
        elif args.action == 'hugging' and args.hug_preset == 'side hug leaning':
            action = 'standing side by side, side hug, leaning together, arm around shoulder, bodies touching'
        scene = f'2girls, {args.shot}, {action}, outdoors, park, daylight, Japanese anime, soft cel shading, thin lineart, masterpiece, best quality, amazing quality'
    if action_lora:
        scene += ', ' + ', '.join(action_lora['triggers'])
    negative = 'worst quality, low quality, bad anatomy, bad hands, extra limbs, extra people, duplicate person, collage, split screen, thick outlines, western comics, text, watermark'
    loras = [{'file':'原神/动画截图风格_Eufoniuzs_Anime_Screencap_illustrious_3-000010.safetensors',
              'weight':args.style_weight, 'character_id':''}] if args.style_weight else []
    if action_lora:
        loras.append({'file':action_lora['relative_path'], 'weight':args.hug_lora, 'character_id':''})
    if hand_lora:
        loras.append({'file':hand_lora['relative_path'], 'weight':args.hand_lora, 'character_id':''})
    characters = []
    source_names = ('甘雨', '刻晴', '琴', '莫娜')
    for index, name in enumerate(args.cast or source_names[:args.count]):
        source_index = source_names.index(name)
        ref = refs[source_index]
        appearance = workflow[str(310 + 40*source_index)]['inputs']['text']
        # Remove the scene appended by the graph; retain the tested identity tags.
        appearance = appearance.split(', , full body,')[0]
        if ', full body,' in appearance:
            appearance = appearance.split(', full body,')[0]
        if name == '甘雨' and 'black gloves' not in appearance:
            # Visible in the unchanged official reference: sleeves are white,
            # gloves are black. The former QA prompt omitted the hand clothing.
            appearance += ', black gloves'
        if args.shared_contact:
            english = {'甘雨':'ganyu', '刻晴':'keqing', '琴':'jean', '莫娜':'mona'}[name]
            appearance = english+' (genshin impact), '+appearance
        profile = {key:'未知；本记录仅用于隔离的绘图流程验证，不补充原作设定。' for key in DETAILS}
        profile.update(identity='隔离测试角色：'+name, clothing=appearance,
                       observed=['仅核对官方半身参考图可见部分'], suggested=[], unknowns=['官方图中未显示的下半身'])
        image = ROOT / 'data/comfy-input' / ref['clip_file']
        uploaded = library.upload('data:image/png;base64,' + base64.b64encode(image.read_bytes()).decode())
        card = library.save(name, profile, {'mode':'image','personality':'未知；流程验证用途'}, uploaded['image_id'])
        path = models[name]['relative_path']
        labels[path] = {**labels.get(path,{}), 'character_id':card['id'], 'character':name}
        loras.append({'file':path, 'weight':.7, 'character_id':card['id'], 'character':name})
        characters.append({'name':name, 'revision':card['revision'], 'prompt':appearance,
                           'region':[index/args.count,0,(index+1)/args.count,1], 'use_reference':not args.no_reference})
    request = {'text':scene, 'negative':negative, 'count':1, 'checkpoint':cfg['checkpoint'],
               'aspect_ratio':args.aspect_ratio, 'characters':characters, 'loras':loras,
               'regional_characters':True, 'use_reference':not args.no_reference,
               'reference_weight':.3, 'hand_detail':use_hand_detail}
    e.save(out / 'source-provenance.json', {
        'workflow_source':source.relative_to(ROOT).as_posix(),
        'primary_workflow':'https://blog.comfy.org/p/masking-and-scheduling-lora-and-model-weights',
        'runtime_config_overrides':{} if args.production_config else {key:cfg[key] for key in ('checkpoint','regional_lora_backend','regional_auto_pose','regional_pose_margin','hires_fix','regional_scene_share')},
        'production_config_used':args.production_config,
        'effective_config':cfg,
        'arguments':vars(args),
        'official_references':refs, 'request':request,
        'scope':'Real quick_sample.create and comfyui.run_batch with isolated cards/projects. Production configuration is used without overrides when explicitly requested; otherwise settings are isolated trial overrides. No production character cards are imported.',
        'gpu_only':True, 'visual_acceptance':'pending',
    })
    manager = comfyui.ComfyManager()
    original_api=comfyui.api
    stages={'base':['6',0],'upscale':['902',0],'hires':['905',0]}
    captured={}
    def tapped_api(path,*a,**kw):
        if path=='/prompt':
            body=copy.deepcopy(a[0])
            graph=body['prompt']
            if args.hug_body_reference_masks:
                changed={}
                for key,node in graph.items():
                    if node['class_type']!='IPAdapterAdvanced':continue
                    for index in range(2):
                        before=['interaction-mask-'+str(index),0]
                        after=['contact-hand-mask-body-'+str(index),0]
                        if node['inputs'].get('attn_mask')==before:
                            e.need(after[0] in graph,'完整身体权重区域缺失')
                            node['inputs']['attn_mask']=after
                            changed[key]={'before':before,'after':after}
                e.need(len(changed)==2,'两位角色的原立绘参考区域需要明确对应')
                e.save(out/'reference-mask-comparison.json',{'changed_nodes':changed,
                    'reference_images_and_weights_retained':True,'pose_coordinates_unchanged':True,
                    'generated_pixel_paste':False,'production_adoption':False})
            for name,output in stages.items():
                e.need(output[0] in graph,'高清阶段节点缺失')
                graph['qa-stage-'+name]={'class_type':'SaveImage','inputs':{
                    'images':output,'filename_prefix':'comic-studio/qa-hires-'+name}}
            e.save(out/'submitted-hires-stage-workflow.json',graph)
            a=(body,*a[1:])
        result=original_api(path,*a,**kw)
        if path.startswith('/history/'):
            result=copy.deepcopy(result)
            for record in result.values():
                outputs=record.get('outputs',{})
                for name in stages:
                    output=outputs.pop('qa-stage-'+name,None)
                    if output and output.get('images') and name not in captured:
                        e.need(len(output['images'])==1,'高清阶段出现多个候选，需要逐一核对')
                        descriptor=output['images'][0]
                        query=urllib.parse.urlencode(descriptor)
                        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
                        with opener.open(cfg['endpoint']+'/view?'+query,timeout=30) as response:
                            content=response.read(40_000_001)
                        e.need(len(content)<=40_000_000,'阶段图片过大')
                        filename='stage-'+name+'.png'
                        (out/filename).write_bytes(content)
                        captured[name]={'file':filename,'source_node':stages[name]}
                if captured:
                    e.save(out/'hires-stages.json',{'same_neural_sampling_run':True,
                        'neural_parameters_unchanged':True,'stages':captured,
                        'diagnostic_outputs_removed_from_studio_history':True})
        return result
    try:
        manager.start({'diagnostic_loras_directory': cfg['diagnostic_loras_directory']} if action_lora or hand_lora else None)
        stats=comfyui.api('/system_stats')
        e.need(any(device.get('type')=='cuda' for device in stats['devices'])
               and '--gpu-only' in stats['system']['argv'],'CPU 神经推理回退被禁止')
        e.save(out / 'gpu-system.json', stats)
        inspections = lora_inspection.inspect_local()
        if action_lora:
            inspections[action_lora['relative_path']] = lora_inspection.inspect_lora(ROOT/action_lora['file'])
        if hand_lora:
            inspections[hand_lora['relative_path']] = lora_inspection.inspect_lora(ROOT/hand_lora['file'])
        with patch.object(comfyui,'config',return_value=cfg), \
             patch.object(comfyui,'lora_labels',return_value=labels), \
             patch.object(lora_inspection,'inspect_local',return_value=inspections), \
             patch.object(reference_images,'CharacterLibrary',return_value=library):
            pid = quick_sample.create(repo, request)['project']
            selected = quick_sample.selected_loras(loras,cfg['checkpoint'])
            if action_lora:
                # Confirm the documented Illustrious/SDXL family only for this
                # isolated run. This is not a quality or production approval.
                e.need(action_lora['base'] == 'Illustrious' and inspections[action_lora['relative_path']]['structurally_compatible'] is True,
                       '作者底模说明和动作权重结构必须一致')
                trial = next(item for item in selected if item['file'] == action_lora['relative_path'])
                trial.update(compatible_checkpoint=cfg['checkpoint'], confirmed_compatibility=True)
            if hand_lora:
                e.need(inspections[hand_lora['relative_path']]['structurally_compatible'] is True, '既有手部权重结构不兼容')
                trial = next(item for item in selected if item['file'] == hand_lora['relative_path'])
                trial.update(compatible_checkpoint=cfg['checkpoint'], confirmed_compatibility=True)
            with patch.object(comfyui,'api',side_effect=tapped_api if args.save_hires_stages else original_api):
                batch = comfyui.run_batch(repo,pid,'P001-K01',lambda event:None,cfg['checkpoint'],1,loras=selected,
                    render_options={'diagnostic':'small_batch','direct_prompt':True,'reference':not args.no_reference,
                                    'reference_weight':.3,'hand_detail':use_hand_detail,'control':False,'seed':args.seed})
        if args.save_hires_stages:
            e.need(set(captured)==set(stages),'高清阶段图片未完整保存')
        e.save(out / 'batch.json', batch)
        image = repo.folder(pid) / 'images' / batch['images'][0]['file']
        shutil.copyfile(image,out / 'result.png')
        base = image.with_name('base-' + image.name)
        if base.exists():
            shutil.copyfile(base,out / 'base.png')
        e.save(ROOT / 'data/test-results/native-studio-batch/latest.json', {'path':str(out),'acceptance':'pending_visual_review'})
        print('完整工作室批次已使用 GPU 执行并保存；等待实图验收',flush=True)
    finally:
        manager.stop()


if __name__ == '__main__':
    main()
