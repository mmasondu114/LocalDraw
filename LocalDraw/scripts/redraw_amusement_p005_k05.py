"""Correct the Doctor's coat color in P005-K05 without changing other prompts."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from comic_workflow import prompt_exchange
from comic_workflow.repository import Repository
from redraw_amusement_page_one import action, get, MODEL, PID

TARGET = 'P005-K05'
OUT = ROOT / 'data/authored-stories/amusement-day-20261018-p005-k05-mask8'


def save(name, value):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT/name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def main():
    receipt = OUT/'generation-started.json'
    if receipt.exists():
        print(receipt.read_text(encoding='utf-8'))
        return
    active = [j for j in get('/api/status')['jobs'] if j['status'] in
              {'running','queued','paused','pausing','cancelling'}]
    assert not active, active
    repo = Repository()
    previous = prompt_exchange.current_import(repo, PID)['bundle']
    if not (OUT/'before.json').exists():
        save('before.json', previous)
    old = {r['panel_id']:r for r in previous['panels']}
    document = prompt_exchange.export_storyboard(repo, PID)
    for row in document['panels']:
        row.update(positive=old[row['panel_id']]['positive'], negative=old[row['panel_id']]['negative'])
        if row['panel_id'] == TARGET:
            row['positive'] = (
                'Medium side-view of exactly two people seated opposite each other inside a Ferris-wheel cabin. '
                'RIGHT: the Doctor wears a matte charcoal-black long coat with a matching charcoal-black raised hood. '
                'The entire coat front, chest pockets, shoulders and sleeves are dark charcoal-black fabric, '
                'with restrained teal-blue trim. Beneath the raised hood, a roomy opaque dark face covering '
                'stands away from the face, with a distinct outer edge and space inside. Its broad plain angular '
                'surfaces bridge across the entire face in a simple loose enclosure, hiding all facial anatomy. '
                'The covering keeps its own outer silhouette rather than following the shape of the face; '
                'the lower edge hangs away from the chin. Deep shadow behind the hood brow conceals the eyes. '
                'Black gloves; a tiny black-and-white penguin plush peeks out of the dark chest pocket. '
                'The Doctor nods toward the left while sliding a small cream souvenir card into a dark coat pocket. '
                'LEFT: Exusiai laughs, short reddish-brown hair, orange eyes, normal human ears, '
                'white halo and pale geometric wing fragments. Exusiai alone wears a white jacket with black shoulders. '
                'Their clothing colors stay distinct. Separate seats, relaxed posture, hands kept apart. '
                'Sunset centered in the window between them, thin warm rim light along the Doctor coat edges '
                'while its broad fabric surfaces remain deep charcoal. Warm anime illustration.')
            row['negative'] = (
                'white coat on the right person, white hood on the right person, pale sleeves on the right person, '
                'white lab coat, matching white outfits, swapped clothing colors, washed-out dark fabric, '
                'overexposed clothing, visible face of hooded person, glowing eyes, '
                'skin-tight face covering, clingy mask, surgical mouth mask, molded human face mask, '
                'nose-shaped mask bulge, sculpted nose bridge on mask, lips embossed through mask, '
                'mouth outline through mask, chin contour through mask, cheek contours through mask, '
                'extra people, duplicate character, animal ears, firearms, holding hands, '
                'duplicated card, text, speech bubbles, watermark')
    changes = [r['panel_id'] for r in document['panels'] if
               (r['positive'],r['negative']) != (old[r['panel_id']]['positive'],old[r['panel_id']]['negative'])]
    assert set(changes) <= {TARGET}
    save('storyboard-prompts.json', document)
    preview = action({'action':'preview_prompt_import','project':PID,'document':document})
    assert preview['valid'] and not preview['missing'], preview
    assert not preview['warnings'], preview['warnings']
    bundle = action({'action':'save_prompt_import','project':PID,'document':document,
                     'preview_hash':preview['preview_hash'],'confirm':True})
    request = dict(action='draw_imported',project=PID,bundle=bundle['id'],targets=[TARGET],
                   candidate_count=8,checkpoint=MODEL,steps=35)
    save('prepared.json', {'request':request,'changed_prompt':TARGET,'old_images_preserved':True,
        'reference_image':'706d6ea88ec7418fa2c49db5d31e9a01',
        'reference_mode':'Viewed saved reference and translated its loose face-cover shape into text; no image conditioning',
        'reference_observations':'Independent angular covering away from facial anatomy; no nose, lips or chin traced through the mask'})
    try:
        result = action(request)
    except Exception:
        matches = []
        for path in (ROOT/'data/jobs').glob('*.json'):
            job=json.loads(path.read_text(encoding='utf-8')); req=job.get('request',{})
            if job.get('project')==PID and req.get('bundle')==bundle['id'] and req.get('targets')==[TARGET]:
                matches.append(job)
        if len(matches)!=1:
            raise
        result={'job':matches[0]['id'],'recovered':True}
    assert result.get('job'), result
    save('generation-started.json', {'request':request,'submission':result,'follow_after_start':False})
    print(json.dumps(result,ensure_ascii=False))
    job=get('/api/job?id='+result['job'])
    print(json.dumps({k:job.get(k) for k in ('status','message','progress','error')},ensure_ascii=False))


if __name__ == '__main__':
    main()
