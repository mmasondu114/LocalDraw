"""GPU-only visual review of actual comparison renders, with saved evidence."""
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from comic_workflow import creative, engine as e
from comic_workflow.model import ModelManager
from comic_workflow.schemas import obj,arr,S,enum

if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    folder=Path(e.read(ROOT/'data/test-results/multi-character-gpu/latest.json')['path'])
    rows=e.read(folder/'renders.json');manager=ModelManager();results=[]
    schema=obj({'people_count':{'type':'integer','minimum':0,'maximum':12},'characters':arr(S),
                'anatomy':enum('pass','reject','uncertain'),'background':enum('pass','reject','uncertain'),
                'scale_consistency':enum('pass','reject','uncertain'),'reason':S})
    try:
        manager.start(vision=True,update=lambda msg:None)
        for row in rows:
            result,metrics=creative.structured(
                '你是画面验收员。独立观察图像，不要照抄预期人数。数真实可见人物，包括边缘人物。按给出的外观识别各角色，分别说明。检查多余头脸、四肢融合、明显畸形、背景断裂或拼接、同排人物的异常尺度。被遮挡的手不臆测指头错误，合理透视不是畸形。只输出JSON，reason用中文。',
                {'expected_count':row['expected_count'],'characters':row['characters'],'scene':'多人在公园并排站立，中景，同一镜头'},schema,
                folder/'vision'/row['key'],image=folder/row['file'],max_tokens=700)
            results.append({'key':row['key'],'expected_count':row['expected_count'],'result':result,'metrics':metrics})
            e.save(folder/'vision-review.json',results);print(row['key'],result,flush=True)
        command=e.read(ROOT/'data/logs/model-command.json');e.need('--no-mmproj-offload' not in command and 'CUDA0' in command,'视觉审核未满足GPU配置')
        e.save(folder/'vision-gpu-command.json',command)
    finally:manager.stop()
