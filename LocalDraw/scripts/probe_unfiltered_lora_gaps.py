"""Inspect actual safetensors headers for missing characters, without family filters.

Header probes only nominate candidates. Complete SHA256 verification and the
normal library inspector are still required before a weight enters the library.
"""
import concurrent.futures
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import sys
import urllib.parse
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
import curate_character_loras as curator
from comic_workflow import engine as e

spec=importlib.util.spec_from_file_location('probe_resolver',ROOT/'Chenkin_LoRA/download.py')
resolver=importlib.util.module_from_spec(spec);spec.loader.exec_module(resolver)


def probe_header(file,token,proxy):
    sha=file['hashes']['SHA256'].lower()
    path=ROOT/'data/lora-curation/header-probes'/(str(file['id'])+'-'+sha[:12]+'.json')
    if path.exists():header=e.read(path)
    else:
        entry=resolver.resolve_download({'url':file['downloadUrl'],'relative_path':file['name'],'sha256':sha},token,proxy)
        request=urllib.request.Request(entry['url'],headers={'Range':'bytes=0-16777223','User-Agent':'LocalDraw-HeaderProbe/1.0'})
        with urllib.request.urlopen(request,timeout=40) as response:
            raw=response.read(8)
            e.need(len(raw)==8,'不是 safetensors 文件头')
            size=struct.unpack('<Q',raw)[0];e.need(2<=size<=16_000_000,'文件头尺寸无效')
            data=response.read(size);e.need(len(data)==size,'文件头不完整')
            header=json.loads(data)
        e.save(path,header)
    metadata=header.get('__metadata__',{})
    dimensions=sorted({v['shape'][1] for k,v in header.items() if k!='__metadata__'
                       and 'attn2_to_k' in k and 'lora_down' in k and len(v.get('shape',[]))==2})
    return {'header_cross_attention_dimensions':dimensions,'header_compatible':dimensions==[2048],
            'header_base_model_version':metadata.get('ss_base_model_version',''),
            'header_training_model':metadata.get('ss_sd_model_name',''),
            'header_evidence':str(path.relative_to(ROOT)),'full_file_verified':False}


def inspect_gap(row,roster,token,proxy,rejected,max_pages):
    character=row['character'];game='Arknights' if character['game']=='明日方舟' else 'Genshin'
    identities=[character['name'],character['english'],*character['aliases']]
    queries=list(dict.fromkeys([*identities,*[name+' '+game for name in identities]]))
    files={};errors=[];truncated=[];page_counts={}
    for query in queries:
        params=urllib.parse.urlencode({'query':query,'types':'LORA','limit':100,'nsfw':'true','sort':'Most Downloaded'})
        try:
            url='https://civitai.com/api/v1/models?'+params;seen=set();pages=0
            while url and pages<max_pages:
                parsed=urllib.parse.urlsplit(url)
                e.need(parsed.scheme=='https' and parsed.netloc=='civitai.com' and parsed.path=='/api/v1/models','目录分页地址异常')
                e.need(url not in seen,'目录分页地址重复');seen.add(url)
                cached='unfiltered-gap-'+hashlib.sha256(parsed.query.encode()).hexdigest()[:20]
                listing=curator.fetch_json(url,cached);pages+=1
                for model in listing.get('items',[]):
                    for version in model.get('modelVersions',[]):
                        if not curator.matches(model,character,roster,version):continue
                        for file in version.get('files',[]):
                            sha=file.get('hashes',{}).get('SHA256','').lower()
                            if file.get('type')!='Model' or not file.get('name','').endswith('.safetensors') or len(sha)!=64 or sha in rejected:continue
                            files.setdefault(file['id'],(model,version,file))
                url=listing.get('metadata',{}).get('nextPage')
            page_counts[query]=pages
            if url:truncated.append(query)
        except Exception as exc:
            errors.append({'query':query,'error_type':type(exc).__name__})
    results=[]
    for model,version,file in files.values():
        result={'model_id':model['id'],'model':model['name'],'version_id':version['id'],
                'api_base_model':version['baseModel'],'file_id':file['id'],'original_name':file['name'],
                'sha256':file['hashes']['SHA256'].lower(),'url':file['downloadUrl'],
                'page':f"https://civitai.com/models/{model['id']}?modelVersionId={version['id']}",
                'triggers':version.get('trainedWords') or []}
        try:result.update(probe_header(file,token,proxy))
        except Exception as exc:result.update(header_compatible=None,error_type=type(exc).__name__)
        results.append(result)
    print(character['name']+'：'+str(len(results))+' 个身份匹配文件，'+str(sum(r.get('header_compatible') is True for r in results))+' 个真实 SDXL 文件头',flush=True)
    return {'character':character,'queries':queries,'searched_pages':page_counts,'truncated_queries':truncated,
            'candidates':results,'search_errors':errors}


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser();parser.add_argument('--pages',type=int,default=20);args=parser.parse_args()
    e.need(1<=args.pages<=100,'分页上限应在 1 至 100 之间')
    rows=[r for r in e.read(ROOT/'data/lora-curation/selection.json')['characters'] if not r['selected']]
    roster=e.read(ROOT/'data/character-roster.json')['characters']
    token=(ROOT/'Chenkin_LoRA/civitai-token.txt').read_text().strip()
    proxy=urllib.request.getproxies().get('https')
    rejected={r['sha256'] for r in e.read(ROOT/'data/lora-curation/rejected-files.json')}
    results=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        futures=[pool.submit(inspect_gap,row,roster,token,proxy,rejected,args.pages) for row in rows]
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())
            e.save(ROOT/'data/lora-curation/research/unfiltered-gap-header-results.json',
                   {'required_gaps':len(rows),'examined':len(results),'complete':len(results)==len(rows),'characters':results})


if __name__=='__main__':main()
