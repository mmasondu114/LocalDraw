"""Isolated 500-LoRA UI fixture: no user data writes and no GPU calls."""
import sys
from pathlib import Path
from unittest.mock import patch
from http.server import ThreadingHTTPServer
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from comic_workflow import engine as e, comfyui
from comic_workflow.server import Application, make_handler
from comic_workflow.repository import Repository
from comic_workflow.characters import CharacterLibrary, DETAILS
root=Path(__file__).resolve().parents[1]/'data/test-results/lora-ui-fixture'
root.mkdir(parents=True,exist_ok=True)
repo=Repository(root/'projects')
library=CharacterLibrary(root/'characters')
if not library.listing():
 for name in ['测试角色甲','测试角色乙']:
  profile={k:'详细测试资料，仅用于界面验证。' for k in DETAILS}
  profile.update(observed=[],suggested=[],unknowns=[])
  library.save(name,profile,{'mode':'text'})
if not repo.listing(): repo.create(None,True)
files=[f'{folder}/角色{i:03}.safetensors' for folder in ['genshin','arknights'] for i in range(250)]
info={f:{'structurally_compatible':True,'note':'离线假文件，无实际权重','family':'fixture'} for f in files}
def no_inference(*a,**k): raise ValueError('离线验收：未连接任何模型')
with patch('comic_workflow.jobs.DATA',root),patch('comic_workflow.comfyui.DATA',root),patch('comic_workflow.characters.DATA',root),patch('comic_workflow.comfyui.discover',return_value={'ready':False,'checkpoints':['fixture.safetensors'],'configured':'fixture.safetensors','loras':files}),patch('comic_workflow.server.inspect_local',return_value=info):
 app=Application(repo)
 app.prepare_language=no_inference;app.model.start=no_inference;app.comfy.start=no_inference
 app.model.stop=lambda:None;app.comfy.stop=lambda:None
 app.model.status=lambda:{'ready':False,'owned':False,'generating':False,'file_size_ok':True}
 print('LoRA offline fixture http://127.0.0.1:8787',flush=True)
 ThreadingHTTPServer(('127.0.0.1',8787),make_handler(app)).serve_forever()
