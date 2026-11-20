import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from comic_workflow import engine as e, problem_panels as issues, prompt_exchange as exchange
from comic_workflow.repository import Repository


class ProblemPanelsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.repo=Repository(Path(self.tmp.name)/'projects');self.pid=self.repo.create(None,True)
        self.before=copy.deepcopy(self.repo.get(self.pid))
        doc=exchange.export_storyboard(self.repo,self.pid)
        for row in doc['panels']:row.update(positive='POV, two cups.',negative='watermark')
        preview=exchange.preview(self.repo,self.pid,doc)
        exchange.save_import(self.repo,self.pid,dict(document=doc,preview_hash=preview['preview_hash'],confirm=True))
        self.keys=[r['panel_id'] for r in doc['panels'][:2]]
        for index,key in enumerate(self.keys):
            issues.save(self.repo,self.pid,dict(panel_id=key,note='只要一个杯子',candidate_count=index+2))

    def completed(self):
        document=issues.export(self.repo,self.pid)
        for row in document['panels']:
            row.update(understanding='画面只应有一个杯子。',changes='移除多余杯子。',positive='POV, exactly one cup.',negative='watermark')
        return document

    def test_persistence_and_atomic_import_before_drawing(self):
        document=self.completed();issues.import_result(self.repo,self.pid,document)
        self.assertTrue(all(r['ready'] for r in issues.state(self.repo,self.pid)['items']))
        self.assertEqual(self.repo.get(self.pid),self.before)
        self.assertFalse((self.repo.folder(self.pid)/'manual-prompts.json').exists())
        self.assertEqual(len(issues.load(Repository(self.repo.root),self.pid)['items']),2)

    def test_changed_note_or_scope_rejects_old_gpt_result(self):
        document=self.completed()
        issues.save(self.repo,self.pid,dict(panel_id=self.keys[0],note='换成红杯子'))
        with self.assertRaisesRegex(ValueError,'变化'):issues.import_result(self.repo,self.pid,document)
        self.assertFalse(any(r['ready'] for r in issues.state(self.repo,self.pid)['items']))
        document=self.completed();issues.save(self.repo,self.pid,dict(panel_id=self.keys[1],marked=False))
        with self.assertRaisesRegex(ValueError,'范围'):issues.import_result(self.repo,self.pid,document)

    def test_counts_do_not_invalidate_understanding(self):
        issues.import_result(self.repo,self.pid,self.completed())
        issues.save(self.repo,self.pid,dict(panel_id=self.keys[0],candidate_count=8))
        self.assertTrue(all(r['ready'] for r in issues.state(self.repo,self.pid)['items']))
        for value in (0,17,True,'8'):
            with self.assertRaises(ValueError):issues.save(self.repo,self.pid,dict(panel_id=self.keys[0],candidate_count=value))

    def test_import_rejects_incomplete_duplicate_and_other_project(self):
        document=self.completed()
        variants=[]
        bad=copy.deepcopy(document);bad['panels'].pop();variants.append(bad)
        bad=copy.deepcopy(document);bad['panels'].append(bad['panels'][0]);variants.append(bad)
        bad=copy.deepcopy(document);bad['project_id']='other';variants.append(bad)
        bad=copy.deepcopy(document);bad['panels'][1]['understanding']='';variants.append(bad)
        for bad in variants:
            with self.assertRaises(ValueError):issues.import_result(self.repo,self.pid,bad)
        self.assertFalse(any(r['ready'] for r in issues.state(self.repo,self.pid)['items']))

    def test_all_redraw_uses_per_panel_counts_and_preserves_unmarked(self):
        issues.import_result(self.repo,self.pid,self.completed())
        request=issues.prepare_drawing(self.repo,self.pid)
        self.assertEqual(request['targets'],self.keys)
        self.assertEqual({k:len(v) for k,v in request['candidate_slots_by_panel'].items()},dict(zip(self.keys,[2,3])))
        app=SimpleNamespace(repo=self.repo,model=Mock(),comfy=Mock())
        with patch('comic_workflow.comfyui.run_batch',return_value={'id':'fake'}) as draw:
            exchange.draw_imported(app,self.pid,request,Mock(),'job')
        self.assertEqual([c.args[2] for c in draw.call_args_list],[self.keys[0]]*2+[self.keys[1]]*3)
        self.assertTrue(all(c.kwargs['prompt_override']['positive']=='POV, exactly one cup.' for c in draw.call_args_list))
        app.model.stop.assert_not_called();app.model.prepare_redraw.assert_called_once()
        self.assertEqual(self.repo.get(self.pid),self.before)
        self.assertEqual(len(issues.state(self.repo,self.pid)['items']),2)

    def test_local_understanding_accepts_explained_random_render_defect(self):
        document=issues.export(self.repo,self.pid)
        app=SimpleNamespace(repo=self.repo,model=Mock(),prepare_language=Mock())
        def respond(prompt,row,*a,**kw):
            return dict(understanding='提示词正确，但图片随机生成失误。',changes='保持描述，重新抽取候选。',
                        positive=row['original_positive'],negative=row['original_negative']),{}
        with patch.object(issues.creative,'structured',side_effect=respond):
            issues.understand(app,self.pid,document,Mock(),'job')
        self.assertTrue(all(r['ready'] for r in issues.state(self.repo,self.pid)['items']))
        app.model.stop.assert_not_called();app.model.clear_context.assert_called_once()

    def test_blank_issues_and_unprepared_draw_do_not_submit(self):
        with self.assertRaises(ValueError):issues.prepare_drawing(self.repo,self.pid)
        issues.save(self.repo,self.pid,dict(panel_id=self.keys[0],note=''))
        with self.assertRaises(ValueError):issues.export(self.repo,self.pid)

    def test_one_click_applies_notes_and_reuses_same_drawing_on_resume(self):
        document=issues.export(self.repo,self.pid)
        app=SimpleNamespace(repo=self.repo,model=Mock(),prepare_language=Mock())
        result=dict(understanding='只要一个杯子',changes='去掉第二个',positive='POV, a single cup',negative='')
        with patch.object(issues.creative,'structured',return_value=(result,{})) as interpret, \
             patch.object(exchange,'draw_imported',return_value={}) as draw:
            for _ in range(2):issues.understand_and_redraw(app,self.pid,document,Mock(),'job')
        self.assertEqual(interpret.call_count,len(self.keys))
        frozen=draw.call_args_list[0].args[2]
        self.assertEqual(frozen,draw.call_args_list[1].args[2])
        self.assertEqual(frozen['targets'],self.keys)
        self.assertEqual([len(v) for v in frozen['candidate_slots_by_panel'].values()],[2,3])
        self.assertTrue(all(r['positive']==result['positive'] for r in exchange.load_import(
            self.repo,self.pid,frozen['bundle'])['panels']))

if __name__=='__main__':unittest.main()
