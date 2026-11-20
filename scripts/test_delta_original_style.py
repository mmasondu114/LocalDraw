"""Four opt-in original-style probes; isolated Comfy process, no studio mutation."""
import copy
import hashlib
import json
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'output/anima-original-style-20261026'
STATE = ROOT / 'data/consistency-upgrade-20261025/state.json'
BASE = 'http://127.0.0.1:8190'
sys.path.insert(0, str(ROOT / 'src'))
from comic_workflow.processes import attach_exit_job, close_job, terminate_tree


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temp.replace(path)


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def api(path, body=None, base=BASE):
    req = urllib.request.Request(base + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def prepare():
    target = read(ROOT / 'data/consistency-upgrade-20261025/historical-ten-group-style-target.json')
    source = ROOT / 'output/delta-v6-character-collection-20261024/05_keqing_ganyu_liyue/02-workflow.json'
    original = read(source)
    project = read(ROOT / 'data/projects/story-550516211421/project.json')
    panels = {p['id']: p for page in project['pages'].values() for p in page['panels']}
    identity = ('1girl, solo, Keqing (genshin impact), one adult woman. Long lavender-purple twin tails, '
        'two pointed cone-shaped hair buns made of purple hair, purple eyes, gold-edged blue diamond earrings. '
        'Canonical ornate purple dress, white and lavender chest panel, dark purple neck ribbon, '
        'detached purple sleeves, black gloves and black tights. Recognizable Keqing face and default costume. ')
    ids = ['P001-K01', 'P002-K02', 'P004-K01', 'P006-K02']
    cases = []
    for index, pid in enumerate(ids, 1):
        panel = panels[pid]
        framing = panel['shot'] + ', ' + panel['angle'] + '. ' + panel['main_instant']
        if pid == 'P002-K02':
            framing += ' Strict face profile facing RIGHT, only one eye visible. Frame from full head to waist; hands and legs outside the image.'
        if pid == 'P006-K02':
            framing += ' Face and shoulders fill the image; full top of the head inside the frame.'
        positive = (target['style_positive'] + '\n\n' + identity + '\n\n' + framing + '\n\n'
            + panel['background'] + ' Liyue Harbor at night, cool blue ambient light and restrained warm amber lamplight.')
        graph = copy.deepcopy(original)
        graph['6']['inputs']['text'] = positive
        graph['13']['inputs']['text'] = target['style_negative'] + ', second person, horns, animal ears'
        graph['3']['inputs']['seed'] = 2610260100 + index
        graph['29']['inputs']['filename_prefix'] = f'{index:02d}-{pid}'
        assert len([n for n in graph.values() if 'Lora' in n['class_type']]) == 0
        path = OUT / f'{index:02d}-{pid}-workflow.json'
        if path.exists() and read(path) != graph:
            raise RuntimeError('Existing frozen workflow differs; use a new round.')
        save(path, graph)
        cases.append({'index': index, 'panel_id': pid, 'workflow': str(path), 'panel': panel})
    experiment = {'purpose': 'Restore historical 80-image collection style before testing new conditioning.',
        'model': target['settings']['model'], 'config': target['settings'],
        'reference_image': target['sample'],
        'reference_sha256': hashlib.sha256(Path(target['sample']).read_bytes()).hexdigest(),
        'reference_use': 'Visual acceptance standard only; no image conditioning in this baseline.',
        'loras': [], 'image_count': 4, 'cases': cases,
        'production_project_unchanged': True, 'visual_acceptance': 'pending_actual_review'}
    save(OUT / 'experiment.json', experiment)
    return cases


def guard():
    llm = read(ROOT / 'data/strata-setup/abliterated-iq4-256k-result.json')
    if not (llm.get('state') == 'completed' and llm.get('integrated') is True and llm.get('resources_released') is True):
        raise RuntimeError('LLM completion gate not satisfied.')
    status = api('/api/status', base='http://127.0.0.1:8765')
    if status.get('queue', {}).get('running') or status.get('queue', {}).get('items'):
        raise RuntimeError('Studio queue is occupied.')
    if any(j.get('status') in {'running', 'queued', 'paused', 'pausing', 'cancelling'} for j in status.get('jobs', [])):
        raise RuntimeError('Studio jobs still active.')
    for port in [8188, 8190, 8192, 8094]:
        with socket.socket() as s:
            s.settimeout(0.3)
            if s.connect_ex(('127.0.0.1', port)) == 0:
                raise RuntimeError(f'Port {port} is occupied; do not interrupt another service.')
    free = subprocess.check_output(['nvidia-smi','--query-gpu=memory.free','--format=csv,noheader,nounits'], text=True)
    if int(free.strip().splitlines()[0]) < 18000:
        raise RuntimeError('GPU resources not free.')


def run(cases):
    guard()
    process = job = None
    results = []
    s = read(STATE)
    s.update(phase='running_original_style_baseline', continuation_hold=False,
        active_submission=str(OUT / 'experiment.json'), active_job='isolated-delta-style-20261026')
    s['llm_wait_check'] = {'state':'completed','integrated':True,'resources_released':True,
        'evidence':'LLM final report and thread confirm completion; studio idle, GPU >18GB free.'}
    save(STATE, s)
    try:
        with (OUT / 'comfy.log').open('w', encoding='utf-8') as log:
            process = subprocess.Popen([
                str(ROOT / 'runtime/kreamoss-venv/Scripts/python.exe'),
                str(ROOT / 'runtime/ComfyUI-KreaMoss/main.py'),
                '--listen','127.0.0.1','--port','8190','--reserve-vram','3',
                '--disable-all-custom-nodes','--whitelist-custom-nodes',
                'ComfyUI-Anima-Delta-Q35-LoRA','local_draw_delta_characters',
                '--disable-api-nodes','--disable-auto-launch',
                '--extra-model-paths-config',str(ROOT / 'config/comfy-model-paths.yaml'),
                '--output-directory',str(OUT / 'raw'),
                '--input-directory',str(OUT / 'input')],
                cwd=ROOT / 'runtime/ComfyUI-KreaMoss',stdout=log,stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW)
            job = attach_exit_job(process)
            save(OUT / 'process.json', {'pid': process.pid, 'owned_by':'test_delta_original_style.py','port':8190})
            for _ in range(120):
                if process.poll() is not None:
                    raise RuntimeError('Owned Comfy process exited during startup.')
                try:
                    api('/system_stats')
                    break
                except OSError:
                    time.sleep(1)
            else:
                raise TimeoutError('Comfy startup timed out.')
            for case in cases:
                label = f"{case['index']:02d}-{case['panel_id']}"
                result_path = OUT / (label + '-result.json')
                if result_path.exists():
                    results.append(read(result_path))
                    continue
                submitted_path = OUT / (label + '-submitted.json')
                if submitted_path.exists():
                    raise RuntimeError('Prior submission exists without result; inspect before resubmitting.')
                started = time.monotonic()
                graph = read(Path(case['workflow']))
                submitted = api('/prompt', {'prompt':graph, 'client_id':'original-style-20261026'})
                save(submitted_path, submitted)
                prompt_id = submitted['prompt_id']
                print('Submitted ' + label + ' ' + prompt_id, flush=True)
                while time.monotonic()-started < 1200:
                    if process.poll() is not None:
                        raise RuntimeError('Owned Comfy process exited during generation.')
                    history = api('/history/' + prompt_id).get(prompt_id)
                    if history:
                        break
                    time.sleep(2)
                else:
                    raise TimeoutError('Generation timed out; inspect frozen submission.')
                save(OUT / (label + '-history.json'), history)
                if history['status']['status_str'] != 'success':
                    raise RuntimeError('Generation failed; inspect saved history.')
                entry = history['outputs']['29']['images'][0]
                with urllib.request.urlopen(BASE + '/view?' + urllib.parse.urlencode(entry), timeout=30) as response:
                    content = response.read()
                image = OUT / (label + '.png')
                image.write_bytes(content)
                from PIL import Image
                with Image.open(image) as im:
                    assert im.size == (2048,1152)
                    im.verify()
                result = {'panel_id':case['panel_id'],'image':str(image),'sha256':hashlib.sha256(content).hexdigest(),
                    'seed':graph['3']['inputs']['seed'],'prompt_id':prompt_id,'seconds':round(time.monotonic()-started,2),
                    'technical_status':'success','visual_status':'pending_actual_review','workflow':case['workflow']}
                save(result_path,result)
                results.append(result)
                save(OUT / 'results.json',results)
                print('Completed ' + label, flush=True)
        s = read(STATE)
        s.update(phase='original_style_baseline_pending_visual_review',active_job=None,active_submission=None)
        s['baseline_results'] = str(OUT / 'results.json')
        save(STATE,s)
    except Exception as exc:
        save(OUT / 'failure.json', {'error':str(exc),'type':type(exc).__name__})
        s = read(STATE)
        s.update(phase='original_style_baseline_error',active_job=None,active_submission=None)
        save(STATE,s)
        raise
    finally:
        terminate_tree(process)
        close_job(job)


if __name__ == '__main__':
    cases = prepare()
    if '--run' in sys.argv:
        run(cases)
    else:
        print('Prepared four frozen workflows; pass --run to execute after the LLM gate.')
