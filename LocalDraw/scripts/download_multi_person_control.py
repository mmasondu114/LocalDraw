"""Fetch the ControlNet used in the published LoRA Pipeline example."""
import argparse
import importlib.util
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from comic_workflow import engine as e


def main():
    spec = importlib.util.spec_from_file_location('downloader', ROOT/'Chenkin_LoRA/download.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    module.ROOT = ROOT/'models/controlnet'
    module.ARIA_EXE = ROOT/'Chenkin_LoRA/tools/aria2-1.36.0/aria2c.exe'
    module.ROOT.mkdir(parents=True, exist_ok=True)
    cfg = e.read(ROOT/'config/multi-person-control.json')
    entry = {'url':cfg['url'],'relative_path':cfg['name'],'sha256':cfg['sha256']}
    token = (ROOT/'Chenkin_LoRA/civitai-token.txt').read_text().strip()
    proxy = urllib.request.getproxies().get('https')
    pending = module.pending_files([entry])
    if pending:
        result = module.download_with_retries([entry],pending,token,argparse.Namespace(jobs=1,connections=8),proxy)
        if result[1]: return 1
    path = module.ROOT/cfg['name']
    e.need(module.digest(path)==cfg['sha256'],'姿势控制模型校验失败')
    cfg['size'] = path.stat().st_size
    e.save(ROOT/'config/multi-person-control.json', cfg)
    print('姿势控制模型已通过 SHA256 校验',flush=True)
    return 0


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
