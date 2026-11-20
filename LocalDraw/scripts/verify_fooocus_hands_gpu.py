"""Run the author's pinned SDXL inpaint-refine graph on eight frozen hands."""
import hashlib
import argparse
from pathlib import Path
import shutil
import subprocess
import sys

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from comic_workflow import comfyui, engine as e, hand_detail


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--denoise', type=float, choices=(.7, 1.0), default=.7)
    parser.add_argument('--hand-only', action='store_true')
    parser.add_argument('--regions', type=int, nargs='+', choices=range(1,9), default=list(range(1,9)))
    args = parser.parse_args()
    author = ROOT / 'runtime/custom_nodes/comfyui-inpaint-nodes'
    revision = subprocess.check_output(['git', '-C', str(author), 'rev-parse', 'HEAD'], text=True).strip()
    e.need(revision == '5b7681746fe19e563113d27366ba85236bc1ba5b', '作者局部重绘版本未核对')
    weights = e.read(ROOT / 'data/lora-curation/research/fooocus-inpaint-models-verified.json')
    for item in weights:
        source = ROOT / item['file']
        with source.open('rb') as stream:
            e.need(hashlib.file_digest(stream, 'sha256').hexdigest() == item['sha256'], '作者局部重绘模型完整校验失败')
        target = ROOT / 'models/inpaint' / item['original_name']
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.hardlink_to(source)
        with target.open('rb') as stream:
            e.need(hashlib.file_digest(stream, 'sha256').hexdigest() == item['sha256'], '局部重绘加载文件完整校验失败')
    source = ROOT / 'data/test-results/published-hires/20261007-102547-732245/result.png'
    found = e.read(ROOT / 'data/test-results/hand-detection-resolution/20261007-104419-563445/results.json')[1]
    e.need(found['image'] == source.relative_to(ROOT).as_posix() and found['device'] == 'cuda:0', '检测输入未核对')
    with Image.open(source) as pic:
        image = pic.convert('RGB')
    source_graph = e.read(source.parent / 'workflow.json')
    roles = e.read(ROOT / 'data/test-results/native-studio-batch/20261007-095103-898360/source-provenance.json')['request']['characters']
    regions = hand_detail.regions(found['boxes'], image.size, 8)
    e.need(len(regions) == 8, '八手检测未完整核对')
    out = ROOT / 'data/test-results/fooocus-hands' / e.stamp()
    out.mkdir(parents=True)
    shutil.copyfile(source, out / 'source.png')
    inputs = ROOT / 'data/comfy-input/qa-fooocus-hands' / out.name
    inputs.mkdir(parents=True)
    manager = comfyui.ComfyManager()
    log = ROOT / 'data/logs/comfyui.log'
    log_start = log.stat().st_size if log.exists() else 0
    report = {'acceptance':'running', 'source_image':source.relative_to(ROOT).as_posix(), 'model_weights':weights,
        'author_source':'https://github.com/Acly/comfyui-inpaint-nodes', 'revision':revision,
        'author_workflow':'workflows/inpaint-refine.json', 'sampler':'dpmpp_2m_sde_gpu', 'steps':20,
        'cfg':5.0, 'denoise':args.denoise, 'hand_only_prompt':args.hand_only,
        'selected_regions':args.regions, 'passes':[], 'gpu_only':True, 'production_defaults_changed':False,
        'adaptations':['WAI anime checkpoint replaces author photo checkpoint.',
                       'Existing verified character/style LoRAs and source role descriptions retain anime costume context.',
                       'Only GPU-verified hand masks are refined. No character body is cropped and pasted.']}
    try:
        manager.start({'fooocus_hand_trial':True})
        stats = comfyui.api('/system_stats')
        e.need('--gpu-only' in stats['system']['argv'], '禁止回退到 CPU')
        e.save(out / 'gpu-system.json', stats)
        e.need('INPAINT_VAEEncodeInpaintConditioning' in comfyui.api('/object_info'), '作者局部重绘节点未加载')
        available = comfyui.discover()['lora_files']
        for index, region in enumerate(regions):
            if index + 1 not in args.regions:
                continue
            center = (region['boxes'][0][0] + region['boxes'][0][2]) / (2 * image.width)
            role_index = next(i for i,r in enumerate(roles) if r['region'][0] <= center < r['region'][2])
            role = roles[role_index]
            work = out / str(index+1)
            work.mkdir()
            mask, size = hand_detail.prepare(image, region, work, 768)
            for name in ('crop.png', 'mask.png'):
                shutil.copyfile(work/name, inputs/f'{index+1}-{name}')
            prefix = f'qa-fooocus-hands/{out.name}/{index+1}-'
            model = ['1', 0]
            clip = ['1', 1]
            g = {'1':{'class_type':'CheckpointLoaderSimple','inputs':{'ckpt_name':'waiIllustriousSDXL_v170.safetensors'}}}
            for key, hook_key in (('8', '200'), ('9', str(300+role_index*40))):
                data = source_graph[hook_key]['inputs']
                g[key] = {'class_type':'LoraLoader','inputs':{'model':model,'clip':clip,
                    'lora_name':available[data['lora_name'].replace('\\','/')],
                    'strength_model':data['strength_model'],'strength_clip':data['strength_clip']}}
                model, clip = [key, 0], [key, 1]
            positive = role['prompt']+', anime, hand, natural hand pose, five fingers'
            negative = 'bad anatomy, bad hands, fused fingers, extra fingers, missing fingers, photorealistic, blurry'
            if args.hand_only:
                # The inpaint prompt describes the masked subject, rather than
                # asking the empty hand mask to regenerate a whole character.
                clothing = {'甘雨':'black glove, blue and white sleeve cuff',
                            '刻晴':'dark purple glove, pale purple cuff',
                            '琴':'black glove, white cuff, gold cuff trim',
                            '莫娜':'black glove, purple cuff, gold trim'}[role['name']]
                positive = 'anime illustration, close-up of one hand, natural relaxed fingers, five fingers, ' + clothing
                negative += ', full body, person, portrait, face, head, hair, miniature person, doll, extra hand'
            g.update({
                '2':{'class_type':'CLIPTextEncode','inputs':{'clip':clip,'text':positive}},
                '3':{'class_type':'CLIPTextEncode','inputs':{'clip':clip,'text':negative}},
                '10':{'class_type':'LoadImage','inputs':{'image':prefix+'crop.png'}},
                '11':{'class_type':'LoadImage','inputs':{'image':prefix+'mask.png'}},
                '12':{'class_type':'ImageToMask','inputs':{'image':['11',0],'channel':'red'}},
                '13':{'class_type':'INPAINT_LoadFooocusInpaint','inputs':{'head':'fooocus_inpaint_head.pth','patch':'inpaint_v26.fooocus.patch'}},
                '14':{'class_type':'LocalDrawGpuFooocusGuard','inputs':{'patch':['13',0]}},
                '15':{'class_type':'INPAINT_VAEEncodeInpaintConditioning','inputs':{'positive':['2',0],'negative':['3',0],'vae':['1',2],'pixels':['10',0],'mask':['12',0]}},
                '16':{'class_type':'INPAINT_ApplyFooocusInpaint','inputs':{'model':model,'patch':['14',0],'latent':['15',2]}},
                '17':{'class_type':'KSampler','inputs':{'model':['16',0],'positive':['15',0],'negative':['15',1],
                    'latent_image':['15',3],'seed':71008+index,'steps':20,'cfg':5.0,'sampler_name':'dpmpp_2m_sde_gpu','scheduler':'karras','denoise':args.denoise}},
                '18':{'class_type':'VAEDecode','inputs':{'samples':['17',0],'vae':['1',2]}},
                '7':{'class_type':'SaveImage','inputs':{'images':['18',0],'filename_prefix':f'qa-fooocus-hands/{out.name}/hand-{index+1}'}},
            })
            settings = {'timeout_seconds':300}
            state = {'region':index+1, 'role':role['name'], 'size':list(size), 'boxes':region['boxes']}
            report['passes'].append(state)
            def persist():
                e.save(out/'report.json', report)
            result = hand_detail.render(g, work, comfyui.config(), settings, state, persist)
            with Image.open(result) as pic:
                e.need(pic.size == size, '作者局部重绘返回尺寸不一致')
                replacement = pic.convert('RGB').resize(mask.size, Image.Resampling.LANCZOS)
            image.paste(replacement, (region['crop'][0],region['crop'][1]), mask)
            state['completed'] = True
            persist()
            print(f'作者 Fooocus 局部重绘完成：区域 {index+1}，{role["name"]}，GPU', flush=True)
        image.save(out / 'result.png')
        report['acceptance'] = 'pending_visual_review'
        e.save(out/'report.json',report)
        e.save(out.parent/'latest.json',{'path':str(out),'acceptance':'pending_visual_review'})
        print(str(out), flush=True)
    finally:
        manager.stop()
        if log.exists():
            with log.open('rb') as stream:
                stream.seek(log_start)
                (out/'runtime.log').write_bytes(stream.read())


if __name__ == '__main__':
    main()
