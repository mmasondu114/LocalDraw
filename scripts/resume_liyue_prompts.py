"""Resume the preserved prompt draft using bounded English generation."""
import os
import time
from types import SimpleNamespace
import prepare_liyue_english as prep
from comic_workflow import engine as e, local_prompts

last_saved = 0


def update(event):
    global last_saved
    if time.monotonic() - last_saved < 2:
        return
    last_saved = time.monotonic()
    event = event if isinstance(event, dict) else {'message':event}
    prep.save(phase='prompt_generation', progress=event.get('progress',prep.state.get('progress')),
              message=event.get('message',''))


def main():
    prep.idle()
    prep.state.update(e.read(prep.STATUS))
    draft = local_prompts.current_draft(prep.REPO,prep.PID)
    e.need(not draft['stale'], 'Story changed; do not reuse old prompts')
    draft = draft['draft']
    e.need(draft['status'] in ('failed','running','complete'), 'Draft cancelled or otherwise unavailable')
    prep.save(phase='prompt_generation', process_id=os.getpid(), worker='resume_liyue_prompts.py',
              draft_id=draft['id'])
    app=SimpleNamespace(repo=prep.REPO,prepare_language=prep.start,model=SimpleNamespace(stop=prep.stop))
    result=local_prompts.generate_draft(app,prep.PID,{'draft_id':draft['id'],
        'storyboard_hash':draft['document']['storyboard_hash']},update,'bounded-'+e.stamp())
    prep.save(phase='prompt_complete',prompt_result=result,
              progress={'completed':result['completed'],'total':result['completed'],'unit':'panels'})
    print(result,flush=True)


if __name__=='__main__':
    try:
        main()
    except Exception as error:
        prep.save(phase='failed',error=str(error))
        raise
