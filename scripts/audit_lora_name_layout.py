"""Record the actual active filename layout and search identity metadata."""
from collections import Counter
import datetime
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from comic_workflow import engine as e
from comic_workflow.lora_inspection import inspect_local


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    base = ROOT / 'models/loras'
    files = list(base.rglob('*.safetensors'))
    games = Counter()
    violations = []
    for path in files:
        rel = path.relative_to(base)
        games[rel.parts[0]] += 1
        if len(rel.parts) != 2 or rel.parts[0] not in ('明日方舟', '原神') or '_' not in path.stem or not path.stem.split('_', 1)[0]:
            violations.append(rel.as_posix())
    mismatches = []
    for row in e.read(ROOT / 'data/lora-curation/selection.json')['characters']:
        chosen = row['selected']
        if chosen:
            expected = row['character']['game'] + '/' + row['character']['name'] + '_' + chosen['original_name']
            if chosen['relative_path'] != expected or not (base / expected).is_file():
                mismatches.append({'name': row['character']['name'], 'expected': expected, 'actual': chosen['relative_path']})
    metadata = inspect_local()
    missing = [name for name, record in metadata.items() if not record.get('english_aliases')]
    latin = sorted({p.name.split('_', 1)[0] for p in files if re.search('[A-Za-z]', p.name.split('_', 1)[0])})
    result = {'checked_at': datetime.datetime.now().isoformat(timespec='seconds'),
              'weights': len(files), 'games': dict(games), 'layout_violations': violations,
              'selected_original_filename_mismatches': mismatches, 'missing_english_aliases': missing,
              'official_names_with_latin_characters': latin}
    e.save(ROOT / 'data/test-results/lora-name-layout-final.json', result)
    e.need(not violations and not mismatches and not missing, '文件布局、原始文件名或英文别名检查未通过')
    print(f"{len(files)} active LoRA filenames and identity aliases verified", flush=True)


if __name__ == '__main__':
    main()
