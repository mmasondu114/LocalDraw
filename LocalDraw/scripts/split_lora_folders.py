"""Move the installed library into model folders and preserve historical aliases."""
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def migrate(root=ROOT):
    root = root.resolve()
    library = (root / 'models/loras').resolve()
    assert library.is_relative_to(root)
    moves = []
    mapping = {}
    for source in sorted(library.rglob('*')):
        if not source.is_file():
            continue
        relative = source.relative_to(library)
        if relative.parts[0] in ('sdxl', 'anime'):
            continue
        target_relative = (Path('anime', *relative.parts[1:]) if relative.parts[0] == 'Anima'
                           else Path('sdxl') / relative)
        target = (library / target_relative).resolve()
        assert source.resolve().is_relative_to(library) and target.is_relative_to(library)
        if target.exists():
            raise ValueError(f'Refusing to overwrite: {target}')
        moves.append({'old': relative.as_posix(), 'new': target_relative.as_posix(), 'bytes': source.stat().st_size})
        for old, new in [(relative.as_posix(), target_relative.as_posix()),
                         ('models/loras/' + relative.as_posix(), 'models/loras/' + target_relative.as_posix()),
                         (source.as_posix(), target.as_posix())]:
            mapping[old] = new
            mapping[old.replace('/', '\\')] = new.replace('/', '\\')
    if not moves:
        return {'moves': [], 'status': 'already_migrated'}
    backup = root / 'data/backups' / ('lora-folders-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    backup.mkdir(parents=True)
    def read(path):
        return json.loads(path.read_text(encoding='utf-8'))
    def save(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    def rewrite(value):
        if isinstance(value, dict):
            return {mapping.get(k, k): rewrite(v) for k, v in value.items()}
        if isinstance(value, list):
            return [rewrite(v) for v in value]
        return mapping.get(value, value) if isinstance(value, str) else value
    alias_path = root / 'data/lora-curation/rename-result.json'
    aliases = read(alias_path) if alias_path.exists() else {}
    # Flatten earlier renames so even the oldest saved selections resolve in one lookup.
    aliases['mapping'] = {**{k: mapping.get(v, v) for k, v in aliases.get('mapping', {}).items()}, **mapping}
    save(backup / 'journal.json', moves)
    references = [root / 'data/lora-labels.json', root / 'data/lora-load-tests.json',
                  root / 'data/anima-migration/coverage.json',
                  root / 'data/lora-curation/selection.json']
    references += list((root / 'data/projects').rglob('drawing-settings.json'))
    references += list((root / 'config').glob('*.json'))
    changed = []
    for path in references:
        if not path.exists():
            continue
        original = read(path)
        updated = rewrite(original)
        if original != updated:
            dest = backup / path.relative_to(root)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
            save(path, updated)
            changed.append(path.relative_to(root).as_posix())
    if alias_path.exists():
        shutil.copy2(alias_path, backup / 'rename-result.json')
    save(alias_path, aliases)
    for move in moves:
        source, target = library / move['old'], library / move['new']
        target.parent.mkdir(parents=True, exist_ok=True)
        source.rename(target)
        assert target.stat().st_size == move['bytes']
    for path in sorted(library.rglob('*'), key=lambda p: len(p.parts), reverse=True):
        if path.is_dir() and path.relative_to(library).parts[0] not in ('sdxl', 'anime') and not any(path.iterdir()):
            path.rmdir()
    result = {'status': 'complete', 'moves': moves, 'changed_references': changed, 'backup': str(backup),
              'counts': {name: len(list((library / name).rglob('*.safetensors'))) for name in ('sdxl', 'anime')}}
    save(root / 'data/lora-curation/folder-migration.json', result)
    return result


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    result = migrate()
    print(json.dumps({k: v for k, v in result.items() if k != 'moves'}, ensure_ascii=False, indent=2))
