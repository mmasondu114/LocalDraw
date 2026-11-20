"""Persistent issue review, manual GPT exchange, and local FlashNext interpretation."""
import copy
import uuid
from . import engine as e, comfyui, creative, manual_drawing, prompt_exchange as exchange
from .batch_drawing import story_fingerprint
from .local_prompts import panel_context
from .paths import prompt_model
from .repository import LOCK, safe_id
from .task_control import checkpoint

FORMAT = 'comic-problem-review/v1'
INSTRUCTION = '''Understand the user's complaint about ONE comic panel and write the final desired image prompts.
The complaint overrides conflicting old descriptions. Preserve unrelated characters, POV, clothing and continuity.
Explain your understanding and proposed change in Chinese. positive and negative must be English, self-contained
image prompts, not instructions to fix an image. Replace contradictions instead of appending the complaint.
Do not change neighboring panels. Do not pretend to see images: only text and image metadata are supplied.
If the existing prompt already describes the desired result and the issue is a random rendering defect, you may
retain it, but explicitly explain that a fresh candidate is needed. Return all required fields, without drawing.'''


def load(repo, pid):
    path = repo.folder(pid) / 'problem-panels.json'
    return e.read(path) if path.exists() else {'items': {}}


def source_hash(project, item):
    return e.digest({'storyboard': story_fingerprint(project), 'note': item['note'],
                     'image_id': item.get('image_id'), 'panel_id': item['panel_id']})


def state(repo, pid):
    project = repo.get(pid)
    valid = {p['id']: p for _, p in exchange.panels(project)}
    items = []
    for key, row in load(repo, pid)['items'].items():
        if key not in valid:
            continue
        item = copy.deepcopy(row)
        item['panel_hash'] = e.digest(valid[key])
        item['ready'] = bool(item.get('prepared') and item['prepared']['source_hash'] == source_hash(project, item))
        items.append(item)
    return {'items': sorted(items, key=lambda r: r['panel_id']), 'project_hash': e.digest(project)}


def save(repo, pid, request):
    with LOCK:
        project = repo.get(pid)
        key = request.get('panel_id')
        e.need(isinstance(key, str) and key in {p['id'] for _, p in exchange.panels(project)}, '画格不存在')
        data = load(repo, pid)
        if request.get('marked') is False:
            data['items'].pop(key, None)
        else:
            item = copy.deepcopy(data['items'].get(key, {'panel_id': key, 'note': '', 'candidate_count': 8}))
            if 'note' in request:
                e.need(isinstance(request['note'], str) and len(request['note']) <= 4000, '问题说明最多 4000 字')
                item['note'] = request['note'].strip()
            if 'candidate_count' in request:
                count = request['candidate_count']
                e.need(type(count) is int and 1 <= count <= 16, '每格重画数量须为 1–16 张')
                item['candidate_count'] = count
            if request.get('image_id'):
                image_id = request['image_id']
                e.need(any(im['id'] == image_id for b in comfyui.drawing_state(repo, pid)['batches']
                           if b['panel_id'] == key for im in b['images']), '问题图片不属于这一格')
                item['image_id'] = image_id
            if item.get('prepared', {}).get('source_hash') != source_hash(project, item):
                item.pop('prepared', None)
            item['updated'] = e.stamp()
            data['items'][key] = item
        e.save(repo.folder(pid) / 'problem-panels.json', data)
    return state(repo, pid)


def export(repo, pid):
    with LOCK:
        project = repo.get(pid)
        issues = state(repo, pid)['items']
        e.need(issues, '请先标记有问题的格子')
        e.need(all(r['note'] for r in issues), '请先填写每个问题格子的问题说明')
        doc = exchange.export_storyboard(repo, pid)
        positions = {r['panel_id']: n for n, r in enumerate(doc['panels'])}
        prompts = manual_drawing.preview(repo, pid, {'base_hash': e.digest(project),
            'targets': [r['panel_id'] for r in issues], 'force': True})['panels']
        prompts = {r['panel_id']: r for r in prompts}
        images = {im['id']: im for b in comfyui.drawing_state(repo, pid)['batches'] for im in b['images']}
        rows = []
        for issue in issues:
            key = issue['panel_id']; image = images.get(issue.get('image_id'), {})
            original = prompts[key]
            rows.append({'panel_id': key, 'page_id': key.split('-')[0], 'problem': issue['note'],
                'source_hash': source_hash(project, issue), 'candidate_count': issue['candidate_count'],
                'image': {k: image[k] for k in ('id', 'file', 'seed') if k in image},
                'context': panel_context(doc, positions[key]),
                'original_positive': image.get('positive') or original['positive'],
                'original_negative': image.get('negative', original['negative']),
                'understanding': '', 'changes': '', 'positive': '', 'negative': ''})
        document = {'format': FORMAT, 'export_id': uuid.uuid4().hex, 'project_id': pid,
            'storyboard_hash': doc['storyboard_hash'], 'title': doc['title'],
            'instructions': INSTRUCTION + '\nFill understanding, changes, positive, negative for EVERY panel. '
                'Return the complete JSON with all IDs, source_hash and problem fields unchanged. '
                'The local image filenames are metadata, not accessible images; ask the user for images if needed.',
            'panels': rows}
        e.save(repo.folder(pid) / 'problem-exports' / (document['export_id'] + '.json'), document)
        return document


def validate_document(repo, pid, document):
    e.need(isinstance(document, dict) and document.get('format') == FORMAT, '请导入问题格子 JSON')
    e.need(document.get('project_id') == pid, '文件属于其他作品')
    export_id = safe_id(document.get('export_id', ''))
    path = repo.folder(pid) / 'problem-exports' / (export_id + '.json')
    e.need(path.exists(), '找不到本次导出记录，请重新导出')
    original = e.read(path)
    project = repo.get(pid)
    e.need(document.get('storyboard_hash') == original['storyboard_hash'] == story_fingerprint(project),
           '分镜或角色已变化，请重新理解问题')
    items = {r['panel_id']: r for r in state(repo, pid)['items']}
    expected = {r['panel_id']: r for r in original['panels']}
    rows = document.get('panels')
    e.need(isinstance(rows, list) and all(isinstance(r, dict) for r in rows), 'panels 格式错误')
    ids = [r.get('panel_id') for r in rows]
    e.need(all(isinstance(k, str) for k in ids) and len(set(ids)) == len(ids)
           and set(ids) == set(expected) == set(items), '问题格子范围已变化，或 JSON 有遗漏／重复，请重新导出')
    for row in rows:
        key = row['panel_id']; old = expected[key]
        e.need(row.get('source_hash') == old['source_hash'] == source_hash(project, items[key])
               and row.get('problem') == old['problem'], f'{key} 的问题说明已变化，请重新理解')
        e.need(row.get('page_id') == old['page_id'], '所属页面不正确')
        for field in ('understanding', 'changes', 'positive', 'negative'):
            value = row.get(field)
            e.need(isinstance(value, str) and len(value) <= 12000 and (field == 'negative' or value.strip()),
                   f'{key} 缺少有效的 {field}')
        e.need(all(ord(c) < 128 for c in row['positive'] + row['negative']), f'{key} 的绘图提示词须为英文')
    return rows


def import_result(repo, pid, document, provider='gpt', keep_prepared=False):
    with LOCK:
        rows = validate_document(repo, pid, document)
        data = load(repo, pid)
        for row in rows:
            if keep_prepared and data['items'][row['panel_id']].get('prepared', {}).get('source_hash') == row['source_hash']:
                continue
            data['items'][row['panel_id']]['prepared'] = {k: row[k] for k in
                ('source_hash', 'understanding', 'changes', 'positive', 'negative')}
            data['items'][row['panel_id']]['prepared'].update(provider=provider, created=e.stamp())
        e.save(repo.folder(pid) / 'problem-panels.json', data)
    return state(repo, pid)


def understand(app, pid, document, update, jid, keep_prepared=False):
    path = app.repo.folder(pid) / 'problem-exports' / (document['export_id'] + '-llm.json')
    result = e.read(path) if path.exists() else copy.deepcopy(document)
    if keep_prepared:
        prepared = {r['panel_id']: r['prepared'] for r in state(app.repo, pid)['items'] if r['ready']}
        for row in result['panels']:
            saved = prepared.get(row['panel_id'])
            if saved and saved['source_hash'] == row['source_hash']:
                row.update({k: saved[k] for k in ('understanding', 'changes', 'positive', 'negative')})
    try:
        with prompt_model('page_redraw'):
            app.prepare_language(update=update)
            for index, row in enumerate(result['panels']):
                checkpoint()
                e.need(story_fingerprint(app.repo.get(pid)) == result['storyboard_hash'], '分镜已变化，请重新理解')
                if row['positive'] and row['understanding']:
                    continue
                update({'message': f"FlashNext 正在理解 {row['panel_id']} 的问题",
                        'progress': {'completed': index, 'total': len(result['panels']), 'unit': '格'}})
                schema = {'type': 'object', 'additionalProperties': False,
                    'required': ['understanding', 'changes', 'positive', 'negative'],
                    'properties': {k: {'type': 'string', 'maxLength': 12000,
                        **({'minLength': 1} if k != 'negative' else {}),
                        **({'pattern': '^[ -~\\n\\r\\t]*$'} if k in ('positive','negative') else {})}
                        for k in ('understanding', 'changes', 'positive', 'negative')}}
                payload, _ = creative.structured(INSTRUCTION, row, schema,
                    app.repo.folder(pid) / 'runs' / jid / row['panel_id'], update,
                    max_tokens=2600, language_policy='')
                row.update(payload); e.save(path, result)
        import_result(app.repo, pid, result, provider='llm', keep_prepared=keep_prepared)
        return {'completion_message': '问题已理解，请核对修改说明，然后点击全部重画。'}
    finally:
        app.model.clear_context()


def understand_and_redraw(app, pid, document, update, jid):
    # Keep the same drawing run on resume, including its completed candidate slots.
    path = app.repo.folder(pid) / 'problem-exports' / (document['export_id'] + '-draw.json')
    if not path.exists():
        understand(app, pid, document, update, jid, keep_prepared=True)
        with LOCK:
            expected = {r['panel_id']: (r['source_hash'], r['candidate_count']) for r in document['panels']}
            project = app.repo.get(pid)
            current = {r['panel_id']: (source_hash(project, r), r['candidate_count'])
                       for r in state(app.repo, pid)['items']}
            e.need(current == expected, '问题说明或重画范围、张数已变化，请重新提交')
            frozen = prepare_drawing(app.repo, pid)
            e.save(path, frozen)
    return exchange.draw_imported(app, pid, e.read(path), update, jid)


def prepare_drawing(repo, pid):
    with LOCK:
        issues = state(repo, pid)['items']
        e.need(issues and all(r['ready'] for r in issues), '请先完成所有问题格子的理解或导入 GPT 结果')
        project = repo.get(pid)
        request = {'base_hash': e.digest(project), 'targets': [r['panel_id'] for r in issues],
                   'force': True, 'candidate_count': 8}
        preview = manual_drawing.preview(repo, pid, request)
        by_id = {r['panel_id']: r for r in issues}
        for row in preview['panels']:
            row.update({k: by_id[row['panel_id']]['prepared'][k] for k in ('positive','negative')})
        frozen = manual_drawing.prepare_request(repo, pid, {**request, 'confirm_prompts': True,
                                                           'prompt_rows': preview['panels']})
        frozen.update(problem_redraw=True, candidate_slots_by_panel={r['panel_id']: list(range(r['candidate_count'])) for r in issues})
        return frozen
