"""Review an existing local-repair result through the real GPU studio path."""
import argparse
import hashlib
from pathlib import Path
import shutil
import sys
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from comic_workflow import comfyui, creative, drawing_tools, engine as e
from comic_workflow.repository import Repository
from comic_workflow.characters import CharacterLibrary
from verify_guided_hand_studio_gpu import VerifiedApplication


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',type=Path,required=True)
    args=parser.parse_args()
    source=args.source.resolve()
    out=ROOT/'data/test-results/owned-local-review'/e.stamp()
    out.mkdir(parents=True)
    shutil.copytree(source/'projects',out/'projects')
    shutil.copytree(source/'characters',out/'characters')
    repo=Repository(out/'projects')
    pid=next(path.name for path in repo.root.iterdir() if path.is_dir())
    project=repo.get(pid)
    saved_batch=e.read(source/'batch.json')
    state=comfyui.drawing_state(repo,pid)
    batch=next(b for b in state['batches'] if b['id']==saved_batch['id'])
    # Reuse the originally captured instruction; no new repair is generated.
    request=e.read(source/'request.json')
    batch['render_options']['local_repair']['requested_instruction']=request['instruction']
    e.save(repo.folder(pid)/'images/index.json',state)
    panel=next(p for page in project['pages'].values() for p in page['panels'] if p['id']==batch['panel_id'])
    image=repo.folder(pid)/'images'/batch['images'][0]['file']
    before=hashlib.sha256(image.read_bytes()).hexdigest()
    app=VerifiedApplication(repo,out)
    app.characters=CharacterLibrary(out/'characters')
    try:
        print('Reviewing existing repair with selected owner, original requirement and crop on GPU.',flush=True)
        with patch.object(creative,'CharacterLibrary',return_value=app.characters):
            drawing_tools.review_batch(app,pid,panel,batch,'local-focus-'+out.name,lambda _:None)
        updated=next(b for b in comfyui.drawing_state(repo,pid)['batches'] if b['id']==batch['id'])
        result=updated['images'][0]['review']
        after=hashlib.sha256(image.read_bytes()).hexdigest()
        e.need(before==after,'Review modified the existing generated image')
        e.save(out/'review.json',result)
        e.save(out/'report.json',{'source':source.relative_to(ROOT).as_posix(),
            'entry':'drawing_tools.review_batch','generated_image_unchanged':True,
            'image_sha256':after,'captured_original_instruction_reused':True,
            'old_automatic_verdict':saved_batch['images'][0]['review']['verdict'],
            'new_automatic_verdict':result['verdict'],
            'local_repair_target':result.get('local_repair_target'),
            'gpu_only':True,'new_neural_drawing':False,'manual_hand_acceptance':False,
            'whole_goal_complete':False,'production_adoption':False})
        print(out.relative_to(ROOT).as_posix(),flush=True)
    finally:
        app.model.stop()


if __name__=='__main__':
    main()
