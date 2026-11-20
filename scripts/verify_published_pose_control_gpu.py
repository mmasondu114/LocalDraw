"""Compare published ControlNets on a saved real-studio seated workflow.

No pose joints, model kernels or vendor files are changed. The OpenPose
Studio author endpoint selects its documented Xinsir rendering option.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from comic_workflow import comfyui, engine as e


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--control', choices=['current', 'union', 'openpose'], default='union')
    parser.add_argument('--xinsir-renderer', action='store_true')
    parser.add_argument('--seed', type=int, default=71008)
    parser.add_argument('--no-reference', action='store_true')
    parser.add_argument('--shared-pose', action='store_true')
    parser.add_argument('--author-keypoint-radius', action='store_true')
    parser.add_argument('--fullbody-framing', action='store_true')
    parser.add_argument('--feather-mask', type=int, default=0)
    parser.add_argument('--reference-type', choices=['linear', 'weak input', 'style transfer'], default=None)
    parser.add_argument('--white-negative-reference', action='store_true')
    parser.add_argument('--author-negative-noise', action='store_true')
    parser.add_argument('--regional-strength', type=float, default=1.0)
    parser.add_argument('--source-workflow', type=Path)
    args = parser.parse_args()
    source = args.source_workflow or next((ROOT / 'data/test-results/native-studio-batch/20261007-123253-280972/projects').rglob('workflow-1.json'))
    source = source.resolve()
    e.need(source.is_relative_to(ROOT / 'data/test-results'), '对照来源必须是保存的本地验收工作流')
    graph = e.read(source)
    e.need(0 < args.regional_strength <= 1., '角色区域权重必须在0至1之间')
    if args.regional_strength < 1.:
        # Native finalize_default_conds subtracts each masked branch's actual
        # strength from 1.0. A residual gives the neutral shared-scene prompt
        # influence during the SAME full-frame sampling, without another pass.
        for node in graph.values():
            if node['class_type'] == 'ConditioningSetMask':
                node['inputs']['strength'] = args.regional_strength
    control_path = {'union': 'config/control-model.json', 'current': 'config/multi-person-control.json',
                    'openpose': 'data/lora-curation/research/xinsir-openpose-download.json'}[args.control]
    control = e.read(ROOT / control_path)
    model = ROOT / 'models/controlnet' / control['name']
    e.need(model.is_file() and model.stat().st_size == control['size'], '姿势模型未安装完整')
    with model.open('rb') as stream:
        e.need(hashlib.file_digest(stream, 'sha256').hexdigest() == control['sha256'], '姿势模型完整 SHA256 未通过')
    out = ROOT / 'data/test-results/published-pose-control' / e.stamp()
    out.mkdir(parents=True)
    graph['281']['inputs']['control_net_name'] = control['name']
    if args.reference_type:
        for node in graph.values():
            if node['class_type'] == 'IPAdapterAdvanced':
                node['inputs']['weight_type'] = args.reference_type
    if args.white_negative_reference:
        # The author's image_negative input explicitly accepts an image of
        # unwanted composition. Reject the white reference backdrop without
        # modifying the official reference pixels or synthesizing a subject.
        graph['reference-white-background'] = {'class_type': 'EmptyImage', 'inputs': {
            'width': 512, 'height': 512, 'batch_size': 1, 'color': 16777215}}
        for node in graph.values():
            if node['class_type'] == 'IPAdapterAdvanced':
                node['inputs']['image_negative'] = ['reference-white-background', 0]
    if args.author_negative_noise:
        e.need(not args.white_negative_reference, '负面噪声与白色负面图必须单独比较')
        example = ROOT / 'runtime/custom_nodes/ComfyUI_IPAdapter_plus/examples/ipadapter_noise_injection.json'
        author_noise = next(n for n in e.read(example)['nodes'] if n['type'] == 'IPAdapterNoise')
        recipe = dict(zip(('type', 'strength', 'blur'), author_noise['widgets_values'], strict=True))
        for key, node in list(graph.items()):
            if node['class_type'] == 'IPAdapterAdvanced':
                noise_id = 'reference-author-noise-' + key
                graph[noise_id] = {'class_type': 'IPAdapterNoise', 'inputs': {
                    **recipe, 'image_optional': node['inputs']['image']}}
                node['inputs']['image_negative'] = [noise_id, 0]
    if args.fullbody_framing:
        for node in graph.values():
            if node['class_type'] == 'CLIPTextEncode':
                text = node['inputs']['text']
                if 'worst quality' in text:
                    node['inputs']['text'] = text + ', cropped, cropped feet, cropped head, out of frame, close-up'
                else:
                    node['inputs']['text'] = text + ', full body, head to toe, all feet visible, distant view, wide shot'
    if args.feather_mask:
        e.need(0 < args.feather_mask <= 128, '遮罩羽化范围无效')
        # Native FeatherMask on each local rectangle, before placement on the
        # shared canvas; apply equally to identity and reference masks.
        for key, node in list(graph.items()):
            if node['class_type'] != 'MaskComposite':
                continue
            source_mask = node['inputs']['source']
            original = graph[source_mask[0]]
            if original['class_type'] != 'SolidMask' or original['inputs']['value'] != 1.:
                continue
            feather = 'feather-' + key
            graph[feather] = {'class_type': 'FeatherMask', 'inputs': {
                'mask': source_mask, 'left': args.feather_mask, 'right': args.feather_mask,
                'top': 0, 'bottom': 0}}
            node['inputs']['source'] = [feather, 0]
    if args.no_reference:
        adapters = [n for n in graph.values() if n['class_type'] == 'IPAdapterAdvanced']
        e.need(bool(adapters), '固定流程没有可对照的参考图')
        upstream = next(n['inputs']['model'] for n in adapters if graph[n['inputs']['model'][0]]['class_type'] != 'IPAdapterAdvanced')
        for node in graph.values():
            if node['class_type'] == 'KSampler':
                node['inputs']['model'] = upstream
    if args.control == 'union':
        graph['283'] = {'class_type': 'SetUnionControlNetType', 'inputs': {'control_net': ['281', 0], 'type': 'openpose'}}
        for node in graph.values():
            if node['class_type'] == 'ControlNetApplyAdvanced' and node['inputs']['control_net'] == ['281', 0]:
                node['inputs']['control_net'] = ['283', 0]
    if args.shared_pose:
        pose_nodes = [n for n in graph.values() if n['class_type'] == 'OpenPoseStudio']
        poses = [json.loads(n['inputs']['pose_json']) for n in pose_nodes]
        shared = {**poses[0], 'people': [person for pose in poses for person in pose['people']]}
        graph['284'] = {'class_type': 'OpenPoseStudio', 'inputs': {
            **pose_nodes[0]['inputs'], 'pose_json': json.dumps(shared)}}
        controls = {key: n for key, n in graph.items() if n['class_type'] == 'ControlNetApplyAdvanced'}
        for node in graph.values():
            for field, value in node['inputs'].items():
                if isinstance(value, list) and len(value) == 2 and value[0] in controls:
                    node['inputs'][field] = controls[value[0]]['inputs']['positive' if value[1] == 0 else 'negative']
        for key in controls:
            del graph[key]
        first = next(iter(controls.values()))
        graph['285'] = {'class_type': 'ControlNetApplyAdvanced', 'inputs': {
            **first['inputs'], 'positive': ['297', 0], 'negative': ['297', 1], 'image': ['284', 0]}}
        for node in graph.values():
            if node['class_type'] == 'KSampler':
                node['inputs'].update(positive=['285', 0], negative=['285', 1])
    hints = []
    for key, node in list(graph.items()):
        if node['class_type'] == 'OpenPoseStudio':
            save_key = 'hint-' + key
            graph[save_key] = {'class_type': 'SaveImage', 'inputs': {'images': [key, 0], 'filename_prefix': 'comic-studio/pose-control-hint-' + key}}
            hints.append(save_key)
        elif node['class_type'] == 'KSampler':
            node['inputs']['seed'] = args.seed
        elif node['class_type'] == 'SaveImage':
            node['inputs']['filename_prefix'] = 'comic-studio/pose-control-' + key
    graph['pose-base'] = {'class_type': 'SaveImage', 'inputs': {'images': ['6', 0], 'filename_prefix': 'comic-studio/pose-control-base'}}
    report = {'acceptance': 'pending_visual_review', 'source_workflow': source.relative_to(ROOT).as_posix(),
              'arguments': {key: str(value) if isinstance(value, Path) else value for key, value in vars(args).items()}, 'model': control, 'complete_sha256_verified': True,
              'sources': ['https://huggingface.co/xinsir/controlnet-union-sdxl-1.0',
                          'https://huggingface.co/xinsir/controlnet-openpose-sdxl-1.0',
                          'https://github.com/andreszs/ComfyUI-OpenPose-Studio',
                          'https://github.com/cubiq/ComfyUI_IPAdapter_plus/blob/main/NODES.md',
                          'https://github.com/Comfy-Org/ComfyUI/blob/master/comfy_extras/nodes_mask.py'],
              'gpu_only': True, 'production_defaults_changed': False,
              'scope': 'Only the recorded published ControlNet loader/type, author render setting, requested seed and explicitly requested reference/shared-hint/framing/native-feather ablations differ from the frozen real-studio workflow. Shared hint uses the native ControlNetApplyAdvanced after the existing conditioning combine; author pose joints are unchanged. Framing ablation adds explicit full-body camera/feet terms and the ControlNet author example negative cropped term. Feathering uses native FeatherMask on original explicit region rectangles before MaskComposite, without changing regions or pasting bodies.'}
    if args.author_negative_noise:
        report['author_negative_noise'] = {'source': 'https://github.com/cubiq/ComfyUI_IPAdapter_plus/blob/main/examples/ipadapter_noise_injection.json',
            'local_example_sha256': hashlib.sha256(example.read_bytes()).hexdigest(), 'recipe': recipe,
            'scope': 'Native author noise node into image_negative for every unchanged official positive reference.'}
    if args.regional_strength < 1.:
        report['shared_scene_residual'] = {'source': 'https://blog.comfy.org/p/masking-and-scheduling-lora-and-model-weights',
            'native_method': 'comfy.samplers.finalize_default_conds', 'regional_strength': args.regional_strength,
            'scene_residual_in_nonoverlapping_regions': 1. - args.regional_strength,
            'same_full_frame_sampling': True, 'background_after_people': False,
            'character_bodies_cropped_or_pasted': False}
    e.save(out / 'report.json', report)
    e.save(out / 'workflow.json', graph)
    manager = comfyui.ComfyManager()
    try:
        manager.start()
        if args.xinsir_renderer:
            settings = {'comfyui_openpose_editor.renderer.xinsir_compatibility': True}
            if args.author_keypoint_radius:
                # The author's published drawing example uses radius 4*ratio;
                # these unchanged 1216x832 hints fall in the 1000..1999 bucket.
                settings['comfyui_openpose_editor.renderer.body.keypoint_radius'] = 12
            response = comfyui.api('/openpose/render_style', settings)
            e.need(response.get('ok'), '作者姿势渲染设置未接受')
            report['author_render_endpoint_response'] = response
        e.save(out / 'gpu-system.json', comfyui.api('/system_stats'))
        job = comfyui.api('/prompt', {'prompt': graph})
        e.save(out / 'job.json', job)
        e.need(not job.get('node_errors'), '公开姿势节点不接受工作流')
        started = time.monotonic()
        history = None
        while time.monotonic() - started < 900:
            history = comfyui.api('/history/' + job['prompt_id']).get(job['prompt_id'])
            if history:
                break
            time.sleep(.5)
        e.save(out / 'history.json', history)
        e.need(history and history.get('status', {}).get('status_str') == 'success', '姿势模型 GPU 执行失败；禁止 CPU 回退')
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        outputs = [('7', 'result.png'), ('pose-base', 'base.png')] + [(key, key + '.png') for key in hints]
        if '906' in graph:
            outputs.append(('906', 'hires.png'))
        if '907' in graph:
            outputs.append(('907', 'base.png'))
        for node, name in outputs:
            descriptor = history['outputs'][node]['images'][0]
            with opener.open(comfyui.config()['endpoint'] + '/view?' + urllib.parse.urlencode(descriptor), timeout=30) as response:
                (out / name).write_bytes(response.read())
        report['execution_seconds'] = time.monotonic() - started
        e.save(out / 'report.json', report)
        e.save(ROOT / 'data/test-results/published-pose-control/latest.json', {'path': str(out), 'acceptance': 'pending_visual_review'})
        print('公开姿势模型对照已使用 GPU 完成：' + str(out), flush=True)
    except Exception as error:
        report.update(acceptance='runtime_error_no_accepted_image', error=str(error),
                      process_exit_code=manager.process.poll() if manager.process else None)
        e.save(out / 'report.json', report)
        raise
    finally:
        manager.stop()
        (out / 'runtime.log').write_text((ROOT / 'data/logs/comfyui.log').read_text(encoding='utf-8', errors='replace')[-25000:], encoding='utf-8')


if __name__ == '__main__':
    main()
