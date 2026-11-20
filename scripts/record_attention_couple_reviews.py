"""Save the human-visible inspection of the completed GPU comparisons."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'data/test-results/attention-couple'


def save(folder, observations, status, change):
    out = BASE / folder
    renders = json.loads((out / 'renders.json').read_text(encoding='utf-8'))
    assert len(renders) == len(observations)
    images = []
    for render, remaining in zip(renders, observations):
        assert (out / (render['key'] + '.png')).is_file()
        images.append({
            'file': render['key'] + '.png',
            'expected_people': render['expected_people'],
            'observed_people': render['expected_people'],
            'background': 'continuous_park',
            'framing': 'heads_and_feet_visible',
            'remaining': remaining,
            'acceptance': 'partial' if 'major_identity_failure' not in remaining else 'failed',
        })
        render['acceptance'] = images[-1]['acceptance']
    report = {
        'status': status, 'images': images,
        'inspection': 'Every original output was opened and visually inspected, against the frozen official upper-body references and explicit full-body prompts.',
        'reference_limit': 'Official references in this comparison show the upper body. Leg and shoe checks are prompt compliance checks, not verified likeness to unseen official legs.',
        'change': change,
        'adopted_to_production': False,
        'full_task_accepted': False,
    }
    (out / 'visual-review.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (out / 'renders.json').write_text(json.dumps(renders, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


save('20261007-091640-244066', [
    ['Main Ganyu/Keqing features recognizable; garment ornaments and gloves are approximate.'],
    ['Main Ganyu/Keqing features recognizable; Ganyu iris colouring is inconsistent; garment ornaments are approximate.'],
    ['Ganyu chest becomes white and sleeves contain red trim; Jean hairstyle and ornaments approximate.'],
    ['Ganyu bare legs contradict the black-pantyhose prompt; Jean hairstyle and ornaments approximate.'],
    ['All four main identities recognizable; Mona eye hue and Jean hairstyle approximate; ornaments incomplete.'],
    ['Mona bare legs contradict the black-pantyhose prompt; eye and garment details approximate.'],
    ['Ganyu white legwear contradicts black-pantyhose prompt; simplified pupils and clothing details.'],
    ['Both main identities recognizable; simplified pupils and approximate clothing details.'],
    ['major_identity_failure', 'Ganyu has red hair and a red outfit; Keqing/Jean recognizable with approximate clothing.'],
    ['Ganyu red iris and red sleeve trim; ornaments and Jean hairstyle approximate.'],
    ['major_identity_failure', 'Ganyu has a long red/white robe and white legwear; Mona bare legs and red iris.'],
    ['Ganyu white chest/red trim; Mona bare legs and red iris; simplified faces and approximate clothing.'],
], 'count_and_background_improved_identity_outfit_review_failed',
     'Native full-canvas masked LoRA conditioning, per-branch one-person pose hints, unmodified pinned author Attention Couple. WAI and Noob were compared at identical settings.')

save('20261007-092546-413857', [
    ['Ganyu black chest, black tights, white/gold curtain and horns more consistent than the old candidate; face and garment ornaments approximate.'],
    ['Ganyu outfit more consistent, but eyes are dark/desaturated rather than the reference purple; Keqing ornaments approximate.'],
    ['All three main identities recognizable; Ganyu garment shape improved; eye hues, Jean hairstyle and small costume details approximate.'],
    ['All three main identities recognizable; Ganyu costume stays coherent; face details and Jean hairstyle approximate.'],
    ['Ganyu/Mona both retain covered legs; main costumes coherent; Ganyu iris and Mona hat/neck ornaments differ from the official reference.'],
    ['Ganyu/Mona both retain covered legs; main costumes coherent; flat/unequal iris details and small costume ornaments remain inaccurate.'],
], 'official_art_candidates_improve_costume_consistency_likeness_still_partial',
     'Same WAI graph, seeds and sampling settings as the preceding WAI run; only Ganyu/Mona candidate LoRA files and their author trigger phrases changed. This does not establish best-version acceptance for all characters.')

save('20261007-093432-049876', [
    ['Both identities recognizable; Ganyu eye hue and bell geometry approximate, Keqing ornaments inaccurate.'],
], 'native_reference_trial_count_passed_likeness_partial',
     'Native ComfyUI masked hooks, one full-canvas pose hint per branch, regional official-art IPAdapter references at 0.3; no Attention Couple nodes.')

save('20261007-093611-915883', [
    ['Both identities recognizable; eyes and costume ornaments approximate; no extra person.'],
    ['All three identities recognizable; Jean hairstyle approximate, garment ornaments inaccurate.'],
    ['All four identities recognizable; Ganyu chest pattern/white insert deviates, Mona eye hue and hat/neck ornaments inaccurate; adjacent gloves touch and fingers need further review.'],
], 'native_reference_trial_count_and_continuity_passed_likeness_partial',
     'Same native reference recipe, seed 71007 expanded to two, three and four people. Heads and feet visible in every image.')

save('20261007-093850-536986', [
    ['All three identities recognizable; Ganyu irises are inconsistent, Jean hairstyle and costume ornaments approximate.'],
    ['All four identities recognizable; Mona irises inconsistent, Jean hairstyle approximate and adjacent gloves overlap; costume ornaments inaccurate.'],
], 'native_reference_trial_count_and_continuity_passed_likeness_partial',
     'Same native reference recipe, seed 71008 expanded to three and four people; no attention-patch batch expansion or image compositing.')

save('20261007-094200-555871', [
    ['Both identities recognizable; Ganyu iris colouring and bell geometry approximate, Keqing ornaments incomplete.'],
], 'studio_graph_count_and_continuity_passed_likeness_partial',
     'Actual studio regional_prompt/lora_binding dispatch to native_masked, with a neutral default conditioning for uncovered areas and regional official-art references.')

save('20261007-094429-736680', [
    ['All three identities recognizable; Ganyu iris hue, Jean hairstyle and costume ornaments approximate.'],
    ['All three identities recognizable; Ganyu irises inconsistent, Jean hairstyle and costume ornaments approximate.'],
    ['All four identities recognizable; Ganyu iris hue, Mona irises and costume ornaments inaccurate; gloves near region boundaries need further hand review.'],
    ['All four identities recognizable; Ganyu/Mona irises and costume ornaments inaccurate; leftmost hand meets the frame edge and adjacent gloves need further review.'],
], 'studio_graph_count_and_continuity_passed_likeness_partial',
     'Actual studio regional_prompt/lora_binding dispatch to native_masked; three/four people at both seeds, with regional official-art references.')

print('Recorded 29 inspected images; no production adoption or full acceptance.')
