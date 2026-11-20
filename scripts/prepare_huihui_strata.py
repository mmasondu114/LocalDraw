"""Prepare the exact verified Huihui variant without changing active studio settings."""
import hashlib,json,pathlib,subprocess,sys,time
ROOT=pathlib.Path(__file__).resolve().parents[1]
MODEL=pathlib.Path('F:/AI_Models/Strata/Huihui-Qwen3.8-Flash-Next-abliterated-v2-UD-IQ4_XS')
STRATA=ROOT/'runtime/strata-terminal/0.1.49.2'
PACK=pathlib.Path('F:/AI_Models/Strata/packs/huihui-v2-ud-iq4-xs')
PROFILE=pathlib.Path('F:/AI_Models/Strata/qwen-flash-next-abliterated-iq4-256k.json')
OUT=ROOT/'data/strata-setup/abliterated-iq4-prepare.json'
def sha(path):
 h=hashlib.sha256()
 with path.open('rb') as f:
  while block:=f.read(16*1024*1024):h.update(block)
 return h.hexdigest()
def main():
 if not (MODEL/'download-complete.json').exists():raise SystemExit('Waiting: author SHA-256 verification and final publication are not complete.')
 entries=json.loads((MODEL/'manifest.json').read_text())
 expected=['5ce89370720f8bf90890f439361282104c1aa1482d4013bb9a50923e758e71a4','ede87d3bd3e354d43f0e710b2df6791d3479e396847e53adbe2b48be7ba6d663','f8aa8bd7ba134b3b7fe4fc95b9131fc8f6678ace374a0fa6e632225031665995']
 assert [e['sha256'] for e in entries]==expected
 for e in entries:
  path=MODEL/e['name'];assert path.stat().st_size==e['size']
  print('Verifying author shard: '+path.name,flush=True)
  assert sha(path)==e['sha256'],path
 if not (PACK/'native_experts.txt').exists() or not (PACK/'tokenizer/vocab.json').exists():
  subprocess.run([sys.executable,str(STRATA/'tools/iq_pack.py'),'--gguf',str(MODEL/entries[0]['name']),'--out',str(PACK),'--compat-bf16'],cwd=STRATA,check=True)
 cfg=json.loads(pathlib.Path('F:/AI_Models/Strata/qwen-flash-next-256k-fast.json').read_text())
 args=cfg['args'][:]
 for flag in ['--ple-gguf','--mtp']:
  if flag in args:
   i=args.index(flag);del args[i:i+2]
 for flag,value in [('--pack',str(PACK)),('--native',str(MODEL/entries[0]['name'])),('--max-context','262144')]:args[args.index(flag)+1]=value
 cfg.update(args=args,args_written=args[:],tokenizer=str(PACK/'tokenizer'),model_name='qwen3.8-flash-next-abliterated-v2-ud-iq4-xs',log='F:/AI_Models/Strata/strata-abliterated-iq4-256k.engine.log',mtp=False)
 if PROFILE.exists():raise SystemExit('Prepared profile already exists; preserving any later tuning. Pack preparation completed.')
 PROFILE.write_text(json.dumps(cfg,ensure_ascii=False,indent=2),encoding='utf-8')
 proposal={'model_backend':'strata','model_name':cfg['model_name'],'model_port':8094,'context_size':262144,'min_free_ram_gib':70,'strata_profile':str(PROFILE),'strata_python':'runtime/strata-terminal/0.1.49.2/python/python.exe','strata_cli':'runtime/strata-terminal/0.1.49.2/strata.py','model_files':[{'path':str(MODEL/e['name']),'size':e['size']} for e in entries]}
 OUT.write_text(json.dumps({'prepared':True,'created':time.strftime('%Y-%m-%d %H:%M:%S'),'profile':str(PROFILE),'prompt_model_proposal':proposal,'mtp':'Disabled for initial quality baseline; validate optional base-model MTP separately.','activated':False},ensure_ascii=False,indent=2),encoding='utf-8')
 print('Verified pack/profile prepared. Active studio settings have not been changed.',flush=True)
if __name__=='__main__':main()
