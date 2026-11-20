import copy
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from comic_workflow import engine as e, comfyui, prompt_exchange as x
from comic_workflow.drawing_quantity import change_remaining_count, slots_for_panel
from comic_workflow.jobs import Jobs
from comic_workflow.repository import Repository


class QuantityPlanTests(unittest.TestCase):
    def setUp(self):
        self.request = {'action': 'draw_imported', 'candidate_count': 2,
                        'run_key': 'run', 'targets': ['A', 'B', 'C']}

    def batch(self, panel, slot, status='done', run='run'):
        return {'panel_id': panel, 'candidate_indices': [slot], 'status': status,
                'images': [{'file': 'saved.png'}] if status == 'done' else [],
                'render_options': {'import_run': run}}

    def test_completed_panels_unchanged_partial_panel_gets_new_allowance(self):
        batches = [self.batch('A', 0), self.batch('A', 1), self.batch('B', 0),
                   self.batch('C', 9, run='unrelated')]
        result = change_remaining_count(self.request, batches, 3)
        self.assertEqual(result['candidate_slots_by_panel'], {'A': [0, 1], 'B': [0, 1, 2, 3], 'C': [0, 1, 2]})
        self.assertEqual(result['quantity_change']['new_images'], 6)
        self.assertNotIn('candidate_slots_by_panel', self.request)
        # Saving a different choice before resuming replaces, rather than adds to, the allowance.
        result = change_remaining_count(result, batches, 1)
        self.assertEqual(result['candidate_slots_by_panel'], {'A': [0, 1], 'B': [0, 1], 'C': [0]})

    def test_inflight_slot_is_retained_and_failed_slots_are_not_reused(self):
        batches = [self.batch('B', 0), self.batch('B', 1, 'failed'), self.batch('B', 2, 'running')]
        result = change_remaining_count(self.request, batches, 3)
        self.assertEqual(slots_for_panel(result, 'B'), [0, 1, 2, 3, 4])
        self.assertEqual(result['retired_candidate_slots_by_panel']['B'], [1])

    def test_validation_does_not_modify_request(self):
        original = copy.deepcopy(self.request)
        for count in (0, 17, True, '3', 1.5, None):
            with self.subTest(count=count), self.assertRaises(ValueError):
                change_remaining_count(self.request, [], count)
        self.assertEqual(self.request, original)
        with self.assertRaises(ValueError):
            change_remaining_count({**self.request, 'action': 'storyboard_all'}, [], 3)
        complete = [self.batch(p, s) for p in self.request['targets'] for s in range(2)]
        with self.assertRaisesRegex(ValueError, '全部生成'):
            change_remaining_count(self.request, complete, 3)

    def test_total_mode_reduces_eight_to_four_without_appending_four(self):
        request = {**self.request, 'candidate_count': 8}
        batches = [self.batch('A', slot) for slot in range(5)] + [self.batch('B', 0)]
        result = change_remaining_count(request, batches, 4, 'total')
        self.assertEqual(result['candidate_slots_by_panel'],
                         {'A': [0,1,2,3,4], 'B': [0,1,2,3], 'C': [0,1,2,3]})
        self.assertEqual(result['quantity_change']['new_images'], 7)
        again = change_remaining_count(result, batches, 4, 'total')
        self.assertEqual(again['candidate_slots_by_panel'], result['candidate_slots_by_panel'])


class PausedQuantityIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = Repository(self.root/'projects')
        self.addCleanup(self.repo._temporary.cleanup)
        self.pid = self.repo.create(None, True)
        self.doc = x.export_storyboard(self.repo, self.pid)
        self.doc['panels'] = self.doc['panels'][:3]
        for row in self.doc['panels']:
            row.update(positive='A reader holds a book in the library.', negative='')
        preview = x.preview(self.repo, self.pid, self.doc)
        self.bundle = x.save_import(self.repo, self.pid, {'document': self.doc, 'confirm': True,
                                                       'preview_hash': preview['preview_hash']})
        self.request = x.prepare_request(self.repo, self.pid, {'bundle': self.bundle['id'], 'candidate_count': 2})
        self.targets = self.request['targets']
        with patch('comic_workflow.jobs.DATA', self.root):
            self.jobs = Jobs()
        self.app = SimpleNamespace(repo=self.repo, jobs=self.jobs, model=Mock(), comfy=Mock())
        self.calls = []
        self.pause_once = True

    def wait_status(self, jid, status):
        until = time.monotonic() + 4
        while time.monotonic() < until:
            if self.jobs.get(jid)['status'] == status:
                return
            time.sleep(.01)
        self.fail(self.jobs.get(jid))

    def render(self, repo, pid, target, update, model, count, mode, loras, indices, **kwargs):
        slot = indices[0]
        self.calls.append((target, slot))
        image = f'quantity/{target}-{slot}.png'
        destination = repo.folder(pid)/'images'/image
        destination.parent.mkdir(parents=True, exist_ok=True)
        self.assertFalse(destination.exists(), 'Previously generated image would be overwritten')
        destination.write_bytes(b'saved image')
        batch = {'id': f'{target}-{slot}', 'status': 'done', 'panel_id': target,
                 'candidate_indices': indices, 'render_options': kwargs['render_options'],
                 'images': [{'file': image}]}
        index = comfyui.drawing_state(repo, pid)
        index['batches'].append(batch)
        e.save(repo.folder(pid)/'images/index.json', index)
        if self.pause_once and target == self.targets[1] and slot == 0:
            self.pause_once = False
            # The worker is registered before fn starts; no race with submit returning its ID.
            jid = next(iter(self.jobs.controls))
            self.jobs.control(jid, 'pause')
        return batch

    def cleanup_worker(self, jid):
        if jid in self.jobs.controls:
            self.jobs.control(jid, 'cancel', confirm=True)
        deadline = time.monotonic() + 4
        while jid in self.jobs.controls and time.monotonic() < deadline:
            time.sleep(.01)

    def test_live_pause_quantity_change_and_resume_preserve_completed_images(self):
        with patch.object(comfyui, 'run_batch', side_effect=self.render):
            jid = self.jobs.submit('draw', lambda update, key: x.draw_imported(
                self.app, self.pid, self.request, update, key), self.pid, request=self.request)
            self.addCleanup(self.cleanup_worker, jid)
            self.wait_status(jid, 'paused')
            original_files = {str(p): p.read_bytes() for p in (self.repo.folder(self.pid)/'images/quantity').glob('*')}
            self.assertEqual(len(self.calls), 3)
            result = self.jobs.update_paused_request(jid, lambda request: change_remaining_count(
                request, comfyui.drawing_state(self.repo, self.pid)['batches'], 3))
            self.assertEqual(result['status'], 'paused')
            self.assertEqual(self.request['candidate_count'], 3)  # Live closure sees the same request object.
            self.assertEqual(self.jobs.get(jid)['progress']['total'], 9)
            saved_request = copy.deepcopy(self.jobs.get(jid)['request'])
            self.jobs.control(jid, 'resume')
            self.wait_status(jid, 'done')
            self.cleanup_worker(jid)
            self.assertEqual(self.calls, [(self.targets[0], 0), (self.targets[0], 1),
                (self.targets[1], 0), (self.targets[1], 1), (self.targets[1], 2), (self.targets[1], 3),
                (self.targets[2], 0), (self.targets[2], 1), (self.targets[2], 2)])
            for name, content in original_files.items():
                self.assertEqual(Path(name).read_bytes(), content)
            self.assertEqual(self.jobs.get(jid)['progress'], {'completed': 9, 'total': 9, 'unit': '张已处理'})
            # Replaying the persisted schedule after a restart must reuse all nine images.
            x.draw_imported(self.app, self.pid, saved_request, lambda _: None, 'restarted')
            self.assertEqual(len(self.calls), 9)

    def test_running_jobs_cannot_be_changed_and_restored_paused_jobs_can(self):
        job = {'id': 'restored', 'project': self.pid, 'label': 'draw', 'created': '1',
               'status': 'running', 'request': self.request}
        self.jobs.save('restored', job)
        with self.assertRaisesRegex(ValueError, '已暂停'):
            self.jobs.update_paused_request('restored', lambda r: change_remaining_count(r, [], 3))
        self.assertEqual(self.jobs.get('restored')['request']['candidate_count'], 2)
        job['status'] = 'paused'
        self.jobs.save('restored', job)
        self.jobs.update_paused_request('restored', lambda r: change_remaining_count(r, [], 3))
        with patch('comic_workflow.jobs.DATA', self.root):
            restarted = Jobs()
        self.assertEqual(restarted.get('restored')['status'], 'paused')
        self.assertEqual(restarted.get('restored')['request']['candidate_count'], 3)
        self.assertTrue(all(slots == [0, 1, 2] for slots in restarted.get('restored')['request']['candidate_slots_by_panel'].values()))

    def test_restarted_worker_then_pause_and_change_uses_authoritative_request(self):
        from comic_workflow.server import Application
        self.request['candidate_count'] = 8
        self.jobs.save('restored', {'id':'restored', 'project':self.pid, 'label':'draw',
            'created':'1', 'status':'paused', 'request':self.request})
        with patch.object(comfyui, 'run_batch', side_effect=self.render):
            result = Application.action(self.app, {'action':'resume_job', 'job':'restored'})
            jid = result['job']
            self.addCleanup(self.cleanup_worker, jid)
            self.wait_status(jid, 'paused')
            self.assertEqual(len(self.calls), 9)
            Application.action(self.app, {'action':'set_remaining_candidate_count',
                'project':self.pid, 'job':jid, 'candidate_count':4, 'quantity_mode':'total'})
            Application.action(self.app, {'action':'resume_job', 'job':jid})
            self.wait_status(jid, 'done')
            self.cleanup_worker(jid)
            # The first complete panel is untouched; the next two total four each.
            self.assertEqual([sum(p == target for p, _ in self.calls) for target in self.targets], [8,4,4])
            self.assertEqual(self.jobs.get(jid)['request']['candidate_count'], 4)
            self.assertEqual(self.jobs.get(jid)['progress']['total'], 16)
