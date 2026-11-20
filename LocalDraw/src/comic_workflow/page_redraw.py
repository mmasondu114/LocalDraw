"""Optional user-directed prompt revision before a native page drawing job."""
import copy
import uuid
from . import engine as e, creative, manual_drawing, prompt_exchange
from .batch_drawing import story_fingerprint
from .local_prompts import panel_context
from .paths import prompt_model
from .repository import LOCK
from .task_control import checkpoint

INSTRUCTION = """Revise an existing comic panel's English image prompts to implement the user's correction.
The user correction has priority over conflicting details in the old prompts or storyboard.
Actually replace/remove conflicting descriptions, including contradictions in negative prompts;
do not merely append a Chinese complaint or say 'fix the image'. Write the desired final image.
Preserve the original POV, characters, style, clothing, setting and continuity unless the user
explicitly asks to change them. Use adjacent panels only as context, never combine panels.
If the correction names a particular panel, change only that panel and preserve the others exactly.
For a page-wide correction, apply it to every relevant panel. Do not add dialogue lettering.
Return panel_id, positive and negative only. Prompts must be English. No unrelated additions.
"""

def prepare(repo, pid, request, *, single_panel=False):
    project = repo.get(pid)
    page = project['pages'].get(request.get('page_id'))
    e.need(page and page['panels'], '请选择已有分镜的页面')
    note = request.get('revision_note', '')
    e.need(isinstance(note, str) and len(note) <= 4000, '问题／修改要求最多4000字')
    targets = [p['id'] for p in page['panels']]
    if single_panel:
        panel_id = request.get('panel_id')
        e.need(isinstance(panel_id, str) and panel_id in targets, '请选择当前页面中已有分镜的格子')
        targets = [panel_id]
    args = {**request, 'targets': targets, 'force': True}
    args.pop('target', None)
    preview = manual_drawing.preview(repo, pid, args)
    frozen = manual_drawing.prepare_request(repo, pid, {
        **args, 'confirm_prompts': True, 'prompt_rows': preview['panels']})
    return {**frozen, 'page_id': page['id'], 'revision_note': note.strip(),
            **({'panel_id': targets[0]} if single_panel else {})}

def apply_revision(app, pid, request, update, jid):
    note = request.get('revision_note', '').strip()
    if not note:
        return
    repo = app.repo
    folder = repo.folder(pid) / 'page-redraw' / request['run_key']
    path = folder / 'revision.json'
    original = prompt_exchange.load_import(repo, pid, request['bundle'])
    fingerprint = original['storyboard_hash']
    def check():
        checkpoint()
        e.need(story_fingerprint(repo.get(pid)) == fingerprint, '分镜已变化，已停止重画；已有图片保留')
    check()
    if path.exists():
        report = e.read(path)
        e.need(report['note'] == note and report['storyboard_hash'] == fingerprint,
               '修改要求或分镜版本与断点不同，请重新发起重画')
    else:
        report = {'note': note, 'storyboard_hash': fingerprint, 'original_bundle': original['id'],
                  'original_rows': copy.deepcopy(original['panels']), 'revised_rows': [], 'status': 'revising'}
        e.save(path, report)
    if report['status'] == 'complete':
        request['bundle'] = report['revised_bundle']
        return
    rows = report['original_rows']
    document = prompt_exchange.export_storyboard(repo, pid)
    positions = {r['panel_id']: n for n, r in enumerate(document['panels'])}
    done = {r['panel_id'] for r in report['revised_rows']}
    schema = {'type': 'object', 'additionalProperties': False,
              'required': ['panel_id', 'positive', 'negative'], 'properties': {
                  'panel_id': {'type': 'string'},
                  'positive': {'type': 'string', 'minLength': 1, 'maxLength': 12000, 'pattern': '^[ -~\\n\\r\\t]*$'},
                  'negative': {'type': 'string', 'maxLength': 12000, 'pattern': '^[ -~\\n\\r\\t]*$'}}}
    try:
        with prompt_model("page_redraw"):
            try:
                if len(done) < len(rows):
                    update('正在根据问题／修改要求调整' + ('当前格' if request.get('panel_id') else '当前页') + '提示词')
                    app.prepare_language(update=update)
                for row in rows:
                    check()
                    if row['panel_id'] in done:
                        continue
                    update(f"修改 {row['panel_id']} 的提示词（{len(done)+1}/{len(rows)}）")
                    context = panel_context(document, positions[row['panel_id']])
                    context.update(user_correction=note, page_id=request['page_id'],
                                   page_panel_ids=request['targets'], original_prompts=row)
                    if request.get('panel_id'):
                        context.update(redraw_panel_id=request['panel_id'],
                            scope_policy='Revise only this selected panel. Adjacent panels are context only; never change or draw them.')
                    schema['properties']['panel_id']['const'] = row['panel_id']
                    result, _ = creative.structured(INSTRUCTION, context, schema,
                        folder / row['panel_id'] / jid, update, max_tokens=2200, language_policy='')
                    e.jsonschema.validate(result, schema)
                    e.need(result['positive'].strip(), '修改后的正向提示词为空，未开始绘图')
                    report['revised_rows'].append({**row, 'positive': result['positive'], 'negative': result['negative']})
                    done.add(row['panel_id']);e.save(path, report)
            finally:
                app.model.clear_context()
        check()
        changed = [r['panel_id'] for r, old in zip(report['revised_rows'], rows)
                   if any(r[k].strip() != old[k].strip() for k in ('positive', 'negative'))]
        e.need(changed, '模型未按问题修改提示词，已停止，未使用旧提示词重画。请补充具体修改要求后重试。')
        with LOCK:
            check()
            bundle = {'id': uuid.uuid4().hex, 'created': e.stamp(), 'project_id': pid,
                      'storyboard_hash': fingerprint, 'panels': report['revised_rows']}
            e.save(repo.folder(pid) / 'prompt-imports' / (bundle['id']+'.json'), bundle)
            manual_drawing.save_confirmed_prompts(repo, pid, bundle)
            report.update(status='complete', revised_bundle=bundle['id'], changed_panels=changed)
            e.save(path, report)
            request['bundle'] = bundle['id']
        update('修改要求已应用，开始按修改后的提示词绘图')
    except Exception as exc:
        if report['status'] != 'complete':
            report.update(status='revision_failed', error=str(exc));e.save(path, report)
        raise
