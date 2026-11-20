"""Journalled LoRA move and exact reference migration; preserves original names."""
import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from comic_workflow import engine as e
from curate_character_loras import fetch_json, normalized

EXTRA_NAMES = {267118:"留云借风真君",161985:"派蒙",1781597:"罗诺瓦",1781643:"伊斯塔露",267080:"佩丽卡",
               156141:"芙宁娜与芙卡洛斯",477132:"荧与空",1927140:"丰川祥子",530715:"特蕾西娅",255102:"流浪者",231598:"阿米娅"}
STYLE_NAMES = {"原神样式.safetensors":("原神","动画截图风格"),"方舟样式.safetensors":("明日方舟","明日方舟风格"),
               "彩色线条.safetensors":("明日方舟","彩色线条"),"立绘样式.safetensors":("明日方舟","立绘风格"),
               "线条样式.safetensors":("明日方舟","线条风格")}


def plan():
    roster=e.read(ROOT / "data/character-roster.json")["characters"]
    models={m["id"]:m for m in e.read(ROOT / "Chenkin_LoRA/catalog.json")["items"]}
    versions={(m["id"],v["id"]):v for m in models.values() for v in m["modelVersions"]}
    rows=[]; unknown=[]
    for folder in ["models/loras","models/Chenkin_SDXL","Chenkin_LoRA"]:
        for path in (ROOT / folder).rglob("*.safetensors"):
            if path.parent.name in ("明日方舟","原神"): continue
            match=re.match(r"m(\d+)_v(\d+)_f(\d+)_(.*)",path.name)
            oldmatch=re.match(r"(\d+)_(\d+)_(.*)",path.name)
            model_id=int(match[1]) if match else int(oldmatch[1]) if oldmatch else None
            version_id=int(match[2]) if match else int(oldmatch[2]) if oldmatch else None
            version=versions.get((model_id,version_id),{})
            files=version.get("files",[])
            source_file=next((f for f in files if match and f["id"]==int(match[3])),files[0] if files else {})
            original=source_file.get("name",match[4] if match else oldmatch[3] if oldmatch else path.name)
            model=models.get(model_id,{})
            text=" ".join([model.get("name",path.name),version.get("name",""),original])
            game="原神" if "genshin" in str(path).casefold() or "genshin" in text.casefold() else "明日方舟"
            if path.name in STYLE_NAMES:
                game,name=STYLE_NAMES[path.name]
                sha=hashlib.file_digest(path.open("rb"),"sha256").hexdigest()
                try:
                    source=fetch_json("https://civitai.com/api/v1/model-versions/by-hash/"+sha,"legacy-hash-"+sha)
                    sf=next((f for f in source.get("files",[]) if f.get("hashes",{}).get("SHA256","").lower()==sha),{})
                    original=sf.get("name",original)
                except RuntimeError: pass
            elif model_id in EXTRA_NAMES: name=EXTRA_NAMES[model_id]
            elif any(t in text.casefold() for t in ["all characters","all in one"]): name=game+"全角色"
            elif "style" in text.casefold(): name=game+"风格"
            else:
                hits=[]
                title=normalized(text)
                for c in roster:
                    if c["game"]!=game: continue
                    for alias in [c["name"],c["english"],*c["aliases"]]:
                        norm=normalized(alias)
                        if len(norm)>=3 and norm in title: hits.append((len(norm),c["name"]))
                        elif alias==c['name'] and re.search(r"(?<![\u3400-\u9fff])"+re.escape(alias)+r"(?![\u3400-\u9fff])",text): hits.append((len(norm),c["name"]))
                if not hits:
                    unknown.append({"file":path.relative_to(ROOT).as_posix(),"model":text,"model_id":model_id});continue
                name=max(hits)[1]
            target=ROOT / "models/loras/sdxl" / game / (name+"_"+original)
            e.need(not any(ch in original for ch in '<>:"/\\|?*'),"原始文件名包含无效路径字符")
            rows.append({"old":path.relative_to(ROOT).as_posix(),"new":target.relative_to(ROOT).as_posix(),
                         "name":name,"game":game,"original_name":original,"size":path.stat().st_size,
                         "model_id":model_id,"version_id":version_id,"triggers":version.get("trainedWords",[])})
    e.save(ROOT / "data/lora-curation/rename-plan.json",{"moves":rows,"unresolved":unknown})
    print(json.dumps({"planned":len(rows),"unresolved":unknown},ensure_ascii=False),flush=True)
    return rows,unknown


def migrate():
    rows,unknown=plan()
    e.need(not unknown,"仍有名称待核实，请先解决 rename-plan.json 中未识别文件")
    # Validate all collisions before touching any weight or references.
    for r in rows:
        old,new=ROOT/r['old'],ROOT/r['new']
        if new.exists():
            e.need(hashlib.file_digest(old.open('rb'),'sha256').hexdigest()==hashlib.file_digest(new.open('rb'),'sha256').hexdigest(),"同名目标权重不同，拒绝覆盖："+r['new'])
    backup=ROOT / 'data/backups' / ('lora-rename-'+e.stamp());backup.mkdir(parents=True)
    e.save(backup/'journal.json',rows)
    alias_path=ROOT/'data/lora-curation/rename-result.json'
    previous_mapping=e.read(alias_path).get('mapping',{}) if alias_path.exists() else {}
    mapping={}
    for r in rows:
        old=Path(r['old']);new=Path(r['new']);shortold=old.as_posix().removeprefix('models/loras/');shortnew=new.as_posix().removeprefix('models/loras/')
        for a,b in [(old.as_posix(),new.as_posix()),(shortold,shortnew),(str(ROOT/old),str(ROOT/new))]:
            mapping[a]=b;mapping[a.replace('/','\\')]=b.replace('/','\\')
        # Former alternate library files can also be referenced by their full filename.
        mapping[old.name]=shortnew
    mapping={**{k:mapping.get(v,v) for k,v in previous_mapping.items()},**mapping}
    def rewrite(value,key=''):
        if isinstance(value,dict):return {mapping.get(k,k):rewrite(v,k) for k,v in value.items()}
        if isinstance(value,list):return [rewrite(v,key) for v in value]
        if isinstance(value,str) and key not in ['original_name','name','model','url','page','original_file']:
            return mapping.get(value,value)
        return value
    # Preserve snapshots of changed files. Download source metadata remains immutable.
    changed=[]
    for dirname in ['data/projects','data/creative','data/input-drafts','data/characters','data/lora-label-history','data/test-results','config','workflows','runs']:
        for path in (ROOT/dirname).rglob('*.json'):
            try:original=e.read(path)
            except (OSError,ValueError):continue
            updated=rewrite(original)
            if updated!=original:
                rel=path.relative_to(ROOT);dest=backup/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,dest)
                e.save(path,updated);changed.append(rel.as_posix())
    for path in [ROOT/'data/lora-labels.json',ROOT/'data/lora-load-tests.json']:
        if path.exists():
            updated=rewrite(e.read(path));shutil.copy2(path,backup/path.name);e.save(path,updated);changed.append(path.relative_to(ROOT).as_posix())
    for r in rows:
        old,new=ROOT/r['old'],ROOT/r['new'];new.parent.mkdir(parents=True,exist_ok=True)
        if new.exists():
            # Keep exact duplicate weights in the recoverable migration backup.
            dest=backup/r['old'];dest.parent.mkdir(parents=True,exist_ok=True);old.replace(dest)
        else:old.replace(new)
    # Populate display and trigger metadata without inventing character-card bindings.
    path=ROOT/'data/lora-labels.json';labels=e.read(path) if path.exists() else {}
    for r in rows:
        file=r['new'].removeprefix('models/loras/')
        label=labels.setdefault(file,{})
        label['name']=r['name'];label.setdefault('trigger_words',', '.join(r['triggers']));label.setdefault('weight',0.7)
    e.save(path,labels)
    e.save(ROOT/'data/lora-curation/rename-result.json',{'moves':rows,'changed_references':changed,'backup':str(backup),'mapping':mapping})
    print('重命名完成',len(rows),'；引用文件更新',len(changed),flush=True)


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8');p=argparse.ArgumentParser();p.add_argument('--apply',action='store_true');args=p.parse_args()
    migrate() if args.apply else plan()
