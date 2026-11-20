"""One-time maintenance launcher preserving the snapshotted prompt session."""
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from comic_workflow import server

BACKUP = ROOT / 'data/maintenance/moss-recognition-restart'
OriginalApplication = server.Application


class RecoveredApplication(OriginalApplication):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        snapshot = json.loads((BACKUP / 'snapshot.json').read_text(encoding='utf-8'))
        for pid in snapshot['sessions']:
            assert pid.startswith('tmp-') and '/' not in pid and '\\' not in pid
            shutil.copytree(BACKUP / 'sessions' / pid, self.repo.transient_root / pid)
            self.repo.transient_ids.add(pid)
        for job in snapshot['jobs']:
            assert job['status'] not in {'running', 'queued', 'paused', 'pausing', 'cancelling'}
            self.jobs.memory[job['id']] = job
        print('Restored prompt sessions:', len(snapshot['sessions']), flush=True)


if __name__ == '__main__':
    server.Application = RecoveredApplication
    server.main()
