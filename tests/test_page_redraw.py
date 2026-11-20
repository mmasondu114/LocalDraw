import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from comic_workflow import engine as e, page_redraw as redraw, prompt_exchange as exchange
from comic_workflow.repository import Repository

class PageRedrawTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.repo=Repository(Path(self.tmp.name)/'projects');self.pid=self.repo.create(None,True)
        self.project=self.repo.get(self.pid)
        document=exchange.export_storyboard(self.repo,self.pid)
        for row in document['panels']:row.update(positive='POV, two cups on the table.',negative='text, watermark')
        preview=exchange.preview(self.repo,self.pid,document)
        exchange.save_import(self.repo,self.pid,dict(document=document,preview_hash=preview['preview_hash'],confirm=True))
        self.args=dict(page_id='P001',revision_note='只留一个杯子',base_hash=e.digest(self.project),candidate_count=8,checkpoint='test.safetensors')
        self.app=SimpleNamespace(repo=self.repo,model=Mock(),prepare_language=Mock())
        self.before=copy.deepcopy(self.project)
    def result(self,prompt,context,schema,*args,**kw):
        self.assertEqual(context['user_correction'],'只留一个杯子')
        old=context['original_prompts']
        return dict(panel_id=old['panel_id'],positive='POV, exactly one cup on the table.',negative=old['negative']),{}
    def test_blank_bypasses_language_model_and_preserves_page_scope(self):
        request=redraw.prepare(self.repo,self.pid,{**self.args,'revision_note':'  ','targets':['P999-K01']})
        expected=[p['id'] for p in self.project['pages']['P001']['panels']]
        self.assertEqual(request['targets'],expected)
        self.assertEqual(request['candidate_count'],8)
        old=request['bundle']
        with patch.object(redraw.creative,'structured') as model:redraw.apply_revision(self.app,self.pid,request,lambda _:None,'job')
        model.assert_not_called();self.app.prepare_language.assert_not_called();self.assertEqual(request['bundle'],old)
    def test_revised_prompts_reach_drawing_and_resume_without_rewriting(self):
        request=redraw.prepare(self.repo,self.pid,self.args);old=request['bundle']
        with patch.object(redraw.creative,'structured',side_effect=self.result) as model:
            redraw.apply_revision(self.app,self.pid,request,lambda _:None,'job')
            self.assertEqual(model.call_count,len(request['targets']))
            model.reset_mock()
            redraw.apply_revision(self.app,self.pid,request,lambda _:None,'resume')
            model.assert_not_called()
        self.assertNotEqual(old,request['bundle']);self.app.model.stop.assert_not_called();self.app.model.clear_context.assert_called()
        rows=exchange.load_import(self.repo,self.pid,request['bundle'])['panels']
        self.assertTrue(all(r['positive']=='POV, exactly one cup on the table.' for r in rows))
        self.app.comfy=Mock()
        with patch('comic_workflow.comfyui.run_batch',return_value={'id':'fake'}) as draw:
            exchange.draw_imported(self.app,self.pid,request,lambda _:None,'drawing')
        self.app.model.stop.assert_not_called();self.app.model.prepare_redraw.assert_called_once()
        self.assertEqual(draw.call_count,len(request['targets'])*8)
        for call in draw.call_args_list:
            self.assertIn(call.args[2],request['targets'])
            self.assertEqual(call.kwargs['prompt_override']['positive'],'POV, exactly one cup on the table.')
        self.assertEqual(self.repo.get(self.pid),self.before)
    def test_unchanged_correction_stops_before_drawing(self):
        request=redraw.prepare(self.repo,self.pid,self.args)
        def unchanged(prompt,context,*a,**kw):
            r=context['original_prompts'];return {k:r[k] for k in ('panel_id','positive','negative')},{}
        with patch.object(redraw.creative,'structured',side_effect=unchanged),patch('comic_workflow.comfyui.run_batch') as draw:
            with self.assertRaisesRegex(ValueError,'未按问题修改'):exchange.draw_imported(self.app,self.pid,request,lambda _:None,'job')
        draw.assert_not_called()
    def test_failure_does_not_fall_back_to_old_prompts(self):
        request=redraw.prepare(self.repo,self.pid,self.args)
        with patch.object(redraw.creative,'structured',side_effect=RuntimeError('model failed')),patch('comic_workflow.comfyui.run_batch') as draw:
            with self.assertRaisesRegex(RuntimeError,'model failed'):exchange.draw_imported(self.app,self.pid,request,lambda _:None,'job')
        draw.assert_not_called();self.app.model.stop.assert_not_called();self.app.model.clear_context.assert_called()
    def test_single_panel_scope_cannot_expand_to_neighboring_panels(self):
        selected=self.project['pages']['P001']['panels'][1]['id']
        request=redraw.prepare(self.repo,self.pid,{**self.args,'panel_id':selected,
            'targets':[p['id'] for p in self.project['pages']['P001']['panels']]},single_panel=True)
        self.assertEqual(request['targets'],[selected]);self.assertEqual(request['panel_id'],selected)
        self.app.comfy=Mock()
        with patch.object(redraw.creative,'structured',side_effect=self.result) as model,patch('comic_workflow.comfyui.run_batch',return_value={'id':'fake'}) as draw:
            exchange.draw_imported(self.app,self.pid,request,lambda _:None,'single-panel')
        self.assertEqual(model.call_count,1);self.assertEqual(draw.call_count,8)
        self.assertTrue(all(c.args[2]==selected for c in draw.call_args_list))
        self.app.model.stop.assert_not_called()
        self.assertEqual(self.repo.get(self.pid),self.before)
        prompts=e.read(self.repo.folder(self.pid)/'manual-prompts.json')['panels']
        self.assertTrue(all(r['positive']=='POV, two cups on the table.' for r in prompts if r['panel_id']!=selected))
    def test_single_panel_requires_a_valid_panel_on_current_page(self):
        for value in (None,'P999-K01','P001-K99',12):
            with self.assertRaises(ValueError):
                redraw.prepare(self.repo,self.pid,{**self.args,'panel_id':value},single_panel=True)
        request=redraw.prepare(self.repo,self.pid,{**self.args,'panel_id':'P001-K01','revision_note':''},single_panel=True)
        with patch.object(redraw.creative,'structured') as model:
            redraw.apply_revision(self.app,self.pid,request,lambda _:None,'empty')
        self.assertEqual(request['targets'],['P001-K01']);model.assert_not_called()
    def test_validation(self):
        for extra in ({'page_id':'missing'},{'revision_note':12},{'revision_note':'x'*4001},{'base_hash':'stale'}):
            with self.assertRaises(ValueError):redraw.prepare(self.repo,self.pid,{**self.args,**extra})
if __name__=='__main__':unittest.main()
