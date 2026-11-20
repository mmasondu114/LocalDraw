"""Record completed manual image inspections without granting goal acceptance."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads((ROOT / path).read_text(encoding='utf-8'))


def save(path, value):
    (ROOT / path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main():
    ledger_path = 'data/lora-curation/current-acceptance.json'
    ledger = read(ledger_path)
    trials = ledger['multi_character']['current_trials']
    for model, folder in [('meta', 'meta-20261007-1506'), ('anime', 'anime-20261007-1517')]:
        base = f'data/test-results/published-sam-hands/{folder}'
        report = read(base + '/report.json')
        passed = model == 'meta'
        report['model']['model_inference_run'] = True
        report['acceptance'] = 'hand_localization_passed_only' if passed else 'failed_clothing_and_arm_spill'
        for item in report['hand_regions']:
            item['visual_acceptance'] = 'hand_area_localized' if passed else 'combined_mask_rejected'
        save(base + '/report.json', report)
        model_record = f'data/lora-curation/research/hand-sam-{model}-verified.json'
        verified = read(model_record)
        verified.update(model_inference_run=True, gpu_inference_report=base + '/report.json')
        save(model_record, verified)
        review = {'reviewer': 'actual_image_inspection', 'viewed': ['overlay.png'],
                  'accepted_for_hand_localization_only': passed, 'accepted_as_final_image': False,
                  'device': 'cuda:0', 'neural_forward_calls': report['guarded_forward_calls'],
                  'observation': 'Eight reviewed hand areas localized without large sleeve or arm spill.' if passed
                  else 'Ganyu sleeve, Jean forearm/sleeve and Mona arm/clothing were incorrectly included.',
                  'production_adoption': False, 'whole_goal_complete': False}
        save(base + '/visual-review.json', review)
        trials[model + '_sam_hands_review'] = base + '/visual-review.json'
    for stamp, schedule in [('20261007-145955-676159', .65), ('20261007-151352-998257', .8)]:
        base = 'data/test-results/native-studio-batch/' + stamp
        save(base + '/visual-review.json', {'accepted': False, 'reviewer': 'actual_image_inspection',
             'viewed': ['result.png'], 'production_configuration_used': True,
             'people_count': 4, 'reference_end_at': schedule, 'seed': 71008,
             'full_body': 'Four heads and all eight shoes remain inside canvas.',
             'background': 'Path and trees are continuous; large washed-out white sky remains.',
             'hard_vertical_seam': False, 'anime_style': 'Japanese anime linework and cel shading',
             'identity': 'Four characters distinguishable; detailed costumes and accessories remain partial.',
             'hands': 'Malformed or fused fingers remain visible.',
             'reference_limit': 'Official reference images show upper body only.',
             'acceptance': 'partial_background_and_fine_anatomy_not_accepted', 'whole_goal_complete': False})
        trials['production_four_reference_end_' + str(schedule)] = base + '/visual-review.json'
    failed = 'data/test-results/animefix-hands/20261007-152658-370043/report.json'
    report = read(failed)
    report.update(acceptance='runtime_error_no_output',
                  error='Impact invokes removed DifferentialDiffusion.apply; no image accepted.')
    save(failed, report)
    base = 'data/test-results/animefix-hands/20261007-153528-776040'
    review = {'accepted': False, 'reviewer': 'actual_image_inspection',
              'viewed': ['result.png', '322-0.png', '322-1.png', '362-0.png', '362-1.png',
                         '402-0.png', '402-1.png', '442-0.png', '442-1.png'],
              'sampling': 'Pinned AnimeFix MaskDetailer 8steps CFG4 Euler normal denoise0.35',
              'hands_inspected': 8, 'gpu_only': True, 'people_count': 4,
              'scene_preserved': True, 'body_crop_paste': False,
              'observation': 'Some outlines improved; fused or irregular fingers remain, especially Jean and Mona. Full hand acceptance is not granted.',
              'acceptance': 'scene_preserved_hands_partial', 'production_adoption': False,
              'whole_goal_complete': False}
    save(base + '/visual-review.json', review)
    report = read(base + '/report.json')
    report['acceptance'] = review['acceptance']
    report['visual_review'] = base + '/visual-review.json'
    save(base + '/report.json', report)
    trials['animefix_author_hands_partial'] = base + '/visual-review.json'
    sources = ledger['multi_character']['primary_sources']
    for source in ['https://github.com/ExMikuPro/AnimeFix-Workflow',
                   'https://github.com/facebookresearch/segment-anything',
                   'https://github.com/Ov3rLoRd-MLEngineer/handsegnet-anime-sdxl',
                   'https://github.com/ltdrdata/ComfyUI-Impact-Pack/issues/1112']:
        if source not in sources:
            sources.append(source)
    ledger.update(complete=False, new_thread_permitted=False)
    save(ledger_path, ledger)
    print('Recorded actual image reviews; current goal remains incomplete.')


if __name__ == '__main__':
    main()
