"""Launch the configured Delta Mix ComfyUI workflow, or verify it on the GPU."""
import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from comic_workflow.processes import attach_exit_job, close_job, terminate_tree

OUT = ROOT / 'data/delta-mix-v6'
ENDPOINT = 'http://127.0.0.1:8190'


def api(path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(ENDPOINT + path, data, {'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def verify_graph(graph, destination):
    destination.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    submitted = api('/prompt', {'prompt': graph})
    job = submitted['prompt_id']
    (destination / 'submitted.json').write_text(json.dumps(submitted, indent=2), encoding='utf-8')
    while time.monotonic() - started < 1200:
        record = api('/history/' + job).get(job)
        if record:
            break
        time.sleep(2)
    else:
        raise TimeoutError('GPU verification timed out')
    (destination / 'history.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
    if record['status']['status_str'] != 'success':
        raise RuntimeError('GPU verification failed; inspect ' + str(destination / 'history.json'))
    entry = record['outputs']['29']['images'][0]
    with urllib.request.urlopen(ENDPOINT + '/view?' + urllib.parse.urlencode(entry), timeout=30) as response:
        (destination / 'preview.png').write_bytes(response.read())
    sampler = graph['3']['inputs']
    report = {'status': 'passed', 'prompt_id': job, 'seconds': round(time.monotonic() - started, 2),
        'width': graph['5']['inputs']['width'], 'height': graph['5']['inputs']['height'],
        'steps': sampler['steps'], 'cfg': sampler['cfg'], 'sampler': sampler['sampler_name'],
        'scheduler': sampler['scheduler'], 'custom_nodes_required': '20' in graph}
    (destination / 'verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


def main(verify=False, character_tests=False):
    OUT.mkdir(parents=True, exist_ok=True)
    ready = ROOT / 'models/diffusion_models/animaDeltaMix38BQwen0_deltaMixV60.safetensors'
    if not ready.is_file():
        raise RuntimeError('Delta Mix v6 model has not finished downloading.')
    paths = ROOT / 'config/delta-mix-model-paths.yaml'
    paths.write_text('delta_mix:\n  base_path: ' + json.dumps(str(ROOT / 'models')) +
        '\n  checkpoints: checkpoints\n  diffusion_models: diffusion_models\n  text_encoders: text_encoders\n  vae: vae\n  loras: loras\n' +
        'delta_nodes:\n  base_path: ' + json.dumps(str(ROOT / 'runtime')) + '\n  custom_nodes: custom_nodes\n', encoding='utf-8')
    process = None
    process_job = None
    log = (OUT / 'comfyui.log').open('a', encoding='utf-8')
    try:
        try:
            api('/system_stats')
        except (OSError, urllib.error.URLError):
            process = subprocess.Popen([
                str(ROOT / '.comfy-venv/Scripts/python.exe'),
                str(ROOT / 'runtime/ComfyUI-Anima/main.py'),
                '--listen', '127.0.0.1', '--port', '8190',
                '--reserve-vram', '3',
                '--disable-all-custom-nodes', '--whitelist-custom-nodes',
                'ComfyUI-Anima-Delta-Q35-LoRA', 'local_draw_delta_characters', '--disable-api-nodes',
                '--extra-model-paths-config', str(paths),
            ], cwd=ROOT / 'runtime/ComfyUI-Anima', stdout=log, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            process_job = attach_exit_job(process)
            for _ in range(90):
                if process.poll() is not None:
                    raise RuntimeError('ComfyUI startup failed; inspect comfyui.log')
                try:
                    api('/system_stats')
                    break
                except (OSError, urllib.error.URLError):
                    time.sleep(2)
            else:
                raise TimeoutError('ComfyUI startup timed out')
        if not verify:
            webbrowser.open(ENDPOINT)
            if process:
                process.wait()
            return
        queue = api('/queue')
        if queue.get('queue_running') or queue.get('queue_pending'):
            raise RuntimeError('ComfyUI has existing jobs; verification was not submitted')
        if character_tests:
            suite = OUT / 'lora-tests'
            cases = json.loads((suite / 'cases.json').read_text(encoding='utf-8'))
            results = []
            for case in cases:
                result = verify_graph(case['graph'], suite / case['name'])
                results.append({**result, **{k: v for k, v in case.items() if k != 'graph'}})
                (suite / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
        else:
            graph = json.loads((ROOT / 'workflows/delta-mix-v6-api.json').read_text(encoding='utf-8'))
            verify_graph(graph, OUT)
    finally:
        terminate_tree(process)
        close_job(process_job)
        log.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--verify', action='store_true')
    parser.add_argument('--verify-characters', action='store_true')
    args = parser.parse_args()
    try:
        main(args.verify or args.verify_characters, args.verify_characters)
    except Exception as exc:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / 'failure.json').write_text(json.dumps({'error': str(exc), 'type': type(exc).__name__}), encoding='utf-8')
        raise
