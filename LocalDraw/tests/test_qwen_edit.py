import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from PIL import Image
from comic_workflow import qwen_edit as q, anima_consistency as ac
from comic_workflow import engine as e, prompt_exchange as exchange, prompt_models, comfyui, lora_folders
from comic_workflow.repository import Repository


class QwenEditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for module in (q, ac):
            patcher = patch.object(module, 'DATA', self.root / 'data')
            patcher.start(); self.addCleanup(patcher.stop)
        self.repo = Repository(self.root / 'data/projects')
        self.pid = self.repo.create(None, True)
        self.doc = exchange.export_storyboard(self.repo, self.pid)
        self.doc['panels'] = self.doc['panels'][:1]
        self.row = self.doc['panels'][0]
        self.row.update(positive='Change image 1 to a right profile. Keep the same person.', negative='')
        preview = exchange.preview(self.repo, self.pid, self.doc)
        self.bundle = exchange.save_import(self.repo, self.pid, dict(document=self.doc, preview_hash=preview['preview_hash'], confirm=True))
        images = []
        for i, color in enumerate(('red', 'green', 'blue')):
            file = f'source/{i}.png'
            target = self.repo.folder(self.pid) / 'images' / file
            target.parent.mkdir(parents=True, exist_ok=True)
            Image.new('RGB', (32, 24), color).save(target)
            images.append({'id': f'ref{i}', 'file': file})
        e.save(self.repo.folder(self.pid)/'images/index.json', {'batches': [{'id': 'source', 'status': 'done', 'images': images}], 'selections': {}})
        self.panel = next(p for _, p in exchange.panels(self.repo.get(self.pid)) if p['id'] == self.row['panel_id'])
        self.refs = q.freeze_references(self.repo, self.pid, self.panel, ['ref0', 'ref1'])
        self.cfg = dict(width=1024, height=768, edit_references=self.refs, **prompt_models.defaults(q.CHECKPOINT))

    def request(self, **changes):
        args = dict(bundle=self.bundle['id'], checkpoint=q.CHECKPOINT, candidate_count=4,
                    references_by_panel={self.row['panel_id']: ['ref0', 'ref1']})
        args.update(changes)
        with patch.object(q, 'freeze_assets', return_value=[{'file': 'fixture', 'sha256': 'fixture'}]):
            return exchange.prepare_request(self.repo, self.pid, args)

    def test_ordered_images_reach_both_conditioning_branches_and_sampler(self):
        graph = comfyui.graph(q.CHECKPOINT, self.row['positive'], '', 17, self.cfg)
        self.assertEqual(graph['2']['inputs']['prompt'], self.row['positive'])
        self.assertEqual(graph['3']['inputs']['prompt'], '')
        self.assertEqual(graph['5']['inputs']['seed'], 17)
        self.assertEqual(graph['5']['inputs']['positive'], ['edit-positive', 0])
        self.assertEqual(graph['edit-positive']['inputs']['conditioning'], ['2', 0])
        for branch in ('2', '3'):
            self.assertEqual(graph[branch]['inputs']['image1'], ['edit-base-scale', 0])
            self.assertEqual(graph[branch]['inputs']['image2'], ['edit-image-2', 0])
        self.assertEqual(graph['edit-image-2']['inputs']['image'], self.refs[1]['reference'])
        self.assertFalse(any('Lora' in n['class_type'] or 'Cosmos' in n['class_type'] for n in graph.values()))

    def test_missing_changed_duplicate_and_escaping_frozen_pixels_rejected(self):
        for refs in ([], self.refs*2, [self.refs[0], self.refs[0]],
                     [{**self.refs[0], 'reference': '../../outside.png'}],
                     [{**self.refs[0], 'reference_sha256': 'wrong'}]):
            with self.subTest(refs=refs), self.assertRaises(ValueError): q.validate_frozen(refs)
        path = q.DATA/'comfy-input'/self.refs[0]['reference']
        path.write_bytes(b'changed')
        with self.assertRaises(ValueError): q.validate_frozen(self.refs)

    def test_source_changes_do_not_change_frozen_reference(self):
        path = self.repo.folder(self.pid)/'images/source/0.png'
        path.write_bytes(b'replaced source')
        self.assertEqual(q.validate_frozen(self.refs), self.refs)

    def test_foreign_images_excess_refs_and_nonvisible_character_rejected(self):
        for selections in (['unknown'], ['ref0']*2, ['ref0','ref1','ref2','ref3'],
                           [{'image_id':'ref0','role':'identity','character_id':'C999'}]):
            with self.subTest(selections=selections), self.assertRaises(ValueError):
                q.freeze_references(self.repo, self.pid, self.panel, selections)

    def test_incompatible_lora_and_distorting_aspect_rejected(self):
        with self.assertRaises(ValueError): q.graph(q.CHECKPOINT, 'x', '', 1, self.cfg, [{'file': ac.LORA}])
        with self.assertRaises(ValueError): q.graph(q.CHECKPOINT, 'x', '', 1, {**self.cfg, 'width': 768, 'height': 1024})
        for name in ('anime/edit/AnimeEditV2.safetensors', 'sdxl/face.safetensors', 'legacy.safetensors'):
            self.assertFalse(lora_folders.matches(name, q.CHECKPOINT))

    def test_each_target_requires_explicit_references_and_assets(self):
        with self.assertRaises(ValueError): self.request(references_by_panel={})
        with self.assertRaises(ValueError): self.request(references_by_panel={self.row['panel_id']: 'ref0'})
        with patch.object(q, 'ROOT', self.root), self.assertRaises(ValueError): q.freeze_assets()

    def test_asset_hash_invalidated_after_file_changes(self):
        path = self.root/'models/test.bin'; path.parent.mkdir(parents=True);path.write_bytes(b'first')
        digest = hashlib.sha256(b'first').hexdigest()
        with patch.object(q, 'ROOT', self.root), patch.object(q, 'ASSETS', {'test.bin': digest}):
            self.assertEqual(q.freeze_assets()[0]['sha256'], digest)
            path.write_bytes(b'second version')
            with self.assertRaises(ValueError): q.freeze_assets()

    def test_four_serial_candidates_and_resume_keep_frozen_refs_no_lora(self):
        request = self.request()
        expected = copy.deepcopy(request)
        app = SimpleNamespace(repo=self.repo, model=Mock(), comfy=Mock())
        calls = []
        def render(repo, pid, target, update, model, count, mode, loras, indices, **kwargs):
            opts = kwargs['render_options'];calls.append(copy.deepcopy(opts))
            self.assertEqual(opts['edit_references'], self.refs)
            self.assertEqual(opts['edit_recipe'], q.RECIPE)
            self.assertFalse(opts['consistency_lora']);self.assertEqual(loras, [])
            self.assertEqual(kwargs['prompt_override']['positive'], self.row['positive'])
            file = f'out/{len(calls)}.png';path=repo.folder(pid)/'images'/file
            path.parent.mkdir(exist_ok=True);path.write_bytes(b'fixture')
            batch = dict(id=str(len(calls)), panel_id=target, status='done', candidate_indices=indices,
                         render_options=copy.deepcopy(opts), images=[dict(file=file)])
            index = comfyui.drawing_state(repo,pid);index['batches'].append(batch)
            e.save(repo.folder(pid)/'images/index.json', index)
            opts['edit_references'].clear()
            return batch
        with patch.object(comfyui,'run_batch',side_effect=render):
            exchange.draw_imported(app,self.pid,request,lambda _:None,'first')
            exchange.draw_imported(app,self.pid,request,lambda _:None,'resume')
        self.assertEqual(len(calls),4)
        self.assertEqual(request, expected)


if __name__ == '__main__':
    unittest.main()
