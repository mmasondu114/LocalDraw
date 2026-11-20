"""Exercise the exported native graph, including bypassed and active LoRA nodes."""
import copy
import json
import time
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/expanded-comfy-verification'
INFO = json.loads((ROOT / 'data/comfy-node-info.json').read_text(encoding='utf-8-sig'))
TEMPLATES = json.loads((ROOT / 'comfy_nodes/local_draw_unified/web/expanded-templates.json').read_text(encoding='utf-8'))


def api(path, body=None):
    return json.load(urlopen(Request('http://127.0.0.1:8189/' + path,
        data=None if body is None else json.dumps(body).encode(), headers={'Content-Type': 'application/json'})))


def prompt_from_graph(graph, active_lora=False):
    nodes = {n['id']: n for n in copy.deepcopy(graph['nodes'])}
    links = {l[0]: l for l in graph['links']}
    if active_lora:
        for i in (20, 21, 22):
            nodes[i]['mode'] = 0
    def resolve(id, slot):
        n = nodes[id]
        if n['mode'] != 4:
            return [str(id), slot]
        typ = n['outputs'][slot]['type']
        input = next(p for p in n['inputs'] if p['type'] == typ)
        l = links[input['link']]
        return resolve(l[1], l[2])
    prompt = {}
    for id, n in nodes.items():
        if n['type'] in ('Note', 'LocalDrawExpandedControls') or n['mode'] == 4:
            continue
        data, values = {}, iter(n['widgets_values'])
        for section in ('required', 'optional'):
            for name, spec in INFO[n['type']]['input'].get(section, {}).items():
                typ = spec[0]
                if isinstance(typ, list) or typ in ('INT', 'FLOAT', 'STRING', 'BOOLEAN', 'COMBO'):
                    data[name] = next(values)
                    if name == 'seed':
                        next(values)
                else:
                    inp = next(p for p in n['inputs'] if p['name'] == name)
                    if inp['link'] is not None:
                        link = links[inp['link']]
                        data[name] = resolve(link[1], link[2])
        prompt[str(id)] = {'class_type': n['type'], 'inputs': data}
    prompt['40']['inputs'].update(width=512, height=512, batch_size=1)
    prompt['41']['inputs'].update(steps=2)
    prompt['43']['inputs']['filename_prefix'] = 'native-graph/' + graph['nodes'][0]['widgets_values'][0].replace(' ', '_')
    return prompt


if __name__ == '__main__':
    results = []
    for label, graph in TEMPLATES.items():
        active = label in ('Anima 3.8B v1.1', 'Anima Aesthetic v1.1', 'Anima Delta Mix v6', 'Krea2 Turbo', 'Illustrious XL v2.0')
        queued = api('prompt', {'prompt': prompt_from_graph(graph, active)})
        id = queued['prompt_id']
        started = time.monotonic()
        while time.monotonic() - started < 240:
            history = api('history/' + id)
            if id in history:
                break
            time.sleep(1)
        item = history[id]
        (OUT / (label.replace(' ', '_') + '.json')).write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding='utf-8')
        ok = item['status']['status_str'] == 'success' and bool(item['outputs'].get('43', {}).get('images'))
        results.append({'model': label, 'success': ok, 'loras': 3 if active else 0})
        print(json.dumps(results[-1], ensure_ascii=False), flush=True)
        if not ok:
            print(item['status'], flush=True)
            break
    (OUT / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
