"""Journalled Chinese naming and character ownership for all installed LoRAs.

Source titles/triggers remain unchanged. Unknown identities are reported, never
silently assigned to a guessed person. Invoke only while download workers are idle.
"""
import argparse
import copy
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from comic_workflow import engine as e
from comic_workflow.characters import CharacterLibrary, DETAILS

LIB=ROOT/'models/loras'
OUT=ROOT/'data/lora-cn-library'
STATE=ROOT/'data/character-expansion-20261031/status.json'
CONFIG=ROOT/'config/lora-chinese-identities.json'
CN=re.compile(r'^[\u3400-\u9fff·・]+$')


def translate_name(name, cfg):
    return cfg.get('names',{}).get(name,name)


def resolve(path, label, row, cfg):
    if row:
        spec=cfg.get('versions',{}).get(str(row['version_id'])) or cfg.get('models',{}).get(str(row['model_id']))
        if spec:
            spec=dict(spec)
            spec['name']=translate_name(spec['name'],cfg)
            return spec
    if label.get('chinese_identity_verified') and CN.fullmatch(label.get('name','')):
        return {'name':translate_name(label['name'],cfg),'kind':label.get('asset_kind','character'),'basis':label.get('name_basis','已核对的中文绑定')}
    # Legacy curated folders have explicit identities in their first name segment.
    if row and row.get('group') not in ('明日方舟','原神'):return None
    name=translate_name(path.stem.split('_',1)[0],cfg)
    if not CN.fullmatch(name):return None
    kind='collection' if any(x in name for x in ('全角色','合集','与')) else 'style' if any(x in name for x in ('画风','风格','线条','加速','修复','编辑','动画','摄影','油画','水粉','波普','动漫')) else 'character'
    return {'name':name,'kind':kind,'basis':'现有已命名角色目录/档案；中文别名保留原文'}


def plan():
    cfg=e.read(CONFIG); labels=e.read(ROOT/'data/lora-labels.json')
    state=e.read(STATE) if STATE.exists() else {'files':{}}
    by_path={r['relative_path']:r for r in state['files'].values()}
    rows=[]; pending=[]
    for path in sorted(LIB.rglob('*.safetensors')):
        old=path.relative_to(LIB).as_posix(); label=labels.get(old,{})
        row=by_path.get(old); spec=resolve(path,label,row,cfg)
        if not spec:
            pending.append({'file':old,'title':(row or {}).get('title',label.get('name',path.stem)),
                'model_id':(row or {}).get('model_id'),'version_id':(row or {}).get('version_id'),
                'version':(row or {}).get('version'),'triggers':(row or {}).get('triggers',[])})
            continue
        assert CN.fullmatch(spec['name']) and len(spec['name'])<=32
        version=(row or {}).get('version_id') or label.get('source_version_id')
        if not version:
            match=re.search(r'_(\d{5,})$',path.stem)
            version=match[1] if match else int(hashlib.sha256(old.encode()).hexdigest()[:8],16)
        if label.get('chinese_identity_verified') and path.stem.startswith(spec['name']+'_版本'):
            new=old
        else:new=(path.parent/(spec['name']+'_版本'+str(version)+'.safetensors')).relative_to(LIB).as_posix()
        rows.append({'old':old,'new':new,**spec,'bytes':path.stat().st_size,'existing_owner':label.get('character_id',''),
                     'model_id':(row or {}).get('model_id'),'version_id':(row or {}).get('version_id')})
    # Never overwrite distinct files that share a source version ID.
    seen={}
    for r in rows:
        if r['new'] in seen and seen[r['new']]!=r['old']:
            suffix=str(int(hashlib.sha256(r['old'].encode()).hexdigest()[:8],16))
            r['new']=r['new'].removesuffix('.safetensors')+'_副本'+suffix+'.safetensors'
        seen[r['new']]=r['old']
    return {'files':rows,'pending':pending,'total_files':len(rows)+len(pending)}


def rewrite(value,mapping):
    if isinstance(value,dict):return {mapping.get(k,k):rewrite(v,mapping) for k,v in value.items()}
    if isinstance(value,list):return [rewrite(v,mapping) for v in value]
    return mapping.get(value,value) if isinstance(value,str) else value


def profile(name,kind):
    result={k:'unknown' for k in DETAILS}
    result['constraints']='Use only the selected model version. Keep unverified appearance and biography unspecified.'
    result.update(observed=[],suggested=[],unknowns=['Visual accuracy has not been verified by rendering.'])
    return result


def apply_plan(report):
    OUT.mkdir(parents=True,exist_ok=True)
    backup=OUT/'backups'/e.stamp();backup.mkdir(parents=True)
    def back(path):
        if path.exists():
            dest=backup/path.relative_to(ROOT)
            if not dest.exists():dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,dest)
    label_path=ROOT/'data/lora-labels.json'; labels=e.read(label_path);back(label_path);back(STATE)
    alias_path=ROOT/'data/lora-curation/rename-result.json';back(alias_path)
    alias=e.read(alias_path) if alias_path.exists() else {}
    mapping={}
    for r in report['files']:
        if r['old']==r['new']:continue
        old,new=LIB/r['old'],LIB/r['new']
        assert old.resolve().is_relative_to(LIB.resolve()) and new.resolve().is_relative_to(LIB.resolve())
        if new.exists():raise ValueError('Rename collision: '+r['new'])
        for a,b in [(r['old'],r['new']),('models/loras/'+r['old'],'models/loras/'+r['new']),
                    (old.as_posix(),new.as_posix())]:
            mapping[a]=b;mapping[a.replace('/','\\')]=b.replace('/','\\')
    e.save(backup/'move-journal.json',report)
    # A pre-written alias map keeps saved old project references resolvable.
    alias['mapping']={**{k:mapping.get(v,v) for k,v in alias.get('mapping',{}).items()},**mapping}
    e.save(alias_path,alias)
    for r in report['files']:
        if r['old']!=r['new']:
            old,new=LIB/r['old'],LIB/r['new'];new.parent.mkdir(parents=True,exist_ok=True);old.rename(new)
            assert new.stat().st_size==r['bytes']
    labels={mapping.get(k,k):v for k,v in labels.items()}
    cfg=e.read(CONFIG); library=CharacterLibrary(); cards=library.listing(); by_id={c['id']:c for c in cards}
    # Rename legacy English card names in place, preserving IDs and old aliases.
    for card in cards:
        translated=translate_name(card['name'],cfg)
        if translated==card['name']:continue
        folder=library.root/'cards'/card['id'];back(folder/'current.json')
        updated=copy.deepcopy(card);updated['name']=translated;updated['revision']+=1
        updated['source']['aliases']=list(dict.fromkeys([*updated['source'].get('aliases',[]),card['name']]))
        updated['saved_at']=e.stamp();e.save(folder/'versions'/f"{updated['revision']:06}.json",updated);e.save(folder/'current.json',updated)
        by_id[card['id']]=updated
    by_name={c['name']:c for c in by_id.values()}; created=[]
    for r in report['files']:
        label=labels.setdefault(r['new'],{})
        card=by_name.get(r['name'])
        if not card:
            source={'mode':'text','text':'由本地LoRA来源名称与人工核对的中文名称建档。','game':Path(r['new']).parts[1],
                    'asset_kind':r['kind'],'aliases':[],'profile_basis':'仅确认名字、来源和文件绑定；未编造人物资料'}
            card=library.save(r['name'],profile(r['name'],r['kind']),source)
            by_name[r['name']]=card;by_id[card['id']]=card;created.append(card['name'])
        label.setdefault('original_filename',r['old'])
        label.setdefault('original_display_name',label.get('name',''))
        label.update(name=r['name'],character=r['name'],character_id=card['id'],asset_kind=r['kind'],
                     name_basis=r['basis'],chinese_identity_verified=True,scope='characters' if r['kind']=='character' else 'global')
        label.setdefault('weight',0.7);label.setdefault('trigger_words','');label.setdefault('preferred',False)
        if r.get('version_id'):label['source_version_id']=r['version_id']
        if r.get('model_id'):label['source_model_id']=r['model_id']
    # Retain every file in its owner's card, one preferred weight per model family.
    owners={}
    for r in report['files']:owners.setdefault(labels[r['new']]['character_id'],[]).append(r['new'])
    for ident,files in owners.items():
        card=by_id[ident];folder=library.root/'cards'/ident
        updated=copy.deepcopy(card);source=updated['source']
        source['lora_files']=list(dict.fromkeys([mapping.get(f,f) for f in source.get('lora_files',[])]+files))
        selected={}
        for filename in files:
            family=filename.split('/')[0]
            if family not in selected or labels[filename].get('preferred'):selected[family]=filename
        for filename in files:labels[filename]['preferred']=selected[filename.split('/')[0]]==filename and labels[filename]['asset_kind']=='character'
        source['primary_loras']=selected
        if updated['profile']['identity'] == 'unknown' and source.get('asset_kind') == 'character':
            from comic_workflow.character_prompt_cleanup import visual_profile
            primary=selected.get('anime') or next(iter(selected.values()),None)
            if primary:
                label=labels[primary]
                updated['profile'].update(visual_profile(label.get('appearance_prompt') or label.get('trigger_words','')))
        if source.get('primary_lora'):source['primary_lora']=mapping.get(source['primary_lora'],source['primary_lora'])
        if updated!=card:
            back(folder/'current.json')
            updated['revision']+=1;updated['saved_at']=e.stamp()
            e.save(folder/'versions'/f"{updated['revision']:06}.json",updated);e.save(folder/'current.json',updated)
    e.save(label_path,labels)
    # Update editable settings and queued downloads; history remains immutable and
    # works through aliases. No historical source titles or trigger words change.
    references=[*ROOT.glob('config/*.json'),*ROOT.glob('workflows/*.json')]
    references += [p for p in (ROOT/'data/projects').rglob('*.json') if 'versions' not in p.parts and 'history' not in p.parts and p.name in ('project.json','drawing-settings.json','prompt-settings.json','lora-settings.json')]
    references += [ROOT/'data/lora-load-tests.json']
    changed=[]
    for path in references:
        if not path.exists() or path==CONFIG:continue
        try:original=e.read(path)
        except (OSError,ValueError):continue
        updated=rewrite(original,mapping)
        if updated!=original:back(path);e.save(path,updated);changed.append(path.relative_to(ROOT).as_posix())
    if STATE.exists():
        state=e.read(STATE)
        for row in state['files'].values():
            old=row['relative_path'];row['relative_path']=alias['mapping'].get(old,old)
            if labels.get(row['relative_path'],{}).get('character_id'):
                label=labels[row['relative_path']]
                row.setdefault('original_display_name',row['name']);row['name']=label['name'];row['character_id']=label['character_id']
        e.save(STATE,state)
    report.update(created_cards=created,renamed_files=sum(r['old']!=r['new'] for r in report['files']),
                  bound_files=len(report['files']),backup=backup.as_posix(),changed_references=changed)
    e.save(OUT/'last-result.json',report)
    return report


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    OUT.mkdir(parents=True,exist_ok=True)
    report=plan();e.save(OUT/'plan.json',report)
    if args.apply:report=apply_plan(report)
    print(json.dumps({k:v for k,v in report.items() if k not in ('files','pending','created_cards')},ensure_ascii=False))
    for row in report['pending']:print('NEEDS_IDENTITY',json.dumps(row,ensure_ascii=False))


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8');main()
