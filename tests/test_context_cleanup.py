import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from comic_workflow.model import ModelManager, GENERATION_LOCK
from comic_workflow.jobs import Jobs
from comic_workflow.server import Application

class ContextCleanupTests(unittest.TestCase):
    def test_llama_erases_context_but_keeps_weights_loaded(self):
        model=ModelManager();model.process=Mock();model.process.poll.return_value=None
        with patch('comic_workflow.model.request_json',side_effect=[[{'id':0,'is_processing':False}],{'id_slot':0,'n_erased':120}]) as call, patch.object(model,'stop') as stop:
            result=model.clear_context()
        self.assertEqual(result['method'],'erase_slots');stop.assert_not_called()
        self.assertTrue(call.call_args_list[-1].args[0].endswith('/slots/0?action=erase'))
    def test_strata_stays_loaded_and_idle_manager_does_not_touch_external_server(self):
        model=ModelManager()
        with patch('comic_workflow.model.request_json') as call:
            self.assertEqual(model.clear_context()['method'],'already_unloaded');call.assert_not_called()
        model.strata=Mock()
        model.strata.clear_context.return_value={'status':'cleared','method':'independent_requests'}
        with patch.object(model,'stop') as stop:self.assertEqual(model.clear_context()['method'],'independent_requests')
        stop.assert_not_called();model.strata.clear_context.assert_called_once()
    def test_unsupported_erase_reports_failure_without_unloading(self):
        model=ModelManager();model.process=Mock();model.process.poll.return_value=None
        with patch('comic_workflow.model.request_json',side_effect=ValueError('unsupported')),patch.object(model,'stop') as stop:
            self.assertEqual(model.clear_context()['status'],'failed')
        stop.assert_not_called()
    def test_busy_server_is_not_interrupted(self):
        model=ModelManager();model.process=Mock();model.process.poll.return_value=None
        with patch('comic_workflow.model.request_json',return_value=[{'id':0,'is_processing':True}]),patch.object(model,'stop') as stop:
            self.assertEqual(model.clear_context()['status'],'deferred')
        stop.assert_not_called()
    def test_job_result_is_saved_and_cleanup_precedes_completion(self):
        for fails in (False,True):
            with self.subTest(fails=fails),tempfile.TemporaryDirectory() as tmp,patch('comic_workflow.jobs.DATA',Path(tmp)):
                events=[];done=threading.Event()
                def finish(job):
                    self.assertTrue(GENERATION_LOCK.locked());events.append('cleanup');done.set()
                    return {'status':'cleared'}
                jobs=Jobs(on_finish=finish)
                def work(update,jid):
                    events.append('output_saved')
                    if fails:raise ValueError('generation failed')
                    return {'text':'keep this result'}
                jid=jobs.submit('文字任务',work)
                self.assertTrue(done.wait(2))
                deadline=time.monotonic()+3
                while jid in jobs.controls and time.monotonic()<deadline:time.sleep(.01)
                saved=jobs.get(jid)
                self.assertEqual(events,['output_saved','cleanup'])
                self.assertEqual(saved['status'],'failed' if fails else 'done')
                self.assertEqual(saved['context_cleanup']['status'],'cleared')
                if not fails:self.assertEqual(saved['result']['text'],'keep this result')
    def test_manual_model_warmup_stays_loaded(self):
        app=Application.__new__(Application);app.model=Mock()
        self.assertIsNone(app.clear_finished_context({'label':'启动 FlashNext'}))
        app.model.clear_context.assert_not_called()
        app.clear_finished_context({'label':'编写分镜'})
        app.model.clear_context.assert_called_once()
    def test_start_button_uses_flashnext_64k_not_27b(self):
        from comic_workflow.paths import settings
        app=Application.__new__(Application);app.model=Mock();app.jobs=Mock()
        observed=[]
        def submit(label,work):
            self.assertEqual(label,'启动 FlashNext');work(Mock(),'start');return 'job'
        app.jobs.submit.side_effect=submit
        app.model.start.side_effect=lambda **kw:observed.append(settings())
        with patch('comic_workflow.server.comfyui.discover',return_value={'ready':False}):
            app.action({'action':'start_model'})
        self.assertEqual(observed[0]['model_backend'],'strata')
        self.assertIn('flash-next',observed[0]['model_name'])
        self.assertEqual(observed[0]['context_size'],65536)
    def test_strata_isolated_context_and_drawing_keep_process(self):
        from comic_workflow.strata_model import StrataManager
        strata=StrataManager({'model_port':8094})
        strata.process=Mock();strata.process.poll.return_value=None;strata.isolated_context=True
        owned=strata.process
        with patch('comic_workflow.model.request_json',return_value={'status':'ok'}) as call,patch.object(strata,'stop') as stop:
            self.assertEqual(strata.clear_context()['method'],'independent_requests')
            strata.reserve_for_drawing()
        self.assertIs(strata.process,owned);stop.assert_not_called()
        self.assertEqual(call.call_args.args[1],{'reserve_mib':12288})
if __name__=='__main__':unittest.main()
