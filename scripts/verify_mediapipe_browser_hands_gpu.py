"""Evaluate Google's GPU Web HandLandmarker on eight frozen anime hand crops.

No Windows Python MediaPipe inference is used. Native official browser models
run only after the WebGL renderer is verified as the physical NVIDIA GPU.
"""
import base64
import hashlib
import io
import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import sys
import tarfile
import threading
import urllib.request
from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from comic_workflow import engine as e


PAGE = '''<!doctype html><meta charset="utf-8"><title>Local GPU hand diagnostic</title>
<script type="module">
import {HandLandmarker, FilesetResolver} from '/vision_bundle.mjs';
window.qa = {done:false, handRegions:[], positiveControls:[], draws:0, renderers:[]};
try {
  const canvas = document.createElement('canvas');
  const gl = canvas.getContext('webgl2');
  const ext = gl && gl.getExtension('WEBGL_debug_renderer_info');
  const renderer = ext && gl.getParameter(ext.UNMASKED_RENDERER_WEBGL);
  if (!renderer || !/NVIDIA/.test(renderer) || /SwiftShader|software|llvmpipe/i.test(renderer))
    throw Error('Physical NVIDIA WebGL GPU required; CPU/software fallback forbidden: ' + renderer);
  window.qa.renderers.push(renderer);
  const originalDraw = WebGL2RenderingContext.prototype.drawArrays;
  WebGL2RenderingContext.prototype.drawArrays = function(...args) {
    const ext = this.getExtension('WEBGL_debug_renderer_info');
    const device = ext && this.getParameter(ext.UNMASKED_RENDERER_WEBGL);
    if (!device || !/NVIDIA/.test(device) || /SwiftShader|software|llvmpipe/i.test(device))
      throw Error('Model WebGL draw must use physical NVIDIA GPU: ' + device);
    window.qa.draws++;
    return originalDraw.apply(this,args);
  };
  const files = await FilesetResolver.forVisionTasks('/wasm');
  const detector = await HandLandmarker.createFromOptions(files, {
    baseOptions:{modelAssetPath:'/hand_landmarker.task', delegate:'GPU'},
    runningMode:'IMAGE', numHands:2,
    minHandDetectionConfidence:0.6, minHandPresenceConfidence:0.6,
    minTrackingConfidence:0.5
  });
  for (let index=0; index<8; index++) {
    const image = new Image(); image.src='/hand-' + index + '.png'; await image.decode();
    const before=window.qa.draws;
    const result=detector.detect(image);
    if(window.qa.draws<=before) throw Error('No observed GPU draw during hand inference');
    window.qa.handRegions.push({index, gpuDraws:window.qa.draws-before,
      landmarks:result.landmarks, worldLandmarks:result.worldLandmarks, handedness:result.handedness});
  }
  for(const name of ['thumb_up','right_hands']) {
    const image = new Image(); image.src='/'+name+'.jpg'; await image.decode();
    const before=window.qa.draws;
    const result=detector.detect(image);
    if(window.qa.draws<=before) throw Error('No GPU draw during official positive control');
    window.qa.positiveControls.push({name, gpuDraws:window.qa.draws-before,
      landmarks:result.landmarks, worldLandmarks:result.worldLandmarks, handedness:result.handedness});
  }
  detector.close(); window.qa.delegate='GPU';
} catch(error) {window.qa.error=String(error.stack || error);}
window.qa.done=true;
</script>'''


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    out = ROOT / 'data/test-results/mediapipe-browser-hands' / e.stamp()
    web = out / 'local-assets'
    web.mkdir(parents=True)
    with urllib.request.urlopen('https://registry.npmjs.org/@mediapipe/tasks-vision/1.1.0', timeout=30) as response:
        package = json.load(response)
    with urllib.request.urlopen(package['dist']['tarball'], timeout=60) as response:
        archive = response.read()
    expected = package['dist']['integrity'].split('sha512-', 1)[1]
    e.need(base64.b64encode(hashlib.sha512(archive).digest()).decode() == expected, '官方 NPM 包完整性校验失败')
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as bundle:
        for item in bundle.getmembers():
            relative = Path(item.name).relative_to('package')
            if item.isfile() and (str(relative) == 'vision_bundle.mjs' or relative.parts[0] == 'wasm'):
                target = (web / relative).resolve()
                e.need(target.is_relative_to(web.resolve()) and item.size < 70_000_000, '官方包路径或大小异常')
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(bundle.extractfile(item).read())
    task_source = ROOT / 'data/published-solutions/comfyui-controlnet-aux/src/custom_controlnet_aux/mesh_graphormer/hand_landmarker.task'
    with urllib.request.urlopen('https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task', timeout=30) as response:
        task = response.read()
    e.need(hashlib.sha256(task).hexdigest() == hashlib.sha256(task_source.read_bytes()).hexdigest(), '移植版手部模型与 Google 官方版本不一致')
    (web / 'hand_landmarker.task').write_bytes(task)
    controls = []
    for name, expected_hands in [('thumb_up',1), ('right_hands',2)]:
        url = f'https://storage.googleapis.com/mediapipe-assets/{name}.jpg'
        with urllib.request.urlopen(url, timeout=30) as response:
            photo = response.read()
        (web / (name + '.jpg')).write_bytes(photo)
        controls.append({'name':name, 'official_url':url, 'sha256':hashlib.sha256(photo).hexdigest(),
                         'expected_hands':expected_hands,
                         'test_source':'https://github.com/google-ai-edge/mediapipe/blob/master/mediapipe/tasks/python/test/vision/hand_landmarker_test.py'})
    source = ROOT / 'data/test-results/published-hires/20261007-102547-732245/result.png'
    detections = e.read(ROOT / 'data/test-results/hand-detection-resolution/20261007-104419-563445/results.json')[1]
    e.need(detections['device'] == 'cuda:0' and len(detections['boxes']) == 8, '来源不是已复核的八手 CUDA 检测')
    crop_records = []
    with Image.open(source) as original:
        for index, detection in enumerate(detections['boxes']):
            x1, y1, x2, y2 = detection['box']
            side = max(x2-x1, y2-y1)*2
            cx, cy = (x1+x2)/2, (y1+y2)/2
            box = (max(0,int(cx-side/2)), max(0,int(cy-side/2)), min(original.width,int(cx+side/2)), min(original.height,int(cy+side/2)))
            original.crop(box).resize((512,512), Image.Resampling.LANCZOS).save(web / f'hand-{index}.png')
            crop_records.append({'index':index,'source_box':box,'detector_box':detection['box']})
    (web / 'index.html').write_text(PAGE, encoding='utf-8')
    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1',0), partial(QuietHandler, directory=str(web)))
    worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
    messages = []
    errors = []
    result = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome', headless=True,
                args=['--enable-gpu','--use-angle=d3d11','--disable-software-rasterizer'])
            context = browser.new_context()
            context.route('**/*', lambda route: route.continue_() if route.request.url.startswith('http://127.0.0.1:') else route.abort())
            page = context.new_page()
            page.on('console', lambda message: messages.append({'type':message.type,'text':message.text}))
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(f'http://127.0.0.1:{server.server_port}/index.html', wait_until='domcontentloaded', timeout=30000)
            page.wait_for_function('window.qa && window.qa.done', timeout=60000)
            result = page.evaluate('window.qa')
            browser.close()
    finally:
        server.shutdown(); server.server_close()
    fallback = [m for m in messages if any(term in m['text'].lower() for term in ('xnnpack','cpu delegate','inferencecalculatorcpu','fallback to cpu'))]
    record = {'acceptance':'pending_landmark_visual_review', 'google_guide':'https://developers.google.com/edge/mediapipe/solutions/vision/hand_landmarker/web_js',
              'sdk_version':package['version'], 'npm_integrity_verified':True,
              'task_sha256':hashlib.sha256(task).hexdigest(), 'task_matches_google_original':True,
              'source':source.relative_to(ROOT).as_posix(), 'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
              'crop_records':crop_records, 'official_positive_controls':controls,
              'result':result, 'console':messages, 'page_errors':errors,
              'cpu_fallback_messages':fallback, 'production_adoption':False,
              'scope':'Google native browser GPU delegate, no Python MediaPipe detector, no landmark fabrication.'}
    if fallback or errors or not result or result.get('error'):
        record['acceptance']='gpu_route_failed_no_cpu_retry'
    elif [len(item['landmarks']) for item in result['positiveControls']] != [1,2]:
        record['acceptance']='official_positive_control_failed'
    elif not any(item['landmarks'] for item in result['handRegions']):
        record['acceptance']='gpu_route_passed_anime_detection_failed'
    e.save(out / 'report.json', record)
    e.save(ROOT / 'data/test-results/mediapipe-browser-hands/latest.json', {'path':str(out),'acceptance':record['acceptance']})
    print(json.dumps({'path':str(out),'acceptance':record['acceptance'],'renderer':(result or {}).get('renderers'),
                      'hand_counts':[len(r['landmarks']) for r in (result or {}).get('handRegions',[])],
                      'positive_controls':[len(r['landmarks']) for r in (result or {}).get('positiveControls',[])]},ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
