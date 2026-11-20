"""Read-only final disk, SHA256, ownership, and character coverage audit."""
import collections
import datetime
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
from comic_workflow import engine as e
from comic_workflow.anima import CHECKPOINT
from comic_workflow.lora_inspection import inspect_lora
import sync_lora_chinese_library as naming


def main():
    base = ROOT / 'data/character-expansion-20261031'
    lib = ROOT / 'models/loras'
    state = e.read(base / 'status.json')
    labels = e.read(ROOT / 'data/lora-labels.json')
    cards = {c['id']: c for c in naming.CharacterLibrary().listing()}
    cfg = e.read(naming.CONFIG)
    problems = []
    verified = []
    partials = []
    for row in state['files'].values():
        path = lib / row['relative_path']
        if row['status'] not in ('verified', 'failed'):
            continue
        if row['status'] == 'failed':
            path = path.with_suffix('.safetensors.partial')
            if not path.exists():
                partials.append({'file':row['relative_path'], 'present':False, 'error':row.get('last_error')})
                continue
        result = {'file':row['relative_path'], 'present':path.exists()}
        if path.exists():
            result['bytes'] = path.stat().st_size
            with path.open('rb') as stream:
                result['sha256_matches'] = hashlib.file_digest(stream, 'sha256').hexdigest() == row['sha256']
            try:
                inspection = inspect_lora(path, checkpoint=CHECKPOINT)
                result['family'] = inspection['family']
                result['structure_valid'] = inspection['family'] == ('Anima' if row['relative_path'].startswith('anime/') else 'SDXL')
            except Exception as exc:
                result['structure_valid'] = False
                result['inspection_error'] = str(exc)[:300]
        if row['status'] == 'verified':
            verified.append(result)
            if not (result.get('sha256_matches') and result.get('structure_valid')):
                problems.append(result)
            if len(verified) % 300 == 0:
                print('AUDITED', len(verified), flush=True)
        else:
            partials.append(result)
    actual = list(lib.rglob('*.safetensors'))
    ownership = []
    by_name = collections.defaultdict(list)
    for path in actual:
        rel = path.relative_to(lib).as_posix()
        label = labels.get(rel, {})
        card = cards.get(label.get('character_id'))
        if not (card and naming.CN.fullmatch(label.get('name','')) and path.stem.startswith(label['name'] + '_版本')
                and label.get('original_filename') and label.get('chinese_identity_verified')
                and rel in card.get('source',{}).get('lora_files',[])):
            ownership.append(rel)
        if label.get('asset_kind') == 'character':
            aliases = {label.get('name',''),label.get('character',''),label.get('original_display_name','')}
            aliases.add(Path(label.get('original_filename','')).stem.split('_')[0])
            if card:
                aliases.update(card.get('source',{}).get('aliases',[]))
            for name in aliases:
                by_name[name].append(rel)
    coverage = []
    for op in e.read(base / 'roster.json'):
        aliases = {op['name'],op['english'],naming.translate_name(op['name'],cfg),*op.get('aliases',[])}
        paths = sorted({p for name in aliases for p in by_name[name]})
        coverage.append({'character':op,'anima':[p for p in paths if p.startswith('anime/')],
                         'sdxl':[p for p in paths if p.startswith('sdxl/')]})
    bands = {name:by_name[name] for name in ['丰川祥子','若叶睦','三角初华','八幡海铃','祐天寺若麦','井芹仁菜','河原木桃香','安和昴','海老冢智','鲁帕']}
    report = {'checked_at':datetime.datetime.now().astimezone().isoformat(), 'state_status':state['status'],
              'verified_count':len(verified),'verified_bytes':sum(x.get('bytes',0) for x in verified),
              'reserved_bytes':state['reserved_bytes'],'limit_bytes':state['limit_bytes'],
              'hash_structure_problems':problems,'verified_files':verified,'failed_partials':partials,
              'all_complete_files':len(actual),'all_complete_bytes':sum(p.stat().st_size for p in actual),
              'ownership_problems':ownership,'pending':naming.plan()['pending'],
              'arknights':coverage,'bands':bands}
    e.save(base / 'final-disk-audit.json',report)
    print(json.dumps({k:v for k,v in report.items() if k not in ('verified_files','failed_partials','arknights','bands','pending')},ensure_ascii=False),flush=True)
    print('RECOVERABLE_PARTIALS',sum(x.get('sha256_matches') and x.get('structure_valid') for x in partials if x.get('present',True)),flush=True)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
