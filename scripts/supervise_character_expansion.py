"""Detached Windows supervisor for this finite, user-requested download job."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import msvcrt

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/character-expansion-20261031'
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT/'src'))
from comic_workflow import engine as e


def main():
    with (OUT/'supervisor.lock').open('a+b') as lock:
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        run_supervised()


def run_supervised():
    record = dict(pid=os.getpid(), status='starting', attempts=[])
    def save():
        record['updated_at'] = time.strftime('%Y-%m-%dT%H:%M:%S%z')
        e.save(OUT/'supervisor.json', record)
    save()
    with (OUT/'download-supervised.log').open('a', encoding='utf-8', buffering=1) as log:
        for attempt in range(6):
            child = subprocess.Popen([str(ROOT/'.venv/Scripts/python.exe'), '-u',
                str(ROOT/'scripts/download_character_expansion.py')]
                + (['--retry-network-only'] if '--retry-network-only' in sys.argv else []), cwd=ROOT,
                stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW)
            item = dict(attempt=attempt+1, pid=child.pid, started_at=time.strftime('%Y-%m-%dT%H:%M:%S%z'))
            record['attempts'].append(item)
            record.update(status='running', worker_pid=child.pid)
            while child.poll() is None:
                save()
                time.sleep(15)
            item.update(exit_code=child.returncode, ended_at=time.strftime('%Y-%m-%dT%H:%M:%S%z'))
            if child.returncode == 0:
                record['status'] = 'finished'
                save()
                return
            record['status'] = 'retrying'
            save()
            time.sleep(min(30*(attempt+1), 120))
        record['status'] = 'stopped_after_retries'
        save()


if __name__ == '__main__':
    main()
