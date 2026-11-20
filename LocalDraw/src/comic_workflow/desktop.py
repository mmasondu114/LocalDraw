"""Compact metadata for the native editor. Pixel data is fetched only on demand."""
from collections import defaultdict
from . import comfyui, engine as e, manual_drawing, problem_panels


def book(repo, pid):
    project = repo.get(pid)
    folder = repo.folder(pid)
    images = comfyui.drawing_state(repo, pid)
    texts = manual_drawing.saved_texts(repo, pid, project)
    marks = problem_panels.load(repo, pid)['items']
    workspace = e.read(folder / 'workspace.json') if (folder / 'workspace.json').exists() else {}
    candidates = defaultdict(list)
    for batch in reversed(images['batches']):
        for image in batch['images']:
            candidates[batch['panel_id'], batch['panel_hash']].append({
                'id': image['id'], 'file': image['file'], 'verdict': (image.get('review') or {}).get('verdict')})
    rows = []
    for key, page in sorted(project['pages'].items()):
        for panel in page['panels']:
            panel_id, panel_hash = panel['id'], e.digest(panel)
            text = texts.get(panel_id, {})
            positive, negative = text.get('positive', ''), text.get('negative', '')
            draft = workspace.get('panelPromptDrafts', {}).get(panel_id, {})
            if draft.get('hash') == panel_hash and draft.get('source') == [positive, negative]:
                positive, negative = draft['positive'], draft['negative']
            selection = images['selections'].get(panel_id, {})
            mark = marks.get(panel_id, {})
            rows.append(dict(id=panel_id, hash=panel_hash, summary=panel.get('main_instant') or panel.get('purpose', ''),
                             positive=positive or 'first-person POV, ' + panel.get('drawing_description', ''),
                             negative=negative, images=candidates[panel_id, panel_hash], marked=bool(mark),
                             note=mark.get('note', ''), count=mark.get('candidate_count', 8),
                             selected=selection['image']['id'] if selection.get('panel_hash') == panel_hash else '',
                             dialogue=[line['text'] for line in page.get('lettering', []) if line['panel_id'] == panel_id]))
    project_hash = e.digest(project)
    plan = (project.get('plan') or {}).get('pages', [])
    complete = bool(plan and all(page['id'] in project['pages'] for page in plan))
    bundle_id, draft_lines = '', []
    if not complete:
        for file in sorted((folder / 'storyboard-drafts').glob('*/bundle.json'), key=lambda f: f.stat().st_mtime, reverse=True):
            bundle = e.read(file)
            if bundle.get('base_hash') == project_hash and bundle.get('status') == 'complete' and bundle.get('pages'):
                bundle_id = bundle['id']
                for _, entry in sorted(bundle['pages'].items()):
                    for panel in entry['payload']['panels']:
                        draft_lines.append(panel['id'] + '  ' + panel['main_instant'])
                        draft_lines.extend(line['text'] for line in entry['payload'].get('lettering', []) if line['panel_id'] == panel['id'])
                break
    return dict(id=pid, title=project['brief']['title'], hash=project_hash, panels=rows, complete=complete,
                bundle=bundle_id, draft=draft_lines, last_panel=workspace.get('reviewPanel', ''))
