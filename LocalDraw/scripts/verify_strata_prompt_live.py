"""Exercise the studio's real prompt action and record its loaded backend."""
import argparse
import json
import time
import urllib.request
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--output', default='data/strata-setup/prompt-integration-256k.json')
parser.add_argument('--expected-model')
args = parser.parse_args()

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
base = 'http://127.0.0.1:8765'
def get(url):
    with opener.open(url, timeout=5) as response:
        return json.load(response)

token = get(base + '/api/bootstrap')['token']
body = dict(action='prompt_translate', text='一个成年女性，蓝色长发，白色连衣裙，站在雨夜车站的屋檐下，手里拿着一把收起的红伞。全身，单人。',
            negative='文字，水印', characters=[])
request = urllib.request.Request(base+'/api/action', data=json.dumps(body).encode(),
    headers={'Content-Type':'application/json', 'X-Studio-Token':token, 'Origin':base})
with opener.open(request, timeout=10) as response:
    submitted = json.load(response)
job_id = submitted['job']
health = None
last = None
deadline = time.monotonic() + 300
while time.monotonic() < deadline:
    state = get(base+'/api/status')
    job = next(j for j in state['jobs'] if j['id'] == job_id)
    current = (job['status'], job.get('message'))
    if current != last:
        print(json.dumps({'status':current[0], 'message':current[1]}, ensure_ascii=False), flush=True)
        last = current
    try:
        health = get('http://127.0.0.1:8094/health')
    except OSError:
        pass
    if job['status'] in ('done', 'failed', 'cancelled'):
        break
    time.sleep(1)
else:
    raise RuntimeError('Prompt verification timed out')
result = {'health_while_loaded':health, 'job':job, 'model_after':state['model']}
Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
assert job['status'] == 'done', job
assert health and health['max_context'] == 262144 and health['loaded'], health
if args.expected_model:
    assert health['model'] == args.expected_model, health
assert not state['model']['owned'], state['model']
print(json.dumps(result, ensure_ascii=False), flush=True)
