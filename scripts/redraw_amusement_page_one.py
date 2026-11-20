"""Prepare the approved Anima text-only correction; preserve existing candidates."""
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from comic_workflow import prompt_exchange
from comic_workflow.repository import Repository

PID = 'story-d35a8fe50bd4'
MODEL = 'animaDeltaMix38BQwen0_deltaMixV60.safetensors'
OUT = ROOT / 'data/authored-stories/amusement-day-20261018-page-one'


def get(path):
    with urllib.request.urlopen('http://127.0.0.1:8765' + path, timeout=30) as r:
        return json.load(r)


def action(payload):
    token = get('/api/bootstrap')['token']
    req = urllib.request.Request('http://127.0.0.1:8765/api/action',
        data=json.dumps(payload).encode(),
        headers={'Content-Type': 'application/json', 'X-Studio-Token': token})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def save(name, value):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def prepare():
    repo = Repository()
    document = prompt_exchange.export_storyboard(repo, PID)
    original = prompt_exchange.load_import(repo, PID, '800de7f037ec48979a6c54ed77466176')
    existing = {p['panel_id']: p for p in original['panels']}
    doctor = ('a tall faceless hooded tactician in a charcoal long coat, the Doctor, '
              'hood pulled forward with a low brow; the face opening is a plain matte-black angular shape '
              'in deep shadow, closed black covering from forehead to chin, black neck fabric. '
              'Black gloves, subtle teal-blue coat accents')
    positive = {
        'P001-K01': ('Wide full-body establishing shot, exactly two people under a colorful amusement-park entrance arch, '
            'quiet sunny morning. LEFT: Exusiai (Arknights), short reddish-brown hair, orange eyes, human ears, '
            'white halo and pale angular wing fragments, white jacket with black shoulders, black skirt over opaque '
            'black tights and black boots, raises two admission tickets in her left hand. RIGHT: ' + doctor +
            ', black trousers and boots; both gloved hands hold one unfolded paper map, head inclined toward it. '
            'Empty path, distant carousel, warm daylight, anime illustration.'),
        'P001-K04': ('Solo chest-up anime illustration of the Doctor, a faceless hooded tactician. '
            'The entire face from forehead to chin is a pitch-black featureless shape inside a charcoal '
            'fabric hood. An opaque matte black angular covering completely encloses the face, '
            'deep unlit shadow under the hood. Charcoal long coat, subtle teal trim, black gloves. '
            'Their own bent forearm rises from their coat sleeve to their chest; gloved fingers release a '
            'small folded map already tucked inside the breast pocket, only one paper corner visible. '
            'Relaxed shoulders, head slightly turned left. Sunny morning, empty colorful amusement-park '
            'entrance softly blurred behind.'),
        'P001-K05': ('Low-angle full-body side view of exactly two people walking together toward a carousel. '
            'LEFT: Exusiai (Arknights), short red-brown hair, human ears, white halo and pale geometric wings, '
            'white jacket with black shoulders, black skirt, opaque black tights and boots; she steps forward '
            'and points ahead with her right hand. RIGHT: ' + doctor + ', black trousers and boots; '
            'walking beside her with empty relaxed hands. Both sets of feet visible, open sunlit path, '
            'striped carousel canopy ahead, anime illustration.')
    }
    avoid = ('eyeglasses, sunglasses, separate goggle lenses, glowing eyes, luminous eye slits, '
             'visible eyes, visible skin on face, nose, lips, hair inside hood, surgical mask, cloth mouth mask, '
             'bird beak mask, skull face, superhero mask, robotic eyes, motorcycle helmet, knight helmet, '
             'metal armor, chrome face, shiny faceplate, large round visor, reflective visor, helmet, '
             'silver outlines on face, facial highlights, open map, unfolded map, reading map, night, stars, '
             'baseball cap, peaked cap, bare skin, skin, exposed nose, other person hand')
    for row in document['panels']:
        old = existing[row['panel_id']]
        row.update(positive=old['positive'], negative=old['negative'])
        if row['panel_id'] == 'P001-K04':
            row['positive'] = positive[row['panel_id']]
            # Exusiai's eyes remain visible in the two-person scenes.
            doctor_avoid = avoid if row['panel_id'] == 'P001-K04' else (
                'eyeglasses, sunglasses, glowing eyes, surgical mask, bird beak mask, skull face, superhero mask, '
                'motorcycle helmet, knight helmet, chrome face, shiny faceplate')
            row['negative'] += ', ' + doctor_avoid
    save('storyboard-prompts.json', document)
    preview = action({'action':'preview_prompt_import', 'project':PID, 'document':document})
    assert preview['valid'] and not preview['missing'], preview
    assert not preview['warnings'], preview['warnings']
    current = prompt_exchange.current_import(repo, PID).get('bundle')
    expected = [(p['panel_id'],p['positive'],p['negative']) for p in preview['panels']]
    if current and [(p['panel_id'],p['positive'],p['negative']) for p in current['panels']] == expected:
        bundle = current
    else:
        bundle = action({'action':'save_prompt_import','project':PID,'document':document,
                         'preview_hash':preview['preview_hash'],'confirm':True})
    record = {'project':PID, 'bundle':bundle['id'], 'checkpoint':MODEL,
        'reference_mode':'Human visual comparison converted to text; no image conditioning',
        'reference_image':'706d6ea88ec7418fa2c49db5d31e9a01',
        'targets':['P001-K04'], 'candidate_count':16,
        'unchanged_panels':['P001-K01','P001-K02','P001-K03','P001-K05'],
        'unchanged_pages':['P002','P003','P004','P005','P006']}
    save('prepared.json',record)
    return record


if __name__ == '__main__':
    print(json.dumps(prepare(), ensure_ascii=False))
