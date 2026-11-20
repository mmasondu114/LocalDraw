"""A failed download worker is restarted; successful completion ends supervision."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import supervise_character_expansion as s


class SupervisorTests(unittest.TestCase):
    def test_restart_after_exit_and_stop_after_success(self):
        first = Mock(pid=101, returncode=1)
        first.poll.side_effect = [None, 1]
        second = Mock(pid=102, returncode=0)
        second.poll.side_effect = [None, 0]
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(s, 'OUT', Path(tmp)), \
                 patch.object(s.subprocess, 'Popen', side_effect=[first,second]) as launch, \
                 patch.object(s.time, 'sleep'):
                s.main()
            record = json.loads((Path(tmp)/'supervisor.json').read_text(encoding='utf-8'))
            self.assertEqual(launch.call_count, 2)
            self.assertEqual(record['status'], 'finished')
            self.assertEqual([a['exit_code'] for a in record['attempts']], [1,0])

    def test_persistent_failure_stops_after_six_attempts(self):
        child = Mock(pid=101, returncode=1)
        child.poll.return_value = 1
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(s, 'OUT', Path(tmp)), \
                 patch.object(s.subprocess, 'Popen', return_value=child) as launch, \
                 patch.object(s.time, 'sleep'):
                s.main()
            record = json.loads((Path(tmp)/'supervisor.json').read_text(encoding='utf-8'))
            self.assertEqual(launch.call_count, 6)
            self.assertEqual(record['status'], 'stopped_after_retries')


if __name__ == '__main__': unittest.main()
