"""Update the idle local studio; retain persistent project and selection files."""
import json,subprocess,time,tempfile,urllib.request,sys
from pathlib import Path
import psutil
ROOT=Path(__file__).resolve().parents[1]
BASE='http://127.0.0.1:8765'
def get(path):
    with urllib.request.urlopen(BASE+path,timeout=20) as r:return json.load(r)
def main():
    owner=psutil.Process(int(sys.argv[1]) if len(sys.argv)>1 else 79668)
    assert owner.cmdline()[1:]==[str(ROOT/'launch.py'),'--no-browser'],owner.cmdline()
    assert not any(list(p.glob('tmp-*/project.json')) for p in Path(tempfile.gettempdir()).glob('local-draw-session-*')),'Transient sessions require preservation before restart'
    boot=get('/api/bootstrap');queue=get('/api/queue')
    assert not any(j['status'] in {'running','queued','paused','pausing','cancelling'} for j in boot['jobs'])
    assert not queue.get('running')
    assert all(c.name().lower()=='conhost.exe' for c in owner.children(recursive=True)), 'Owned model process still present'
    backup=ROOT/'data/maintenance/page-redraw-20261106';backup.mkdir(parents=True,exist_ok=True)
    (backup/'projects-before.json').write_text(json.dumps(boot['projects'],ensure_ascii=False,indent=2),encoding='utf-8')
    owner.terminate();owner.wait(timeout=20)
    with (backup/'startup.log').open('ab') as log:
        child=subprocess.Popen([str(ROOT/'.venv/Scripts/python.exe'),str(ROOT/'launch.py'),'--no-browser'],cwd=ROOT,
            stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
    for _ in range(80):
        if child.poll() is not None:raise RuntimeError('Studio restart failed; see startup.log')
        try:
            status=get('/api/status')
            if status.get('features',{}).get('page_redraw_corrections'):break
        except OSError:pass
        time.sleep(.5)
    else:raise RuntimeError('Studio did not become ready')
    after=get('/api/bootstrap')
    assert {p['id']:p['hash'] for p in after['projects']}=={p['id']:p['hash'] for p in boot['projects']}
    (backup/'result.json').write_text(json.dumps({'ready':True,'launcher_pid':child.pid,'projects_unchanged':True,'features':status['features']},indent=2),encoding='utf-8')
    print('Studio updated; projects unchanged; page correction feature is active.')
if __name__=='__main__':main()
