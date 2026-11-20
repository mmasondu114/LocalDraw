"""Archive reviewed two-character library entries without deleting their data."""

import argparse
import copy
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from comic_workflow import engine as e

NAMES = {
    '宝田六花与南梦芽合集', '宇宙公主与月亮公主合集', '姊崎真守与泷铃音合集',
    '闪光的哈萨维空乘双人合集', '塞蕾丝汀与辉夜合集', '哥伦比娅与桑多涅合集',
    '龙女仆双角色合集', '荧与空', '火花与花火合集', '天津亚衣与鬼麿合集',
    '宫岛椿与宫岛樱合集', '芙宁娜与芙卡洛斯', '辉夜与塞蕾丝汀', '芙娃娃与莫可可',
    '咲江与留美', '火芽与水芸合集', '弗蕾妮与弗蕾迪娜合集', '胧与甲河胧合集',
}


def checked(path, parent):
    path, parent = path.resolve(), parent.resolve()
    if not path.is_relative_to(parent) or path == parent:
        raise ValueError('Archive path is outside the expected directory: ' + str(path))
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    root = ROOT.resolve()
    library = root / 'models/loras'
    cards_root = root / 'data/characters/cards'
    labels_path = root / 'data/lora-labels.json'
    labels = e.read(labels_path)
    all_cards = [e.read(p) for p in cards_root.glob('*/current.json')]
    cards = [c for c in all_cards if c['name'] in NAMES]
    ids = {c['id'] for c in cards}
    targets = {k: v for k, v in labels.items() if v.get('character_id') in ids}
    assert all(c['source'].get('asset_kind') == 'collection' for c in cards)
    assert not any(c['name'] in {'哥伦比娅', '桑多涅'} for c in cards)
    if not cards and not targets:
        print('Already archived; no matching active entries.')
        return
    archive = checked(root / 'data/maintenance' / ('duo-library-archive-' + e.stamp()), root / 'data/maintenance')
    records = []
    for filename in targets:
        original = checked(library / filename, library)
        assert original.is_file(), filename
        destination = checked(archive / 'models/loras' / filename, archive)
        assert not destination.exists()
        records.append({'kind': 'lora', 'source': str(original), 'archive': str(destination),
                        'bytes': original.stat().st_size, 'inode': original.stat().st_ino})
    for card in cards:
        original = checked(cards_root / card['id'], cards_root)
        destination = checked(archive / 'data/characters/cards' / card['id'], archive)
        assert original.is_dir() and not original.is_symlink() and not destination.exists()
        assert e.read(original / 'current.json') == card
        records.append({'kind': 'card', 'name': card['name'], 'source': str(original), 'archive': str(destination)})
    state_path = root / 'data/character-expansion-20261031/status.json'
    state = e.read(state_path) if state_path.exists() else None
    updated_state = copy.deepcopy(state)
    if updated_state:
        for item in updated_state.get('files', {}).values():
            if item.get('relative_path') in targets:
                item['status'] = 'user_archived'
                item['archive_reason'] = 'User requested removal of two-character LoRAs and cards'
                item['archive_directory'] = str(archive)
    summary = {'cards': len(cards), 'loras': len(targets),
               'bytes': sum(r.get('bytes', 0) for r in records), 'archive': str(archive)}
    if not args.apply:
        print(json.dumps({**summary, 'names': sorted(c['name'] for c in cards)}, ensure_ascii=False))
        return
    # Do not move weights underneath active rendering or queued sessions.
    status = json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/status', timeout=15))
    assert not any(j.get('status') in {'running', 'queued', 'pausing', 'paused', 'cancelling'} for j in status.get('jobs', []))
    assert not status.get('queue', {}).get('items') and not status.get('queue', {}).get('running')
    assert e.read(labels_path) == labels
    if state is not None:
        assert e.read(state_path) == state
    archive.mkdir(parents=True)
    e.save(archive / 'backup/lora-labels.json', labels)
    if state is not None:
        e.save(archive / 'backup/expansion-status.json', state)
    manifest = {**summary, 'status': 'moving', 'records': records,
                'removed_labels': targets, 'card_names': sorted(c['name'] for c in cards)}
    e.save(archive / 'manifest.json', manifest)
    moved = []
    try:
        for record in records:
            original, destination = Path(record['source']), Path(record['archive'])
            destination.parent.mkdir(parents=True, exist_ok=True)
            original.rename(destination)
            moved.append(record)
            if record['kind'] == 'lora':
                assert destination.stat().st_size == record['bytes']
                assert destination.stat().st_ino == record['inode']
        e.save(labels_path, {k: v for k, v in labels.items() if k not in targets})
        if updated_state is not None:
            e.save(state_path, updated_state)
    except Exception:
        # Same-volume renames are reversible, including complete card history.
        for record in reversed(moved):
            original = checked(Path(record['source']), library if record['kind'] == 'lora' else cards_root)
            destination = checked(Path(record['archive']), archive)
            assert not original.exists()
            destination.rename(original)
        e.save(labels_path, labels)
        if state is not None:
            e.save(state_path, state)
        manifest['status'] = 'rolled_back'
        e.save(archive / 'manifest.json', manifest)
        raise
    remaining = [e.read(p) for p in cards_root.glob('*/current.json')]
    assert not ids.intersection(c['id'] for c in remaining)
    assert all(c in remaining for c in all_cards if c['id'] not in ids)
    assert all(not Path(r['source']).exists() and Path(r['archive']).exists() for r in records)
    manifest['status'] = 'complete'
    manifest['remaining_cards'] = len(remaining)
    manifest['remaining_loras'] = len(labels) - len(targets)
    e.save(archive / 'manifest.json', manifest)
    (archive / '清理清单.txt').write_text(
        f"已归档 {len(cards)} 张双角色卡、{len(targets)} 个 LoRA。文件及完整角色历史可恢复。\n\n"
        + '\n'.join(manifest['card_names']) + '\n\nLoRA 文件：\n' + '\n'.join(targets), encoding='utf-8')
    print(json.dumps({**summary, 'status': 'complete'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
