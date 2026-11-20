"""Evaluate existing SDXL inpaint patch with the reviewed five-finger depth.

Use the author's inpaint latent for the patch and sample latent for denoising.
All diffusion, text and inpaint neural execution is on GPU.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageChops, ImageOps

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from comic_workflow import comfyui, engine as e, hand_detail, guided_hand


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser()
    parser.add_argument('--seed',type=int,default=71008)
    parser.add_argument('--control-strength',type=float,default=.8)
    parser.add_argument('--hand-weight',type=float,default=.35)
    parser.add_argument('--guided-case',type=Path)
    parser.add_argument('--pose-control',action='store_true')
    parser.add_argument('--pose-only',action='store_true',
                        help='Use the existing anime hand OpenPose control without the generic SDXL depth control.')
    parser.add_argument('--prefill',choices=['none','neutral','telea','blur'],default='none')
    parser.add_argument('--prefill-falloff',type=int,default=11,
                        help='Author pre-process workflow uses an 11-pixel border falloff in the resized crop.')
    parser.add_argument('--outline',type=Path,help='Reviewed hand outline in original image pixels; requires --guided-case.')
    parser.add_argument('--color-match',action='store_true')
    parser.add_argument('--positive',help='Isolated crop-specific prompt; does not change studio defaults.')
    parser.add_argument('--negative',help='Isolated crop-specific exclusions; does not change studio defaults.')
    parser.add_argument('--edge-feather',action='store_true',
                        help='Author Shrink Mask and blur, clipped to the exact user mask, for compositing only.')
    parser.add_argument('--denoise',type=float,default=1.)
    parser.add_argument('--source-image',type=Path,
                        help='Refine an existing candidate with the original reviewed placement and mask.')
    parser.add_argument('--reuse-crop',type=Path,
                        help='Prior crop report, for a comparison that changes only the mask inside the same crop.')
    parser.add_argument('--postprocess-from',type=Path,help='Existing crop to color-match without re-running diffusion.')
    args=parser.parse_args()
    e.need(0<=args.control_strength<=1 and 0<=args.hand_weight<=1,'试验权重无效')
    e.need(0<=args.prefill_falloff<=256,'填充边缘参数无效')
    e.need(0<args.denoise<=1,'局部精修强度无效')
    e.need(not args.outline or args.guided_case,'手部描边需要已核对的工作室来源')
    e.need(not args.pose_only or args.guided_case,'独立五指骨架需要已核对的工作室来源')
    args.pose_control=args.pose_control or args.pose_only
    e.need(not args.postprocess_from or (args.color_match and args.prefill=='none'),'已绘制图片只用于独立颜色校正')
    source=ROOT/'data/test-results/native-studio-batch/20261007-234949-400872'
    guide=ROOT/'data/test-results/pair-hand-detailer/20261008-011730-491708'
    if args.guided_case:
        source=ROOT/e.read(args.guided_case/'report.json')['source_scene']
        guide=ROOT/'data/comfy-input/repairs'/('guided-hand-'+args.guided_case.name)
    out=ROOT/'data/test-results/pair-fooocus-depth'/e.stamp()
    out.mkdir(parents=True)
    inp=ROOT/'data/comfy-input/pair-fooocus-depth'/out.name
    inp.mkdir(parents=True)
    author=ROOT/'runtime/custom_nodes/comfyui-inpaint-nodes'
    revision=subprocess.check_output(['git','-C',str(author),'rev-parse','HEAD'],text=True).strip()
    e.need(revision=='5b7681746fe19e563113d27366ba85236bc1ba5b','作者局部重绘版本未核对')
    weights=e.read(ROOT/'data/lora-curation/research/fooocus-inpaint-models-verified.json')
    for item in weights:
        path=ROOT/item['file']
        with path.open('rb') as stream:
            e.need(hashlib.file_digest(stream,'sha256').hexdigest()==item['sha256'],'既有重绘模型校验失败')
        target=ROOT/'models/inpaint'/item['original_name']
        e.need(target.is_file(),'既有重绘加载文件不存在')
        with target.open('rb') as stream:
            e.need(hashlib.file_digest(stream,'sha256').hexdigest()==item['sha256'],'重绘加载文件校验失败')
    hand=e.read(ROOT/'data/lora-curation/research/hand-lora-author-verified.json')[1]
    with (ROOT/hand['file']).open('rb') as stream:
        e.need(hashlib.file_digest(stream,'sha256').hexdigest()==hand['sha256'],'既有手部 LoRA 校验失败')
    control=e.read(ROOT/'config/control-model.json')
    with (ROOT/'models/controlnet'/control['name']).open('rb') as stream:
        e.need(hashlib.file_digest(stream,'sha256').hexdigest()==control['sha256'],'既有深度控制模型校验失败')
    source_image=args.source_image.resolve() if args.source_image else source/'result.png'
    image=Image.open(source_image).convert('RGB')
    with Image.open(source/'result.png') as original:
        e.need(image.size==original.size,'精修来源画幅不一致')
    crop=(912,478,1174,758)
    region=(1002,568,1084,657)
    size=(768,832)
    mask=Image.new('L',image.size,0)
    ImageDraw.Draw(mask).rounded_rectangle(region,radius=6,fill=255)
    if args.guided_case:
        case_request=e.read(args.guided_case/'request.json')
        with Image.open(guide/'mask.png') as selected:mask=selected.convert('L')
        if args.outline:
            points=e.read(args.outline)
            case_request['hand_guidance']['outline']=[[x/image.width,y/image.height] for x,y in points]
            guidance=guided_hand.validate_request(case_request['hand_guidance'],case_request['rect'],
                                                 {case_request['hand_guidance']['owner']})
            case_request['hand_guidance']=guidance
            image.save(inp/'original.png')
            guided_hand.prepare(inp,guidance,case_request['rect'])
            guide=inp
            mask=guided_hand.edit_mask(image.size,case_request['rect'],guidance).convert('L')
        e.save(out/'request.json',case_request)
        region=mask.getbbox()
        crop=(max(0,region[0]-100),max(0,region[1]-120),min(image.width,region[2]+100),min(image.height,region[3]+120))
        ratio=(crop[3]-crop[1])/(crop[2]-crop[0]);size=(768,round(768*ratio/64)*64)
    if args.reuse_crop:
        prior=e.read(args.reuse_crop)
        crop=tuple(prior['crop'])
        with Image.open(args.reuse_crop.parent/'crop.png') as previous:size=previous.size
        e.need(0<=crop[0]<crop[2]<=image.width and 0<=crop[1]<crop[3]<=image.height,
               '既有局部画幅越界')
        e.need(crop[0]<=region[0]<region[2]<=crop[2] and crop[1]<=region[1]<region[3]<=crop[3],
               '新的手部描边超出既有局部画幅')
    for name,pic in [('crop',image.crop(crop)),('mask',mask.crop(crop).convert('RGB')),
                     ('depth',Image.open(guide/'hand-depth.png').convert('RGB').crop(crop))]:
        pic.resize(size,Image.Resampling.LANCZOS if name=='crop' else Image.Resampling.BILINEAR).save(inp/(name+'.png'))
        shutil.copyfile(inp/(name+'.png'),out/(name+'.png'))
    image.save(out/'source.png')
    source_graph=e.read(next(source.glob('projects/*/images/*/workflow-1.json')))
    prefix='pair-fooocus-depth/'+out.name+'/'
    g={'1':source_graph['1']}
    model,clip=['1',0],['1',1]
    loras=[('9',hand['diagnostic_relative'],args.hand_weight)]
    if not args.guided_case:loras.insert(0,('8',source_graph['200']['inputs']['lora_name'],.35))
    for key,lora,weight in loras:
        g[key]={'class_type':'LoraLoader','inputs':{'model':model,'clip':clip,'lora_name':lora,'strength_model':weight,'strength_clip':weight}}
        model,clip=[key,0],[key,1]
    g.update({
        '290':{'class_type':'LocalDrawGpuHooks','inputs':{'model':model}},
        '299':{'class_type':'LocalDrawGpuClip','inputs':{'clip':clip}},
        '2':{'class_type':'CLIPTextEncode','inputs':{'clip':['299',0],'text':'Japanese anime, soft cel shading, thin lineart, one right hand wearing a plain black glove, back of hand, visible thumb above four gently curved fingers, hand resting on a waist, blue and white sleeve cuff attached to wrist at left'}},
        '3':{'class_type':'CLIPTextEncode','inputs':{'clip':['299',0],'text':'bad hands, malformed fingers, extra fingers, fused fingers, missing thumb, broken wrist, extra hands, face, portrait, person, text, jewelry, ornate glove, photorealistic'}},
        '10':{'class_type':'LoadImage','inputs':{'image':prefix+'crop.png'}},
        '11':{'class_type':'LoadImage','inputs':{'image':prefix+'mask.png'}},
        '12':{'class_type':'ImageToMask','inputs':{'image':['11',0],'channel':'red'}},
        '13':{'class_type':'INPAINT_LoadFooocusInpaint','inputs':{'head':'fooocus_inpaint_head.pth','patch':'inpaint_v26.fooocus.patch'}},
        '14':{'class_type':'LocalDrawGpuFooocusGuard','inputs':{'patch':['13',0]}},
        '15':{'class_type':'INPAINT_VAEEncodeInpaintConditioning','inputs':{'positive':['2',0],'negative':['3',0],'vae':['1',2],'pixels':['10',0],'mask':['12',0]}},
        '16':{'class_type':'INPAINT_ApplyFooocusInpaint','inputs':{'model':['290',0],'patch':['14',0],'latent':['15',2]}},
        '20':{'class_type':'ControlNetLoader','inputs':{'control_net_name':control['name']}},
        '21':{'class_type':'SetUnionControlNetType','inputs':{'control_net':['20',0],'type':'depth'}},
        '22':{'class_type':'LoadImage','inputs':{'image':prefix+'depth.png'}},
        '23':{'class_type':'ControlNetApplyAdvanced','inputs':{'positive':['15',0],'negative':['15',1],'control_net':['21',0],'image':['22',0],'strength':args.control_strength,'start_percent':0.,'end_percent':1.,'vae':['1',2]}},
        '17':{'class_type':'KSampler','inputs':{'model':['16',0],'positive':['23',0],'negative':['23',1],'latent_image':['15',3],'seed':args.seed,'steps':24,'cfg':5.,'sampler_name':'dpmpp_2m_sde_gpu','scheduler':'karras','denoise':args.denoise}},
        '18':{'class_type':'VAEDecode','inputs':{'samples':['17',0],'vae':['1',2]}},
        '7':{'class_type':'SaveImage','inputs':{'images':['18',0],'filename_prefix':prefix+'hand'}}
    })
    if args.prefill!='none':
        if args.prefill=='blur':
            g['40']={'class_type':'INPAINT_MaskedBlur','inputs':{'image':['10',0],'mask':['12',0],'blur':255,'falloff':args.prefill_falloff}}
        else:
            g['40']={'class_type':'INPAINT_MaskedFill','inputs':{'image':['10',0],'mask':['12',0],'fill':args.prefill,'falloff':args.prefill_falloff}}
        g['15']['inputs']['pixels']=['40',0]
        g['41']={'class_type':'SaveImage','inputs':{'images':['40',0],'filename_prefix':prefix+'prefill'}}
    if args.guided_case:
        guidance=case_request['hand_guidance']
        g['2']['inputs']['text']=guided_hand.prompt(guidance['glove'])
        g['3']['inputs']['text']=guided_hand.NEGATIVE
        if args.pose_control:
            cfg=guided_hand.settings();placement=e.read(guide/'hand-guidance.json')
            pose_control=e.read(ROOT/'config/multi-person-control.json')
            with (ROOT/'models/controlnet'/pose_control['name']).open('rb') as stream:
                e.need(hashlib.file_digest(stream,'sha256').hexdigest()==pose_control['sha256'],'既有骨架控制模型校验失败')
            with Image.open(ROOT/cfg['depth_file']) as template:
                pose=guided_hand.pose_from_depth(cfg['projected_joints'],image.size,placement['placement'],template.width)
            pose.update(canvas_width=size[0],canvas_height=size[1])
            for person in pose['people']:
                for name in ('hand_left_keypoints_2d','hand_right_keypoints_2d'):
                    points=person[name]
                    for i in range(0,len(points),3):
                        points[i]=(points[i]-crop[0])*size[0]/(crop[2]-crop[0])
                        points[i+1]=(points[i+1]-crop[1])*size[1]/(crop[3]-crop[1])
            g['24']={'class_type':'ControlNetLoader','inputs':{'control_net_name':pose_control['name']}}
            g['25']={'class_type':'LocalDrawPublishedPoseRender','inputs':{'pose_json':json.dumps(pose),
                'render_body':False,'render_hand':True,'render_face':False}}
            parent='15' if args.pose_only else '23'
            g['26']={'class_type':'ControlNetApplyAdvanced','inputs':{'positive':[parent,0],'negative':[parent,1],
                'control_net':['24',0],'image':['25',0],'strength':.8,'start_percent':0.,'end_percent':1.,'vae':['1',2]}}
            g['17']['inputs'].update(positive=['26',0],negative=['26',1])
            if args.pose_only:
                for key in ('20','21','22','23'):del g[key]
    if args.positive:
        g['2']['inputs']['text']=args.positive
    if args.negative:
        g['3']['inputs']['text']=args.negative
    if args.color_match:
        color_source=['18',0]
        if args.postprocess_from:
            with Image.open(args.postprocess_from) as existing:
                e.need(existing.size==size,'颜色校正来源尺寸不一致')
            shutil.copyfile(args.postprocess_from,inp/'postprocess-source.png')
            g['50']={'class_type':'LoadImage','inputs':{'image':prefix+'postprocess-source.png'}}
            color_source=['50',0]
        g['42']={'class_type':'INPAINT_ColorMatch','inputs':{'target':color_source,'reference':['10',0],
            'exclude_mask':['12',0],'strength':1.}}
        g['7']['inputs']['images']=['42',0]
        if args.postprocess_from:
            g={key:g[key] for key in ('10','11','12','42','50','7')}
    if args.edge_feather:
        candidate=g['7']['inputs']['images']
        # The author node smooths the transition. Intersect it with the user's
        # binary mask so the original cuff/background outside remains exact.
        g['43']={'class_type':'INPAINT_ShrinkMask','inputs':{'mask':['12',0],'shrink':6,'blur':11,'blur_type':'gaussian'}}
        g['44']={'class_type':'MaskComposite','inputs':{'destination':['12',0],'source':['43',0],'x':0,'y':0,'operation':'multiply'}}
        g['45']={'class_type':'ImageCompositeMasked','inputs':{'destination':['10',0],'source':candidate,
            'x':0,'y':0,'resize_source':False,'mask':['44',0]}}
        g['7']['inputs']['images']=['45',0]
    report={'source_image':source_image.relative_to(ROOT).as_posix(),'source_sha256':hashlib.sha256(source_image.read_bytes()).hexdigest(),
            'guide_provenance':e.read(guide/('hand-guidance.json' if args.guided_case else 'source-provenance.json')),
            'author_source':'https://github.com/Acly/comfyui-inpaint-nodes',
            'author_revision':revision,'author_method':'Author inpaint-refine graph: latent_inpaint to Fooocus patch, latent_samples to sampler at denoise=1. Existing Fooocus SDXL patch plus existing depth ControlNet.',
            'existing_weights':weights,'hand_lora':hand,'hand_weight':args.hand_weight,'control':control,'control_strength':args.control_strength,
            'crop':crop,'region':region,'prefill':args.prefill,'prefill_falloff':args.prefill_falloff,'color_match':args.color_match,
            'edge_feather':args.edge_feather,'denoise':args.denoise,
            'postprocess_from':args.postprocess_from.as_posix() if args.postprocess_from else None,
            'mode':'color_match_existing_candidate' if args.postprocess_from else 'inpaint_generation',
            'gpu_only':True,'production_adoption':False,'acceptance':'running','state':{}}
    if args.guided_case:
        report.update(guided_case=args.guided_case.as_posix(),manual_guidance=guidance,
            guide_provenance=e.read(guide/'hand-guidance.json'),pose_control=args.pose_control,
            depth_control=not args.pose_only,
            original_project_untouched=True,exact_composite_mask=True)
    e.save(out/'report.json',report)
    manager=comfyui.ComfyManager()
    try:
        manager.start({'fooocus_hand_trial':True,'diagnostic_loras_directory':str(ROOT/'data/comfy-lora-diagnostic/hand-author')})
        stats=comfyui.api('/system_stats')
        e.need('--gpu-only' in stats['system']['argv'],'禁止 CPU 神经推理回退')
        e.save(out/'gpu-system.json',stats)
        available=comfyui.discover()['lora_files']
        for n in g.values():
            if n['class_type']=='LoraLoader':
                n['inputs']['lora_name']=available[n['inputs']['lora_name'].replace('\\','/')]
        def persist():e.save(out/'report.json',report)
        result=hand_detail.render(g,out,comfyui.config(),{'timeout_seconds':600},report['state'],persist)
        shutil.copyfile(result,out/'crop-after.png')
        patched=image.copy()
        with Image.open(result) as pic:
            e.need(pic.size==size,'重绘输出尺寸不一致')
            local=pic.convert('RGB').resize((crop[2]-crop[0],crop[3]-crop[1]),Image.Resampling.LANCZOS)
        composite=mask.crop(crop) if args.guided_case else mask.crop(crop).filter(ImageFilter.GaussianBlur(3))
        patched.paste(local,crop[:2],composite)
        if args.guided_case:
            diff=ImageChops.difference(image,patched)
            outside=ImageChops.multiply(diff,ImageOps.invert(mask).convert('RGB')).getbbox()
            e.need(outside is None,'描边外像素发生变化')
            report['pixel_preservation']={'outside_selection_changed':False,'changed_bbox':diff.getbbox()}
        patched.save(out/'result.png')
        report['acceptance']='pending_visual_review'
        persist()
        e.save(out.parent/'latest.json',{'path':str(out),'acceptance':report['acceptance']})
        print(out.relative_to(ROOT).as_posix(),flush=True)
    finally:
        manager.stop()


if __name__=='__main__':main()
