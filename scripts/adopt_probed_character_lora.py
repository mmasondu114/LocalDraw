"""Adopt a complete, hash-verified weight whose public family label was wrong."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
import curate_character_loras as curator
from comic_workflow import engine as e
from comic_workflow.lora_inspection import inspect_lora


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser()
    parser.add_argument('--name',required=True)
    parser.add_argument('--model',required=True,type=int)
    parser.add_argument('--version',required=True,type=int)
    parser.add_argument('--trigger',required=True)
    args=parser.parse_args()
    manifest_path=ROOT/'data/lora-curation/selection.json'
    manifest=e.read(manifest_path);roster=e.read(ROOT/'data/character-roster.json')['characters']
    row=next(r for r in manifest['characters'] if r['character']['name']==args.name)
    e.need(row['selected'] is None,'已有选型，需先比较实际候选效果，不能直接覆盖')
    model=curator.fetch_json(f'https://civitai.com/api/v1/models/{args.model}',f'direct-model-{args.model}')
    version=next(v for v in model['modelVersions'] if v['id']==args.version)
    e.need(curator.matches(model,row['character'],roster,version),'模型不属于指定角色')
    file=next(f for f in version['files'] if f['type']=='Model' and f.get('primary',True))
    source=ROOT/'data/lora-curation/probes'/file['name']
    e.need(source.is_file(),'探查文件尚未完整下载')
    with source.open('rb') as stream:sha=hashlib.file_digest(stream,'sha256').hexdigest()
    e.need(sha==file['hashes']['SHA256'].lower(),'文件 SHA256 不符，不得采用')
    inspection=inspect_lora(source)
    e.need(inspection['structurally_compatible'] is True,'探查文件不是可用 SDXL 权重')
    with source.open('rb') as stream:
        size=struct.unpack('<Q',stream.read(8))[0];metadata=json.loads(stream.read(size)).get('__metadata__',{})
    frequency=json.loads(metadata.get('ss_tag_frequency','{}'))
    e.need(any(args.trigger in tags for tags in frequency.values()),'触发词没有文件训练记录依据')
    corrected={**version,'baseModel':'SDXL 1.0'}
    score,evidence=curator.rank_version(model,corrected,file)
    chosen={'model_id':model['id'],'model':model['name'],'version_id':version['id'],'version':version['name'],
            'base_model':'SDXL 1.0','api_base_model':version['baseModel'],'original_name':file['name'],
            'file_id':file['id'],'sha256':sha,'bytes':source.stat().st_size,'url':file['downloadUrl'],
            'page':f"https://civitai.com/models/{model['id']}?modelVersionId={version['id']}",
            'triggers':[args.trigger],'score':score,'evidence':{**evidence,
                'verified_family_override':inspection,'header_base_model_version':metadata.get('ss_base_model_version'),
                'trigger_source':{'type':'verified_safetensors_training_header','sha256':sha,'tokens':[args.trigger]}}}
    chosen['relative_path']=row['character']['game']+'/'+args.name+'_'+file['name']
    destination=ROOT/'models/loras'/chosen['relative_path'];destination.parent.mkdir(parents=True,exist_ok=True)
    if not destination.exists():os.link(source,destination)
    else:
        with destination.open('rb') as stream:existing=hashlib.file_digest(stream,'sha256').hexdigest()
        e.need(existing==sha,'目标文件不同，保留已有文件并停止')
    row['candidates'].append(chosen);row.update(selected=chosen,status='selected_verified_family_override')
    e.save(manifest_path,manifest)
    e.save(ROOT/'data/lora-curation/characters'/(row['character']['game']+'-'+row['character']['id']+'.json'),row)
    gap_path=ROOT/'data/lora-curation/missing-downloads.json';gaps=e.read(gap_path)
    gaps['characters']=[r for r in gaps['characters'] if r['name']!=args.name];e.save(gap_path,gaps)
    curator.finalize_library()
    print(args.name+' 已按真实文件结构采用并保留原始文件名',flush=True)


if __name__=='__main__':main()
