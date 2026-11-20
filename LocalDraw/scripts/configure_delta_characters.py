"""Verify the existing character collection and build selectable Delta Mix workflows."""
import collections
import copy
import hashlib
import importlib.util
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from comic_workflow.comfyui import lora_file_key

OUT = ROOT / 'data/delta-mix-v6/lora-tests'
OUT.mkdir(parents=True, exist_ok=True)


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def main():
    spec = importlib.util.spec_from_file_location('delta_maps', ROOT / 'runtime/custom_nodes/ComfyUI-Anima-Delta-Q35-LoRA/maps_lora.py')
    mapper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mapper)
    overrides = read(ROOT / 'config/anima-lora-trigger-overrides.json')
    catalog = []
    for path in (ROOT / 'data/anima-migration/downloads').glob('*.json'):
        r = read(path)
        if r.get('status') != 'downloaded_verified':
            continue
        selected = r['selected']
        key = lora_file_key(r['relative_path'])
        file = ROOT / 'models/loras' / key
        with file.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            stream.seek(0)
            length = struct.unpack('<Q', stream.read(8))[0]
            header = json.loads(stream.read(length))
        assert digest == selected['sha256'], file
        tensors = {k: v for k, v in header.items() if k != '__metadata__'}
        family = mapper.detect_lora_family(tensors)
        remapped, stats = mapper.remap_lora_keys(tensors, family)
        assert family in ('28', '40', '52') and stats['dropped_unknown'] == 0, file
        assert max(mapper.collect_block_indices(remapped)) <= 51, file
        triggers = overrides.get(str(selected['version_id']), {}).get('words', selected['triggers'])
        character = r['character']
        catalog.append({'label': character['game'] + '｜' + character['name'],
            'character_id': character['id'], 'game': character['game'], 'name': character['name'],
            'lora_name': key, 'triggers': triggers, 'sha256': digest, 'family': family,
            'mapping': stats, 'source': selected['page']})
    catalog.sort(key=lambda r: (r['game'] != '原神', r['name']))
    save(ROOT / 'runtime/custom_nodes/local_draw_delta_characters/catalog.json', catalog)
    save(OUT / 'catalog-verification.json', catalog)
    cases = []
    scene = 'solo, upper body, standing in a peaceful garden, soft daylight, detailed background'
    for character_id, slug in [('10000037', 'ganyu'), ('char_003_kalts', 'kaltsit')]:
        row = next(r for r in catalog if r['character_id'] == character_id)
        api = read(ROOT / 'workflows/delta-mix-v6-api.json')
        api['20'] = {'class_type': 'DeltaMixCharacterLoRA', 'inputs': {
            'model': ['10', 0], 'clip': ['11', 0], 'character': row['label'],
            'strength': 0.65, 'scene': scene, 'use_outfit_tags': True}}
        api['3']['inputs']['model'] = ['20', 0]
        api['6']['inputs'] = {'clip': ['20', 1], 'text': ['20', 2]}
        api['13']['inputs']['clip'] = ['20', 1]
        api['29']['inputs']['filename_prefix'] = 'DeltaMix-characters/' + slug
        ui = read(ROOT / 'workflows/delta-mix-v6-ui.json')
        by = {n['id']: n for n in ui['nodes']}
        by[10]['pos'] = [-900, -240]
        by[11]['pos'] = [-900, 0]
        by[12]['pos'] = [-900, 250]
        node = {'id': 20, 'type': 'DeltaMixCharacterLoRA', 'pos': [-410, -240], 'size': [450, 440],
            'flags': {}, 'order': 3, 'mode': 0,
            'inputs': [{'name': 'model', 'type': 'MODEL', 'link': 2}, {'name': 'clip', 'type': 'CLIP', 'link': 10}],
            'outputs': [{'name': 'model', 'type': 'MODEL', 'links': [11]},
                        {'name': 'clip', 'type': 'CLIP', 'links': [1, 4]},
                        {'name': 'positive_prompt', 'type': 'STRING', 'links': [12]},
                        {'name': 'details', 'type': 'STRING', 'links': []}],
            'properties': {'Node name for S&R': 'DeltaMixCharacterLoRA'},
            'widgets_values': [row['label'], 0.65, scene, True]}
        ui['nodes'].append(node); by[20] = node
        by[11]['outputs'][0]['links'] = [10]
        by[3]['inputs'][0]['link'] = 11
        by[6]['inputs'].append({'name': 'text', 'type': 'STRING', 'widget': {'name': 'text'}, 'link': 12})
        by[6]['widgets_values'] = [', '.join(row['triggers']) + ', ' + scene]
        by[6]['title'] = '角色提示词（自动生成）'
        for link in ui['links']:
            if link[0] == 2: link[3:5] = [20, 0]
            if link[0] in (1, 4): link[1:3] = [20, 1]
        ui['links'].extend([[10, 11, 0, 20, 1, 'CLIP'], [11, 20, 0, 3, 0, 'MODEL'], [12, 20, 2, 6, 1, 'STRING']])
        ui['last_node_id'] = 29; ui['last_link_id'] = 12
        for lid, a, slot, b, dst, typ in ui['links']:
            assert by[b]['inputs'][dst]['link'] == lid and lid in by[a]['outputs'][slot]['links']
        name = 'delta-mix-' + slug + '-lora'
        save(ROOT / 'workflows' / (name + '-api.json'), api)
        save(ROOT / 'workflows' / (name + '-ui.json'), ui)
        save(ROOT / 'runtime/ComfyUI-Anima/user/default/workflows' / ('DeltaMix角色-' + row['name'] + '.json'), ui)
        for strength in (0, 0.65):
            graph = copy.deepcopy(api)
            tag = slug + ('-off' if strength == 0 else '-on')
            graph['20']['inputs']['strength'] = strength
            graph['29']['inputs']['filename_prefix'] = 'DeltaMix-character-test/' + tag
            cases.append({'name': tag, 'character': row['label'], 'strength': strength, 'graph': graph})
    save(OUT / 'cases.json', cases)
    print('Verified', len(catalog), 'LoRAs; families:', dict(collections.Counter(r['family'] for r in catalog)))


if __name__ == '__main__':
    main()
