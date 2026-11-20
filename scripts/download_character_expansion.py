"""Resumable, hash-verified character expansion with a 200 decimal GB hard cap."""
import concurrent.futures as cf
import hashlib
import json
import math
import os
import re
import shutil
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from comic_workflow import engine as e
from comic_workflow.anima import CHECKPOINT
from comic_workflow.lora_inspection import inspect_lora
from curate_anima_loras import fetch, SafeRedirect, TOKEN, default_trigger
from curate_character_loras import matches, ALIASES, FAMILIES
from curate_character_loras import word_text

OUT = ROOT / 'data/character-expansion-20261031'
LIB = ROOT / 'models/loras'
LIMIT = 200_000_000_000
LOCK = threading.RLock()
STATE = {}
KNOWN = {}


def save_state():
    STATE['updated_at'] = time.strftime('%Y-%m-%dT%H:%M:%S%z')
    e.save(OUT / 'status.json', STATE)


def catalog(query='', anima=True, pages=200, tag=None):
    params = dict(types='LORA', limit=100, sort='Most Downloaded', period='AllTime', nsfw='true')
    if query: params['query'] = query
    if anima: params['baseModels'] = 'Anima'
    if tag: params['tag'] = tag
    url = 'https://civitai.com/api/v1/models?' + urllib.parse.urlencode(params)
    seen = set()
    for page in range(pages):
        if not url or url in seen: return
        seen.add(url)
        key = hashlib.sha256(url.encode()).hexdigest()
        path = OUT / 'sources' / (key + '.json')
        try:
            data = e.read(path) if path.exists() else fetch(url)
            if not path.exists(): e.save(path, data)
        except Exception:
            with LOCK:
                STATE.setdefault('search_errors', []).append({'query': query, 'page': page, 'anima': anima})
                save_state()
            return
        yield from data.get('items', [])
        url = data.get('metadata', {}).get('nextPage')


def candidates(models, anima=True):
    result = []
    for model in models:
        if model.get('type') != 'LORA': continue
        if re.search(r'artist style|\bstyle\b|画风|画風|screencap|photoreal|real person', model['name'], re.I): continue
        if 'character' not in [t.lower() for t in model.get('tags', [])]: continue
        for version in model.get('modelVersions', []):
            base = version.get('baseModel', '')
            if ('anima' not in base.lower()) if anima else (base not in FAMILIES): continue
            if version.get('availability', 'Public') != 'Public': continue
            files = sorted(version.get('files', []), key=lambda f: not f.get('primary', False))
            for f in files:
                sha = f.get('hashes', {}).get('SHA256', '').lower()
                if f.get('type') != 'Model' or not f['name'].endswith('.safetensors') or len(sha) != 64: continue
                reported_size = math.ceil(f.get('sizeKB', 0) * 1024)
                if reported_size <= 0: continue
                # API sizeKB can omit the 8-byte safetensors header prefix or round.
                # Reserve tolerance inside the hard cap, never outside it.
                size = reported_size + 4096
                result.append(dict(model_id=model['id'], version_id=version['id'], title=model['name'],
                    version=version['name'], base_model=base, sha256=sha, bytes=size,
                    url=f['downloadUrl'], triggers=version.get('trainedWords', []),
                    popularity=(model.get('stats') or {}).get('downloadCount') or 0,
                    version_popularity=(version.get('stats') or {}).get('downloadCount') or 0,
                    source=f"https://civitai.com/models/{model['id']}?modelVersionId={version['id']}",
                    _model=model, _version=version))
                break
    return sorted(result, key=lambda c: (c['popularity'], c['version_popularity']), reverse=True)


def public(c):
    return {k:v for k,v in c.items() if not k.startswith('_')}


def band_matches(c, group):
    """Search is only discovery; require franchise or full character identity."""
    text = ' ' + word_text(' '.join([c.get('title', c.get('name', '')),
        *c.get('triggers', []), *c.get('_model', {}).get('tags', [])])) + ' '
    aliases = {
        'Ave Mujica': ['ave mujica', 'ave mujiac', '豊川祥子', '丰川祥子', '若葉睦', '若叶睦',
            '八幡海鈴', '八幡海铃', '祐天寺にゃむ', '祐天寺若麦', '三角初華', '三角初华',
            'togawa sakiko', 'wakaba mutsumi', 'yahata umiri', 'yuutenji nyamu', 'yutenji nyamu', 'misumi uika',
            'sakiko togawa', 'mutsumi wakaba', 'umiri yahata', 'nyamu yuutenji', 'nyamu yutenji', 'uika misumi'],
        'Girls Band Cry': ['girls band cry', 'ガールズバンドクライ', '少女乐队的呐喊',
            '井芹仁菜', '河原木桃香', '安和昴', '海老塚智',
            'iseri nina', 'kawaragi momoka', 'awa subaru', 'ebizuka tomo',
            'nina iseri', 'momoka kawaragi', 'subaru awa', 'tomo ebizuka']}
    return any(word_text(alias) and ' '+word_text(alias)+' ' in text for alias in aliases[group])


def reserve(c, group, name):
    sha = c['sha256']
    with LOCK:
        if sha in STATE['files']: return None
        if sha in KNOWN and (LIB / KNOWN[sha]).exists(): return None
        # Reserve declared file bytes before dispatching any concurrent download.
        if STATE['reserved_bytes'] + c['bytes'] > LIMIT: return None
        if shutil.disk_usage(LIB).free < c['bytes'] + 10_000_000_000: raise RuntimeError('Insufficient free disk')
        safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '-', name).strip(' .')[:100]
        family = 'anime' if 'anima' in c['base_model'].lower() else 'sdxl'
        relative = f'{family}/{group}/{safe}_{c["version_id"]}.safetensors'
        row = dict(public(c), relative_path=relative, group=group, name=name, status='queued', received_bytes=0)
        STATE['files'][sha] = row
        STATE['reserved_bytes'] += c['bytes']
        save_state()
        return row


def download(row):
    path = LIB / row['relative_path']
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix('.safetensors.partial')
    for attempt in range(3):
        try:
            with LOCK:
                row['status'] = 'downloading'
                save_state()
            if not path.exists():
                offset = partial.stat().st_size if partial.exists() else 0
                headers = {'User-Agent':'Mozilla/5.0'}
                if urllib.parse.urlsplit(row['url']).hostname == 'civitai.com': headers['Authorization'] = 'Bearer ' + TOKEN
                if offset: headers['Range'] = f'bytes={offset}-'
                opener = urllib.request.build_opener(SafeRedirect())
                with opener.open(urllib.request.Request(row['url'], headers=headers), timeout=60) as response:
                    resumed = response.status == 206 and response.headers.get('Content-Range','').startswith(f'bytes {offset}-')
                    offset = offset if resumed else 0
                    expected_end = offset + int(response.headers['Content-Length']) if response.headers.get('Content-Length') else None
                    if response.headers.get('Content-Length') and offset + int(response.headers['Content-Length']) > row['bytes']:
                        raise ValueError('Source exceeds reserved size')
                    last_update = time.monotonic()
                    with partial.open('ab' if resumed else 'wb') as stream:
                        while block := response.read(min(1024*1024, row['bytes'] - offset + 1)):
                            if offset + len(block) > row['bytes']: raise ValueError('File exceeds reserved size')
                            stream.write(block)
                            offset += len(block)
                            row['received_bytes'] = offset
                            if time.monotonic() - last_update > 10:
                                with LOCK: save_state()
                                last_update = time.monotonic()
                if expected_end is not None and offset < expected_end:
                    raise OSError('Incomplete response; resume on retry')
                with partial.open('rb') as stream:
                    if hashlib.file_digest(stream, 'sha256').hexdigest() != row['sha256']: raise ValueError('SHA256 mismatch')
                inspection = inspect_lora(partial, checkpoint=CHECKPOINT)
                expected = 'Anima' if row['relative_path'].startswith('anime/') else 'SDXL'
                if inspection['family'] != expected: raise ValueError('Architecture not verified')
                partial.replace(path)
            else:
                with path.open('rb') as stream:
                    if hashlib.file_digest(stream, 'sha256').hexdigest() != row['sha256']: raise ValueError('Existing hash mismatch')
                inspection = inspect_lora(path, checkpoint=CHECKPOINT)
            with LOCK:
                labels_path = ROOT / 'data/lora-labels.json'
                labels = e.read(labels_path)
                words = default_trigger(row['triggers'])
                labels[row['relative_path']] = dict(name=row['name'], character=row['name'], trigger_words=words,
                    appearance_prompt=words, source_trained_words=row['triggers'], weight=0.7,
                    checkpoint=CHECKPOINT if row['relative_path'].startswith('anime/') else '',
                    confirmed=True, base_model=row['base_model'], source=row['source'],
                    sha256=row['sha256'], tested_load=False)
                e.save(labels_path, labels)
                row.update(status='verified', received_bytes=path.stat().st_size, inspection=inspection)
                KNOWN[row['sha256']] = row['relative_path']
                save_state()
            print('VERIFIED', row['group'], row['name'], path.stat().st_size, flush=True)
            return
        except Exception as exc:
            with LOCK:
                row['last_error'] = type(exc).__name__ + ((' HTTP '+str(exc.code)) if hasattr(exc, 'code') else '')
                save_state()
            if isinstance(exc, ValueError): break
            time.sleep(2*(attempt+1))
    with LOCK:
        row['status'] = 'failed'
        save_state()
    print('FAILED', row['name'], row['last_error'], flush=True)


def run_rows(rows, stage):
    with LOCK:
        STATE['stage'] = stage
        save_state()
    with cf.ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(download, rows))
    identity_config=ROOT/'config/lora-chinese-identities.json'
    if rows and identity_config.exists():
        from sync_lora_chinese_library import plan as identity_plan, apply_plan
        result=apply_plan(identity_plan())
        updated=e.read(OUT/'status.json')
        STATE.clear(); STATE.update(updated)
        aliases=e.read(ROOT/'data/lora-curation/rename-result.json').get('mapping',{})
        for sha,path in list(KNOWN.items()): KNOWN[sha]=aliases.get(path,path)
        STATE['chinese_library_pending']=len(result['pending'])
        save_state()


def collect(queries, anima=True, pages=200):
    result = {}
    with LOCK:
        STATE['search_progress'] = dict(queries=len(queries), completed=0, anima=anima)
        save_state()
    with cf.ThreadPoolExecutor(max_workers=3) as pool:
        for items in pool.map(lambda q:list(catalog(q, anima, pages)), queries):
            result.update({m['id']:m for m in items})
            with LOCK:
                STATE['search_progress']['completed'] += 1
                STATE['search_progress']['models'] = len(result)
                save_state()
    print('CATALOG', len(queries), 'queries', len(result), 'models', flush=True)
    return list(result.values())


def operator_plan(roster, cs, existing_names, group='明日方舟'):
    rows, coverage = [], []
    for char in roster:
        if char['name'] in existing_names:
            coverage.append(dict(character=char, status='already_installed'))
            continue
        selected = next((c for c in cs if matches(c['_model'], char, roster, c['_version'])), None)
        coverage.append(dict(character=char, status='candidate_found' if selected else 'no_candidate_found',
                             selected=public(selected) if selected else None))
        if selected:
            row = reserve(selected, group, char['name'])
            if row: rows.append(row)
    return rows, coverage


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    # Windows process lock: a second launch cannot race budget reservations.
    import msvcrt
    lock = (OUT / 'worker.lock').open('a+b')
    lock.seek(0)
    if lock.read(1) == b'': lock.write(b'0'); lock.flush()
    lock.seek(0)
    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    status_path = OUT / 'status.json'
    STATE.update(e.read(status_path) if status_path.exists() else dict(limit_bytes=LIMIT, reserved_bytes=0, files={}, stage='discovery'))
    STATE.update(pid=os.getpid(), status='running')
    STATE.pop('error', None)
    if '--retry-network-only' in sys.argv:
        # A final bounded pass over already reserved network failures. No new
        # catalog work, budget reservations, or structural-failure promotion.
        assert STATE['reserved_bytes'] <= LIMIT
        rows = [r for r in STATE['files'].values()
                if r['status'] in {'failed', 'downloading'} and r.get('last_error') == 'URLError']
        STATE['network_recovery'] = dict(started_at=time.strftime('%Y-%m-%dT%H:%M:%S%z'), count=len(rows))
        save_state()
        run_rows(rows, 'network_recovery')
        STATE['network_recovery']['finished_at'] = time.strftime('%Y-%m-%dT%H:%M:%S%z')
        STATE.update(status='complete_with_gaps', stage='finished')
        save_state()
        print('NETWORK_RECOVERY_FINISHED', flush=True)
        return
    labels = e.read(ROOT / 'data/lora-labels.json')
    if not (OUT / 'lora-labels-before.json').exists(): e.save(OUT / 'lora-labels-before.json', labels)
    for relative, label in labels.items():
        if label.get('sha256') and (LIB / relative).exists(): KNOWN[label['sha256'].lower()] = relative
    # Index old files without labels once, so renaming does not cause duplicate downloads.
    cache_path = OUT / 'existing-hashes.json'
    cache = e.read(cache_path) if cache_path.exists() else {}
    for path in LIB.rglob('*.safetensors'):
        key = path.relative_to(LIB).as_posix()
        identity = [path.stat().st_size, path.stat().st_mtime_ns]
        if key not in cache or cache[key]['identity'] != identity:
            with path.open('rb') as stream: sha = hashlib.file_digest(stream, 'sha256').hexdigest()
            cache[key] = dict(identity=identity, sha256=sha)
        KNOWN[cache[key]['sha256']] = key
    e.save(cache_path, cache)
    save_state()
    pending = [r for r in STATE['files'].values() if r['status'] in {'queued', 'downloading', 'failed'}]
    if pending: run_rows(pending, 'resume')
    try:
        roster_source = OUT / 'arknights-roster-source.json'
        table = e.read(roster_source) if roster_source.exists() else fetch('https://raw.githubusercontent.com/Kengxxiao/ArknightsGameData/master/zh_CN/gamedata/excel/character_table.json')
        e.save(OUT / 'arknights-roster-source.json', table)
    except Exception:
        table = e.read(ROOT / 'data/anima-migration/arknights-current.json')
        STATE['roster_warning'] = 'Live roster unavailable; using previous snapshot'
    roster = [dict(id=k, name=v['name'], english=v['appellation'], game='明日方舟', aliases=ALIASES.get(v['name'], []),
                   rarity=v['rarity'], temporary=bool(v['isNotObtainable']))
              for k,v in table.items() if k.startswith('char_') and v['profession'] not in ('TOKEN','TRAP')]
    e.save(OUT / 'roster.json', roster)
    models = collect(['Arknights', '明日方舟'])
    existing = {v.get('character',v.get('name')) for k,v in labels.items() if k.startswith('anime/明日方舟/') and (LIB/k).exists()}
    rows, coverage = operator_plan(roster, candidates(models), existing)
    e.save(OUT / 'arknights-anima-coverage.json', coverage)
    run_rows(rows, 'arknights_anima')
    # Search individual missing identities too: franchise names are often absent from titles.
    missing = [r['character'] for r in coverage if r['status']=='no_candidate_found']
    extra = collect(list(dict.fromkeys(c['english'] for c in missing if len(c['english'])>=3)), pages=3)
    rows, coverage2 = operator_plan([r['character'] for r in coverage if r['status']=='no_candidate_found'], candidates(extra), existing)
    e.save(OUT / 'arknights-anima-supplement.json', coverage2)
    run_rows(rows, 'arknights_anima_supplement')
    # Isolated SDXL alternatives only for operators still missing in both local families.
    labels = e.read(ROOT / 'data/lora-labels.json')
    existing_all = {v.get('character',v.get('name')) for k,v in labels.items() if '/明日方舟/' in k and (LIB/k).exists()}
    gaps = [c for c in roster if c['name'] not in existing_all]
    fallback = collect(['Arknights', '明日方舟'], anima=False)
    rows, fallback_coverage = operator_plan(gaps, candidates(fallback, anima=False), existing_all)
    e.save(OUT / 'arknights-sdxl-fallback.json', fallback_coverage)
    run_rows(rows, 'arknights_sdxl_fallback')
    for group, queries in [('Ave Mujica',['Ave Mujica','丰川祥子','Sakiko','Mutsumi','Umiri','Nyamu','Uika','Wakaba Mutsumi','Yahata Umiri','Yuutenji Nyamu','Misumi Uika']),
                           ('Girls Band Cry',['Girls Band Cry','Iseri Nina','Kawaragi Momoka','Awa Subaru','Ebizuka Tomo','Rupa'])]:
        cs = candidates(collect(queries))
        seen = set(); rows = []
        for c in cs:
            if not band_matches(c, group): continue
            # Collections may contain a different band member in each version.
            if c['sha256'] in seen: continue
            seen.add(c['sha256'])
            row = reserve(c, group, c['title'])
            if row: rows.append(row)
        run_rows(rows, group)
    # Global character popularity is explicitly a Civitai download-count proxy.
    STATE['ranking_basis'] = 'Civitai character LoRA all-time downloadCount, descending; one version per model'
    seen = set()
    rows = []
    for model in catalog(tag='character', pages=1000):
        cs = candidates([model])
        if not cs or model['id'] in seen: continue
        seen.add(model['id'])
        c = cs[0]
        row = reserve(c, '热门角色', c['title'])
        if row: rows.append(row)
        if STATE['reserved_bytes'] > LIMIT - 1_000_000: break
        if len(rows) >= 30:
            run_rows(rows, 'popular_characters'); rows = []
        # Once only a tiny remainder is left, there is no need for thousands of API pages.
        if STATE['reserved_bytes'] > LIMIT - 20_000_000: break
    if rows: run_rows(rows, 'popular_characters')
    labels = e.read(ROOT / 'data/lora-labels.json')
    final = []
    for c in roster:
        paths = [k for k,v in labels.items() if '/明日方舟/' in k and v.get('character',v.get('name')) == c['name'] and (LIB/k).exists()]
        final.append(dict(character=c, anima=[k for k in paths if k.startswith('anime/')], fallback=[k for k in paths if k.startswith('sdxl/')]))
    e.save(OUT / 'arknights-final-coverage.json', final)
    STATE.update(status='complete_with_gaps' if any(not r['anima'] for r in final) else 'complete', stage='finished')
    save_state()
    print('FINISHED', flush=True)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    try: main()
    except Exception as exc:
        if STATE:
            STATE.update(status='stopped_error', error=type(exc).__name__)
            save_state()
        print('STOPPED', type(exc).__name__, flush=True)
        raise
