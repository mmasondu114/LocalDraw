"""Create separate, ready-to-use workflows for the downloaded MOSS adapters."""
import copy
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/moss-loras'


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def main():
    rows = json.loads((OUT / 'manifest.json').read_text(encoding='utf-8'))
    ui_base = json.loads((ROOT / 'workflows/moss-image-sft-bf16-ui.json').read_text())
    api_base = json.loads((ROOT / 'workflows/moss-image-sft-bf16-api.json').read_text())
    records = []
    for index, row in enumerate(rows, 1):
        label = Path(row['path']).stem.split('_')[0]
        lora_name = row['path'].removeprefix('models/loras/').replace('/', os.sep)
        prompt = ((row['trigger'] + ', ') if row['trigger'] else '') + 'an adult traveler in a blue coat beside a quiet mountain lake, sunrise, pine trees, soft golden light'
        api = copy.deepcopy(api_base)
        api['30'] = {'class_type': 'LoraLoaderModelOnly', 'inputs': {'model': ['10', 0], 'lora_name': lora_name, 'strength_model': row['strength']}}
        api['3']['inputs'].update(model=['30', 0], steps=row['steps'], cfg=row['cfg'], sampler_name='euler', scheduler='simple')
        api['6']['inputs']['text'] = prompt
        api['13']['inputs']['text'] = ''
        api['29']['inputs']['filename_prefix'] = f'MOSS-LoRA/{index:02d}'
        ui = copy.deepcopy(ui_base)
        nodes = {n['id']: n for n in ui['nodes']}
        nodes[10]['outputs'][0]['links'] = [10]
        nodes[10]['pos'] = [-600, -520]
        nodes[3]['widgets_values'] = [42, 'randomize', row['steps'], row['cfg'], 'euler', 'simple', 1]
        nodes[6]['widgets_values'] = [prompt]
        nodes[13]['widgets_values'] = ['']
        nodes[29]['widgets_values'] = [f'MOSS-LoRA/{index:02d}']
        ui['nodes'].append({'id': 30, 'type': 'LoraLoaderModelOnly', 'title': label + ' · MOSS SFT',
                            'pos': [-210, -520], 'size': [430, 100], 'flags': {}, 'order': 1, 'mode': 0,
                            'inputs': [{'name': 'model', 'type': 'MODEL', 'link': 10}],
                            'outputs': [{'name': 'MODEL', 'type': 'MODEL', 'links': [2]}],
                            'properties': {'Node name for S&R': 'LoraLoaderModelOnly'},
                            'widgets_values': [lora_name, row['strength']]})
        next(link for link in ui['links'] if link[0] == 2)[1] = 30
        ui['links'].append([10, 10, 0, 30, 0, 'MODEL'])
        ui['last_node_id'], ui['last_link_id'] = 30, 10
        if 'Impressionism' in row['filename'] or 'impressionism' in row['filename']:
            api['31'] = {'class_type': 'SkipLayerGuidanceDiTSimple', 'inputs': {
                'model': ['30', 0], 'double_layers': '', 'single_layers': '22', 'start_percent': 0.0, 'end_percent': 1.0}}
            api['3']['inputs']['model'] = ['31', 0]
            ui['nodes'][-1]['outputs'][0]['links'] = [11]
            ui['nodes'].append({'id': 31, 'type': 'SkipLayerGuidanceDiTSimple', 'pos': [260, -520],
                                'size': [340, 170], 'flags': {}, 'order': 2, 'mode': 0,
                                'inputs': [{'name': 'model', 'type': 'MODEL', 'link': 11}],
                                'outputs': [{'name': 'MODEL', 'type': 'MODEL', 'links': [2]}],
                                'properties': {'Node name for S&R': 'SkipLayerGuidanceDiTSimple'},
                                'widgets_values': ['', '22', 0.0, 1.0]})
            next(link for link in ui['links'] if link[0] == 2)[1] = 31
            ui['links'].append([11, 30, 0, 31, 0, 'MODEL'])
            ui['last_node_id'], ui['last_link_id'] = 31, 11
        stem = f'{index:02d}-{label}'
        save(ROOT / 'workflows/MOSS-LoRA' / (stem + '-ui.json'), ui)
        save(ROOT / 'workflows/MOSS-LoRA' / (stem + '-api.json'), api)
        save(ROOT / 'runtime/ComfyUI-KreaMoss/user/default/workflows/MOSS-LoRA' / (stem + '.json'), ui)
        records.append({'label': label, 'api': 'workflows/MOSS-LoRA/' + stem + '-api.json',
                        'ui': 'workflows/MOSS-LoRA/' + stem + '-ui.json', 'lora': lora_name})
    save(OUT / 'workflows.json', records)
    print('Prepared', len(records), 'workflows')


if __name__ == '__main__':
    main()
