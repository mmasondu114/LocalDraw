"""Stage the redistributable Windows runtime from the working studio installation."""
import json
import os
from pathlib import Path
import shutil
import sys

APP = Path(__file__).resolve().parents[1]
SOURCE = APP.parent
OUT = Path(sys.argv[1]).resolve()
if OUT.exists():
    raise SystemExit("Choose a new staging directory")
PY = OUT / "runtime/python"
LIB = PY / "Lib/site-packages"
SKIP = {"__pycache__", ".git", ".github", ".pytest_cache", ".ruff_cache", "node_modules"}

def copy_tree(src, dst, exclusions=()):
    skip = SKIP.union(exclusions)
    def ignore(folder, names):
        return [n for n in names if n in skip or n.endswith((".pyc", ".pyo"))]
    shutil.copytree(src, dst, dirs_exist_ok=True, ignore=ignore)

def config(name, data):
    (OUT / "config" / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def read(name):
    return json.loads((APP / "config" / name).read_text(encoding="utf-8-sig"))

base = Path(next(line.split(" = ", 1)[1] for line in (SOURCE / ".venv/pyvenv.cfg").read_text().splitlines() if line.startswith("home = ")))
PY.mkdir(parents=True)
for name in ["python.exe","pythonw.exe","python312.dll","python3.dll","LICENSE.txt","vcruntime140.dll","vcruntime140_1.dll"]:
    shutil.copy2(base/name, PY/name)
copy_tree(base/"DLLs", PY/"DLLs")
copy_tree(base/"Lib", PY/"Lib", {"site-packages", "test", "idlelib", "ensurepip", "tkinter"})
gpu = SOURCE / ".comfy-venv/Lib/site-packages"
overlay = SOURCE / "runtime/kreamoss-venv/Lib/site-packages"
for source in [gpu, overlay]:
    for item in source.iterdir():
        if item.name in SKIP or item.name.startswith(("pip-", "pip~")) or item.name == "pip" or item.suffix in (".pth",".pyc"):
            continue
        if source == gpu and (overlay/item.name).exists():
            continue
        if item.is_dir():
            copy_tree(item, LIB/item.name)
        else:
            shutil.copy2(item, LIB/item.name)
# Add only the app's dependencies missing from the drawing environment.
from importlib.metadata import Distribution
app_lib = SOURCE / ".venv/Lib/site-packages"
for name in ('attrs','jsonschema','jsonschema-specifications','referencing','rpds-py','typing_extensions','Pillow','websocket-client','pypinyin','fontTools','shiboken6','PySide6','PySide6-Essentials','PySide6-Addons'):
    dist = next((d for d in Distribution.discover(path=[str(app_lib)]) if d.metadata['Name'].lower().replace('_','-')==name.lower().replace('_','-')), None)
    if dist is None: continue
    for f in dist.files or []:
        if '..' in f.parts or '__pycache__' in f.parts or f.suffix=='.pyc': continue
        target = LIB/f
        if not target.exists() and (app_lib/f).is_file():
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(app_lib/f,target)
(PY/"python312._pth").write_text(".\nDLLs\nLib\nLib/site-packages\n../ComfyUI-KreaMoss\nimport site\n",encoding='ascii')
crtbase = Path(os.environ['ProgramFiles(x86)']) / 'Microsoft Visual Studio'
crt = next(d for d in crtbase.glob('*/BuildTools/VC/Redist/MSVC/*/x64/Microsoft.VC*.CRT') if d.is_dir())
for dest in (PY,OUT/'runtime'):
    for f in crt.glob('*.dll'): shutil.copy2(f,dest/f.name)
for pattern in ('llama-server.exe','llama-server-impl.dll','llama.dll','llama-common.dll','mtmd.dll','ggml*.dll','cublas*.dll','cudart*.dll','libomp.dll','LICENSE-*'):
    for f in (SOURCE/'runtime').glob(pattern): shutil.copy2(f,OUT/'runtime'/f.name)
for folder in ('ComfyUI-KreaMoss','custom_nodes'):
    copy_tree(APP/'runtime'/folder,OUT/'runtime'/folder)
for folder in ('src','schemas','workflows','comfy_nodes','config'):
    copy_tree(APP/folder,OUT/folder)
(OUT/'scripts').mkdir()
for name in ('copy_prompt_images.ps1','detect_hands.py','comfy_person_mask_node.py','comfy_anime_mask.py','comfy_gpu_hooks_node.py',
             'desktop_release.py'):
    if (APP/'scripts'/name).is_file():
        shutil.copy2(APP/'scripts'/name, OUT/'scripts'/name)
copy_tree(APP/'scripts/vendor/anime_seg', OUT/'scripts/vendor/anime_seg')
for file in ('LocalDraw.exe','LocalDraw.exe.config','launch.py','start_studio.py'):
    shutil.copy2(APP/file,OUT/file)
# Built-in character cards and their references. No workspaces, jobs or browser profiles.
cards = APP/'data/characters'
for file in cards.glob('cards/*/current.json'):
    card=json.loads(file.read_text(encoding='utf-8'))
    if not card.get('source',{}).get('game'): continue
    dst=OUT/'data/characters'/file.relative_to(cards)
    dst.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(file,dst)
    if card.get('image_id'):
        img=cards/'images'/(card['image_id']+'.png')
        if img.is_file():
            dst=OUT/'data/characters/images'/img.name
            dst.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(img,dst)
cfg=read('settings.json')
for key in ('prompt_model','page_redraw_model'): cfg.pop(key,None)
cfg.update(analysis_model="huihui_27b",model_path="models/"+Path(cfg['model_path']).name,runtime_path="runtime/llama-server.exe",auto_fit_vram=True,context_size=16384,batch_size=256,ubatch_size=128)
config('settings.json',cfg)
comfy=read('comfyui.json')
comfy.update(python_path="runtime/python/python.exe",consistency_lora=False)
config('comfyui.json',comfy)
vision=read('vision-model.json');vision['directory']="models/vision";config('vision-model.json',vision)
config('installed-runtime.json',{'pythonw':'runtime/python/pythonw.exe','models':'models'})
for pattern in ('settings.before-*.json','strata-*.json','*model-paths.yaml'):
    for f in (OUT/'config').glob(pattern): f.unlink()
print('Staged',OUT)
