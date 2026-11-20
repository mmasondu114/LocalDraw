"""Exercise the production interaction review on the actual rejected hand.

The local vision review is supporting evidence, never a replacement for the
human image inspection or automatic permission to adopt a drawing recipe.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
from unittest.mock import patch

from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from comic_workflow import creative, engine as e
from comic_workflow.model import ModelManager


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser()
    parser.add_argument('--candidate',type=Path,action='append',default=[])
    parser.add_argument('--finger-evidence',action='store_true')
    parser.add_argument('--source',type=Path,default=ROOT/'data/test-results/native-studio-batch/20261007-234949-400872')
    parser.add_argument('--hand-box',type=int,nargs=4,action='append',default=[])
    args=parser.parse_args()
    for candidate in args.candidate:
        e.need(candidate.is_file(),'Review candidate image is missing')
    out=ROOT/'data/test-results/interaction-review-gpu'/e.stamp()
    out.mkdir(parents=True)
    source=args.source.resolve()
    provenance=e.read(source/'source-provenance.json')
    roles=provenance['request']['characters']
    panel={'characters':[{'character_id':r['name'],'action':'互相拥抱'} for r in roles],
           'drawing_description':provenance['request']['text']}
    refs=[{'id':r['name'],'name':r['name'],'saved_character':{'profile':{'clothing':r['prompt']}}} for r in roles]
    manager=ModelManager()
    log=ROOT/'data/logs/model-server.log'
    offset=log.stat().st_size if log.exists() else 0
    try:
        e.need(not manager.status()['ready'],'Existing language service must finish before isolated GPU review')
        print('Loading existing studio vision model on GPU.',flush=True)
        original_popen=subprocess.Popen
        def verbose_start(argv,**kwargs):
            actual_argv=list(argv)+['--verbose']
            e.save(out/'actual-model-argv.json',actual_argv)
            return original_popen(actual_argv,**kwargs)
        with patch('comic_workflow.model.subprocess.Popen',side_effect=verbose_start):
            manager.start(vision=True,update=lambda _:None)
        model_args=e.read(ROOT/'data/logs/model-command.json')
        e.need(model_args[model_args.index('--gpu-layers')+1]=='all' and model_args[model_args.index('--device')+1]=='CUDA0','Neural CPU fallback forbidden')
        with log.open('rb') as stream:
            stream.seek(offset)
            lines=stream.read().decode('utf-8',errors='replace').splitlines()
        offload=[line for line in lines if re.search(r'offloaded\s+(\d+)/(\d+)\s+layers to GPU',line)]
        e.need(bool(offload),'GPU layer offload confirmation missing')
        match=re.search(r'offloaded\s+(\d+)/(\d+)\s+layers to GPU',offload[-1])
        e.need(match.group(1)==match.group(2),'Not all neural layers are on GPU')
        vision_cuda=[line for line in lines if 'CUDA0' in line and ('clip' in line.lower() or 'vision' in line.lower() or 'mmproj' in line.lower())]
        e.need(bool(vision_cuda),'Vision CUDA allocation confirmation missing; refuse unverified inference')
        e.save(out/'gpu-proof.json',{'argv_gpu_layers':'all','argv_device':'CUDA0','all_layers_offload':offload[-1],
                                   'vision_cuda':vision_cuda,'cpu_model_fallback':False})
        original_structured=creative.structured
        results=[]
        cases=[('source',source/'result.png')]
        cases.extend((f'candidate_{index+1}',image.resolve()) for index,image in enumerate(args.candidate)
                     if image.resolve()!=(source/'result.png').resolve())
        if not args.candidate:
            cases.append(('partial_candidate',ROOT/'data/test-results/pair-hand-detailer/20261008-005904-590974/result.png'))
        for label,image in cases:
            folder=out/label
            folder.mkdir()
            crops=[]
            with Image.open(image) as pic:
                for index,box in enumerate(args.hand_box or [(960,535,1100,688)]):
                    e.need(0<=box[0]<box[2]<=pic.width and 0<=box[1]<box[3]<=pic.height,'Closeup box is outside the candidate')
                    crop=folder/f'candidate-hand-closeup-{index+1}.png'
                    pic.crop(box).resize((420,459),Image.Resampling.LANCZOS).save(crop)
                    crops.append(crop)
            def with_closeup(prompt,context,schema,logdir,update,images,max_tokens):
                return original_structured(prompt,context,schema,logdir,update,
                    images[:1]+[('候选画格中的局部放大：仅用于检查手及其连接，不是另一个人物或参考图',crop) for crop in crops]+images[1:],max_tokens)
            print('Reviewing actual image:',label,flush=True)
            with patch.object(creative,'structured',side_effect=with_closeup):
                review=creative.review_image(panel,provenance['request']['text'],image,'',refs,folder/'model',lambda _:None)
            e.save(folder/'review.json',review)
            item={'case':label,'image':image.relative_to(ROOT).as_posix(),'verdict':review['verdict'],
                  'interaction_anatomy':review.get('interaction_anatomy')}
            if args.finger_evidence and label!='source':
                e.need(len(crops)==1,'Individual fingertip evidence requires one focus region')
                crop=crops[0]
                integer={'type':'integer'}
                evidence_schema=creative.obj({
                    'visible_fingers':creative.arr(creative.obj({'tip_x':integer,'tip_y':integer,
                        'connected_to_palm':creative.enum('yes','no','uncertain'),'observation':creative.S})),
                    'thumb_observation':creative.S,'shape_verdict':creative.enum('pass','reject','uncertain'),
                    'reason':creative.S})
                evidence,metrics=original_structured(
                    '只检查这张局部图中的手部可见事实，不猜角色姓名或归属。逐个列出实际可见指尖，坐标以这张420x459局部图左上角为原点。'
                    '每个真实指尖只列一次；袖口、衣服、装饰边缘和手指高光不另算手指。不要预设指尖数量。'
                    '说明每根可见手指是否连续连接到手掌，说明拇指所在侧。不能确认的部分记uncertain，不编造被遮住的形状。'
                    '正常形状pass，明确多余或断裂手指reject，无法确认uncertain。',
                    {'task':'Enumerate visible fingertip evidence before judging hand shape','crop_width':420,'crop_height':459},
                    evidence_schema,folder/'finger-model',lambda _:None,[('待核查的唯一手部局部图',crop)],1400)
                invalid_tips=[index for index,finger in enumerate(evidence['visible_fingers'])
                    if not (0<=finger['tip_x']<420 and 0<=finger['tip_y']<459)]
                evidence_quality={'coordinate_bounds_valid':not invalid_tips,'invalid_tip_indices':invalid_tips}
                e.save(folder/'finger-evidence.json',{'evidence':evidence,'metrics':metrics,'gpu_only':True,
                    'evidence_quality':evidence_quality,
                    'note':'Supporting isolated observation; does not override production review or visual inspection.'})
                item['finger_evidence']=evidence
                item['finger_evidence_quality']=evidence_quality
            results.append(item)
            e.save(out/'results.json',{'gpu_only':True,'results':results,'production_drawing_adoption':False,
                                      'scope':'Production review function with explicit same-image hand closeup; human visual acceptance remains incomplete.'})
            print(label,review['verdict'],flush=True)
        e.save(out.parent/'latest.json',{'path':str(out),'completed':True})
        print(out.relative_to(ROOT).as_posix(),flush=True)
    finally:
        manager.stop()


if __name__=='__main__':
    main()
