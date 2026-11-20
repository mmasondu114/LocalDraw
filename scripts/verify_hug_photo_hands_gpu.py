"""Recheck an existing published hug photo with cached official Google GPU assets.

No new weights and no neural CPU path. The old GPU-positive controls are rerun;
landmarks still require direct digit/ownership inspection before use in drawing.
"""
from functools import partial
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import sys
import threading

from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from comic_workflow import engine as e
from verify_mediapipe_browser_hands_gpu import PAGE


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    verified = ROOT / 'data/test-results/mediapipe-browser-hands/20261007-162633-358975'
    previous = e.read(verified / 'report.json')
    e.need(previous['npm_integrity_verified'] and previous['task_matches_google_original'],
           'Cached official browser assets have no verified source')
    out = ROOT / 'data/test-results/hug-photo-google-hands' / e.stamp()
    web = out / 'local-assets'
    web.mkdir(parents=True)
    asset_hashes = {}
    for name in ('vision_bundle.mjs', 'wasm', 'hand_landmarker.task', 'thumb_up.jpg', 'right_hands.jpg'):
        source = verified / 'local-assets' / name
        target = web / name
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copyfile(source, target)
    for path in sorted(web.rglob('*')):
        if path.is_file():
            asset_hashes[path.relative_to(web).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    e.need(asset_hashes['hand_landmarker.task'] == previous['task_sha256'] ==
           'fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1',
           'Existing Google hand weights failed checksum verification')
    for control in previous['official_positive_controls']:
        e.need(asset_hashes[control['name'] + '.jpg'] == control['sha256'],
               'Official positive control image checksum changed')
    # Both orientations in the official photo are tested separately as single
    # hand controls. Keep the failed whole-photo two-hand result in the report;
    # a cropped-hand pass cannot establish multi-hand detector acceptance.
    with Image.open(web / 'right_hands.jpg') as control:
        control.crop((0, 0, 360, control.height)).save(web / 'right_hand_down.jpg')
        control.crop((360, 0, control.width, control.height)).save(web / 'right_hand_up.jpg')
    source = ROOT / 'data/lora-curation/research/photo-hug-references/pair-crop.jpg'
    # The crops contain the whole real hand and wrist. They are inference inputs,
    # never pasted into generated artwork. Two independently visible hands.
    boxes = [(540, 520, 995, 850), (190, 705, 620, 1015)]
    crop_records = []
    with Image.open(source) as image:
        for index, box in enumerate(boxes):
            e.need(0 <= box[0] < box[2] <= image.width and 0 <= box[1] < box[3] <= image.height,
                   'Reference hand crop exceeds the photo')
            width, height = box[2] - box[0], box[3] - box[1]
            scale = 640 / max(width, height)
            size = (round(width * scale), round(height * scale))
            image.crop(box).resize(size, Image.Resampling.LANCZOS).save(web / f'hand-{index}.png')
            crop_records.append({'index': index, 'source_box': list(box), 'size': list(size)})
    # Official two-hand control uses default confidence 0.5. The prior anime
    # diagnostic's 0.6 threshold failed this control; do not silently waive it.
    page_source = PAGE.replace('index<8', 'index<2').replace(
        'minHandDetectionConfidence:0.6, minHandPresenceConfidence:0.6',
        'minHandDetectionConfidence:0.5, minHandPresenceConfidence:0.5').replace(
        "['thumb_up','right_hands']", "['thumb_up','right_hands','right_hand_down','right_hand_up']")
    (web / 'index.html').write_text(page_source, encoding='utf-8')
    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=str(web)))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    messages, errors, result = [], [], None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome', headless=True,
                args=['--enable-gpu', '--use-angle=d3d11', '--disable-software-rasterizer'])
            context = browser.new_context()
            context.route('**/*', lambda route: route.continue_()
                          if route.request.url.startswith('http://127.0.0.1:') else route.abort())
            page = context.new_page()
            page.on('console', lambda message: messages.append({'type': message.type, 'text': message.text}))
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(f'http://127.0.0.1:{server.server_port}/index.html', wait_until='domcontentloaded', timeout=30000)
            page.wait_for_function('window.qa && window.qa.done', timeout=60000)
            result = page.evaluate('window.qa')
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
    fallback = [m for m in messages if any(term in m['text'].lower() for term in
                ('xnnpack', 'cpu delegate', 'inferencecalculatorcpu', 'fallback to cpu'))]
    record = {'source': source.relative_to(ROOT).as_posix(),
              'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
              'source_credit': 'data/lora-curation/research/photo-hug-references/sources.json',
              'confidence_settings': {'detection': 0.5, 'presence': 0.5, 'tracking': 0.5},
              'settings_source': 'https://ai.google.dev/edge/mediapipe/solutions/vision/hand_landmarker/web_js',
              'official_asset_source': verified.relative_to(ROOT).as_posix(),
              'asset_sha256': asset_hashes, 'crop_records': crop_records,
              'result': result, 'console': messages, 'page_errors': errors,
              'cpu_fallback_messages': fallback, 'production_adoption': False,
              'new_weights_downloaded': False, 'acceptance': 'pending_direct_landmark_inspection'}
    e.save(out / 'report.json', record)
    e.need(not fallback and not errors and result and not result.get('error'),
           'Google GPU route failed; no CPU retry is permitted')
    counts = {c['name']: len(c['landmarks']) for c in result['positiveControls']}
    record['whole_photo_two_hand_control_passed'] = counts['right_hands'] == 2
    record['single_hand_controls_passed'] = all(counts[name] == 1 for name in
        ('thumb_up', 'right_hand_down', 'right_hand_up'))
    record['automatic_hand_acceptance'] = False
    record['detector_scope'] = 'single_hand_reference_crops_only; whole-photo two-hand check recorded separately'
    e.save(out / 'report.json', record)
    # Preserve diagnostic overlays even when controls fail. A completed script
    # is evidence collection only, never detector or artwork acceptance.
    record['acceptance'] = ('pending_direct_landmark_inspection' if
        record['single_hand_controls_passed'] else 'controls_failed_diagnostic_only')
    e.save(out / 'report.json', record)
    chains = ((0, 1, 2, 3, 4), (0, 5, 6, 7, 8), (0, 9, 10, 11, 12),
              (0, 13, 14, 15, 16), (0, 17, 18, 19, 20))
    colors = ('orange', 'red', 'lime', 'cyan', 'magenta')
    converted = []
    for crop, detection in zip(crop_records, result['handRegions']):
        with Image.open(web / f"hand-{crop['index']}.png") as image:
            overlay = image.convert('RGB')
            draw = ImageDraw.Draw(overlay)
            hands = []
            for hand_index, landmarks in enumerate(detection['landmarks']):
                e.need(len(landmarks) == 21, 'Google returned an incomplete hand')
                pixel_points = [(v['x'] * overlay.width, v['y'] * overlay.height) for v in landmarks]
                for chain, color in zip(chains, colors):
                    draw.line([pixel_points[i] for i in chain], fill=color, width=3)
                for joint, (x, y) in enumerate(pixel_points):
                    draw.ellipse((x-3, y-3, x+3, y+3), fill='white')
                    draw.text((x+4, y-5), str(joint), fill='black')
                box = crop['source_box']
                absolute = [[box[0] + v['x'] * (box[2] - box[0]),
                             box[1] + v['y'] * (box[3] - box[1]), 1.] for v in landmarks]
                hands.append({'hand_index': hand_index, 'handedness': detection['handedness'][hand_index],
                              'source_points': absolute, 'landmarks': landmarks})
            overlay.save(out / f"hand-{crop['index']}-overlay.png")
            converted.append({'crop_index': crop['index'], 'source_box': crop['source_box'], 'hands': hands})
    e.save(out / 'source-landmarks.json', {'hands': converted, 'owner_assignment': 'unverified',
           'no_landmark_fabrication': True, 'production_adoption': False,
           'positive_controls_passed': record['single_hand_controls_passed'],
           'automatic_hand_acceptance': False})
    e.save(out.parent / 'latest.json', {'path': str(out), 'acceptance': record['acceptance']})
    print(e.json.dumps({'path': str(out), 'renderer': result['renderers'],
          'hand_counts': [len(c['landmarks']) for c in result['handRegions']],
          'positive_controls': [len(c['landmarks']) for c in result['positiveControls']],
          'cpu_fallback': False}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
