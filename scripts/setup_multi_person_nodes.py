"""Install the unmodified, pinned components of the author's published workflow."""
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]


def main():
    cfg=json.loads((ROOT/'config/multi-person-workflow.json').read_text(encoding='utf-8'))
    for component in cfg['components']:
        destination=ROOT/'runtime/custom_nodes'/component['name']
        if not destination.exists():
            subprocess.run(['git','clone',component['repository'],str(destination)],check=True)
            subprocess.run(['git','-C',str(destination),'checkout','--detach',component['revision']],check=True)
        actual=subprocess.check_output(['git','-C',str(destination),'rev-parse','HEAD'],text=True).strip()
        if actual!=component['revision']:
            raise RuntimeError('已安装的组件版本不同，请保留本地修改并核对：'+component['name'])
        print(component['name']+' 已核实',flush=True)


if __name__=='__main__':main()
