"""Run the actual studio local-repair entry, rendering, review and preservation QA."""
import argparse
import copy
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from unittest.mock import patch

from PIL import Image, ImageChops, ImageOps

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from comic_workflow import comfyui, creative, drawing_tools, engine as e, guided_hand, fooocus_guided_hand
from comic_workflow.repository import Repository
from comic_workflow.server import Application
from comic_workflow.characters import CharacterLibrary


class VerifiedApplication(Application):
    def __init__(self,repo,out):
        super().__init__(repo)
        self.out=out

    def prepare_language(self,vision=False,update=lambda _:None):
        log=ROOT/'data/logs/model-server.log'
        offset=log.stat().st_size if log.exists() else 0
        original_popen=subprocess.Popen
        def verbose(argv,**kwargs):
            return original_popen(list(argv)+['--verbose'],**kwargs)
        with patch('comic_workflow.model.subprocess.Popen',side_effect=verbose):
            super().prepare_language(vision=vision,update=update)
        command=e.read(ROOT/'data/logs/model-command.json')
        e.need(command[command.index('--gpu-layers')+1]=='all' and command[command.index('--device')+1]=='CUDA0','CPU neural fallback forbidden')
        with log.open('rb') as stream:
            stream.seek(offset);lines=stream.read().decode('utf-8',errors='replace').splitlines()
        offload=[line for line in lines if re.search(r'offloaded\s+(\d+)/(\d+)\s+layers to GPU',line)]
        e.need(bool(offload),'GPU language layer proof missing')
        match=re.search(r'offloaded\s+(\d+)/(\d+)\s+layers to GPU',offload[-1])
        e.need(match.group(1)==match.group(2),'Not all neural layers are on GPU')
        vision_cuda=[line for line in lines if 'CUDA0' in line and ('clip' in line.lower() or 'vision' in line.lower() or 'mmproj' in line.lower())]
        e.need(not vision or bool(vision_cuda),'GPU vision proof missing')
        e.save(self.out/'review-gpu-proof.json',{'gpu_layers':'all','device':'CUDA0','offload':offload[-1],
            'vision_cuda':vision_cuda,'cpu_model_fallback':False})


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--owner',default='甘雨')
    parser.add_argument('--side',choices=['right','left'],default='right')
    parser.add_argument('--glove',choices=['black','purple','bare'],default='black')
    parser.add_argument('--box',type=int,nargs=4,required=True)
    parser.add_argument('--wrist',type=float,nargs=2,required=True)
    parser.add_argument('--palm',type=float,nargs=2,required=True)
    parser.add_argument('--seed',type=int,default=71008)
    parser.add_argument('--outline',type=Path)
    parser.add_argument('--pose-control',action='store_true')
    parser.add_argument('--pose-only',action='store_true',
                        help='Isolated compatible finger-pose control without generic SDXL depth; existing hand LoRA is retained.')
    parser.add_argument('--retain-source-style',action='store_true',
                        help='Keep only catalogued rendering style LoRAs from the original joint scene, excluding character/action/all-character hooks.')
    parser.add_argument('--backend',choices=['impact','fooocus_depth'])
    parser.add_argument('--reference-guide',type=Path,
                        help='Reviewed cached depth and projected joints for isolated QA; production settings are unchanged.')
    parser.add_argument('--author-denoise',action='store_true',
                        help='Use published Impact handfix 0.6/0.4 denoise, retaining the SDXL sampler and guidance sizes.')
    parser.add_argument('--save-inpaint-stages',action='store_true',
                        help='Capture raw decode, ColorMatch and blend from the same Fooocus sampling run; only the full final image enters studio review.')
    parser.add_argument('--save-impact-stages',action='store_true',
                        help='Capture actual cropped reference and refined crop from each Impact pass; neural settings and final studio review remain unchanged.')
    parser.add_argument('--hand-image-reference',type=Path,
                        help='Isolated IPAdapter image-conditioning comparison from a directly inspected hand reference; no reference pixels are pasted.')
    parser.add_argument('--hand-reference-weight',type=float,default=.5)
    parser.add_argument('--hand-reference-type',choices=['linear','composition'],default='linear')
    args=parser.parse_args()
    e.need(not args.save_inpaint_stages or args.backend=='fooocus_depth',
           '补绘阶段对照需要明确选择既有 Fooocus 后端')
    e.need(not args.save_impact_stages or args.backend=='impact',
           '手部裁剪对照需要明确选择既有 Impact 后端')
    e.need(not args.hand_image_reference or args.backend=='impact',
           '手形图片参考对照需要明确选择既有 Impact 后端')
    e.need(0.<args.hand_reference_weight<=.8,'手形图片参考强度需要在已公开示例范围内')
    source=args.source.resolve()
    out=ROOT/'data/test-results/guided-hand-studio'/e.stamp();out.mkdir(parents=True)
    shutil.copytree(source/'projects',out/'projects')
    if (source/'characters').exists():shutil.copytree(source/'characters',out/'characters')
    repo=Repository(out/'projects')
    pid=next(p.name for p in repo.root.iterdir() if p.is_dir())
    project=repo.get(pid)
    batches=comfyui.drawing_state(repo,pid)['batches']
    batch=next(b for b in batches if b.get('images'))
    image=batch['images'][0]
    original=repo.folder(pid)/'images'/image['file']
    with Image.open(original) as pic:width,height=pic.size
    owner=next(c['id'] for c in project['canon']['characters'] if c['name']==args.owner)
    request={'base_hash':e.digest(project),'image':image['id'],
        'rect':[args.box[i]/(width if i%2==0 else height) for i in range(4)],
        'instruction':'修复所选角色放松搭着的手，保留袖口、人物和背景',
        'hand_guidance':{'owner':owner,'side':args.side,'glove':args.glove,'view':'back','pose':'relaxed',
            'wrist':[args.wrist[0]/width,args.wrist[1]/height],'palm':[args.palm[0]/width,args.palm[1]/height]}}
    if args.outline:
        points=e.read(args.outline)
        request['hand_guidance']['outline']=[[x/width,y/height] for x,y in points]
    e.save(out/'request.json',request)
    repair_settings={**guided_hand.settings(),'pose_control':args.pose_control or args.pose_only,
                     'pose_only':args.pose_only,'retain_source_style_loras':args.retain_source_style}
    if args.reference_guide:
        reference=e.read(args.reference_guide.resolve())
        keys=('depth_file','depth_sha256','projected_joints','source_credit')
        e.need(all(key in reference for key in keys), '手形对照需要已检查的深度、校验值、关节与来源')
        e.need(len(reference['projected_joints'])==21, '手形对照必须保留完整的二十一关节')
        repair_settings.update({key:reference[key] for key in keys})
    if args.backend:
        repair_settings['backend']=args.backend
    if args.author_denoise:
        repair_settings.update(first_denoise=.6,second_denoise=.4)
    hand_reference=None
    if args.hand_image_reference:
        reference_path=args.hand_image_reference.resolve()
        e.need(reference_path.is_relative_to(ROOT) and reference_path.is_file(),'手形图片参考需要可核对的本地工作区来源')
        with Image.open(reference_path) as pic:
            pic.verify()
        digest=hashlib.sha256(reference_path.read_bytes()).hexdigest()
        reference_folder=ROOT/'data/comfy-input/qa-hand-references'
        reference_folder.mkdir(parents=True,exist_ok=True)
        destination=reference_folder/(digest+reference_path.suffix)
        shutil.copyfile(reference_path,destination)
        model_config=e.read(ROOT/'config/reference-models.json')
        for item in model_config['models']:
            model_path=ROOT/item['directory']/item['name']
            e.need(model_path.is_file() and hashlib.sha256(model_path.read_bytes()).hexdigest()==item['sha256'],
                   '既有图片参考模型校验失败')
        hand_reference={'source':reference_path.relative_to(ROOT).as_posix(),'sha256':digest,
            'image':'qa-hand-references/'+destination.name,'weight':args.hand_reference_weight,
            'weight_type':args.hand_reference_type,
            'primary_method':'https://github.com/cubiq/ComfyUI_IPAdapter_plus',
            'new_weights':False,'reference_pixels_pasted':False,'production_adoption':False}
        e.save(out/'hand-image-reference.json',hand_reference)
    e.save(out/'effective-guided-settings.json',repair_settings)
    app=VerifiedApplication(repo,out)
    original_start=app.comfy.start
    def verified_start(*a,**kw):
        result=original_start(*a,**kw)
        stats=comfyui.api('/system_stats')
        e.need(any(d.get('type')=='cuda' for d in stats['devices']),'Drawing GPU proof missing')
        e.save(out/'drawing-gpu-proof.json',stats)
        return result
    app.comfy.start=verified_start
    original_workflow=fooocus_guided_hand.workflow
    original_hand_workflow=guided_hand.workflow
    original_api=comfyui.api
    stages={'raw':['18',0],'color':['42',0],'blend':['45',0]}
    if args.save_impact_stages:
        stages={name+'-'+kind:[key,output] for name,key in (('pass1','70'),('pass2','80'))
                for kind,output in (('source-crop',1),('refined-crop',2),('control-crop',4))}
    captured={}
    def add_stages(graph):
        for name,output in stages.items():
            graph['qa-stage-'+name]={'class_type':'SaveImage','inputs':{
                'images':output,'filename_prefix':'comic-studio/qa-hand-'+name}}
        return graph
    def tapped_workflow(*a,**kw):
        return add_stages(original_workflow(*a,**kw))
    def tapped_hand_workflow(*a,**kw):
        graph=original_hand_workflow(*a,**kw)
        if hand_reference:
            models=model_config['models']
            graph['qa-hand-reference-image']={'class_type':'LoadImage','inputs':{'image':hand_reference['image']}}
            graph['qa-hand-reference-vision']={'class_type':'CLIPVisionLoader','inputs':{'clip_name':models[0]['name']}}
            graph['qa-hand-reference-adapter']={'class_type':'IPAdapterModelLoader','inputs':{'ipadapter_file':models[1]['name']}}
            graph['qa-hand-reference-model']={'class_type':'IPAdapterAdvanced','inputs':{
                'model':graph['70']['inputs']['model'],'ipadapter':['qa-hand-reference-adapter',0],
                'clip_vision':['qa-hand-reference-vision',0],'image':['qa-hand-reference-image',0],
                'weight':hand_reference['weight'],'weight_type':hand_reference['weight_type'],'combine_embeds':'concat',
                'start_at':0.,'end_at':.8,'embeds_scaling':'V only'}}
            for key in ('70','80'):
                graph[key]['inputs']['model']=['qa-hand-reference-model',0]
        return add_stages(graph) if args.save_impact_stages else graph
    def tapped_api(path,*a,**kw):
        result=original_api(path,*a,**kw)
        if path.startswith('/history/'):
            result=copy.deepcopy(result)
            for record in result.values():
                outputs=record.get('outputs',{})
                for name in stages:
                    key='qa-stage-'+name
                    output=outputs.pop(key,None)
                    if output and output.get('images') and name not in captured:
                        e.need(len(output['images'])==1,'阶段对照出现多个区域，需要逐一核对')
                        descriptor=output['images'][0]
                        query=urllib.parse.urlencode(descriptor)
                        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
                        with opener.open(comfyui.config()['endpoint']+'/view?'+query,timeout=30) as response:
                            content=response.read(40_000_001)
                        e.need(len(content)<=40_000_000,'阶段图片过大')
                        filename='stage-'+name+'.png'
                        (out/filename).write_bytes(content)
                        with Image.open(out/filename) as picture:
                            picture.verify()
                        captured[name]={'file':filename,'source_node':stages[name],
                                        'saved_descriptor':descriptor}
                if captured:
                    stage_file='impact-stages.json' if args.save_impact_stages else 'inpaint-stages.json'
                    e.save(out/stage_file,{'same_neural_sampling_run':True,
                        'stages':captured,'diagnostic_outputs_removed_from_studio_history':True})
        return result
    try:
        print('Running actual studio guided hand repair and review on GPU.',flush=True)
        # Override the configured hand recipe and random seed for comparison.
        # local_repair, graph construction, rendering and review code are real.
        with ExitStack() as stack:
            stack.enter_context(patch.object(comfyui.secrets,'randbelow',return_value=args.seed))
            stack.enter_context(patch.object(guided_hand,'settings',return_value=repair_settings))
            stack.enter_context(patch.object(creative,'CharacterLibrary',return_value=app.characters))
            if args.save_inpaint_stages:
                stack.enter_context(patch.object(fooocus_guided_hand,'workflow',side_effect=tapped_workflow))
            if args.save_impact_stages or hand_reference:
                stack.enter_context(patch.object(guided_hand,'workflow',side_effect=tapped_hand_workflow))
            if args.save_inpaint_stages or args.save_impact_stages:
                stack.enter_context(patch.object(comfyui,'api',side_effect=tapped_api))
            result=drawing_tools.local_repair(app,pid,request,lambda _:None,'guided-hand-'+out.name)
        if args.save_inpaint_stages or args.save_impact_stages:
            e.need(set(captured)==set(stages),'阶段对照图片未完整保存')
        e.save(out/'entry-result.json',result)
        repaired=next(b for b in comfyui.drawing_state(repo,pid)['batches'] if b['id']==result['batch'])
        output=repo.folder(pid)/'images'/repaired['images'][0]['file']
        shutil.copyfile(original,out/'source.png');shutil.copyfile(output,out/'result.png')
        with Image.open(original) as a,Image.open(output) as b:
            a=a.convert('RGB');b=b.convert('RGB')
            e.need(a.size==b.size,'Repair output dimensions changed')
            diff=ImageChops.difference(a,b)
            mask=Image.open(ROOT/'data/comfy-input/repairs'/('guided-hand-'+out.name)/'mask.png').convert('L')
            outside=ImageChops.multiply(diff,ImageOps.invert(mask).convert('RGB')).getbbox()
            e.need(outside is None,'Pixels outside the user selection changed')
            proof={'size':list(a.size),'changed_bbox':list(diff.getbbox()),'outside_selection_changed':False}
        e.save(out/'pixel-preservation.json',proof)
        e.save(out/'batch.json',repaired)
        e.save(out/'report.json',{'status':'rendered_reviewed_awaiting_direct_image_inspection','gpu_only':True,
            'source_scene':source.relative_to(ROOT).as_posix(),'entry':'drawing_tools.local_repair',
            'seed_provider_fixed':args.seed,'original_project_untouched':True,'pixel_preservation':proof,
            'model_review':repaired['images'][0]['review'],'whole_goal_complete':False})
        e.save(out.parent/'latest.json',{'path':str(out),'acceptance':'pending_visual_review'})
        print(out.relative_to(ROOT).as_posix(),flush=True)
    finally:
        app.model.stop();app.comfy.stop()


if __name__=='__main__':
    main()
