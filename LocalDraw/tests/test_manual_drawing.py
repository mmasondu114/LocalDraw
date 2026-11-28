import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from comic_workflow import engine as e, comfyui, manual_drawing as manual, prompt_exchange as exchange
from comic_workflow.character_binding import identity_hash
from comic_workflow.repository import Repository
from comic_workflow.app_core import StudioApp as Application


class ManualDrawingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Repository(Path(self.tmp.name)/"projects")
        self.pid = self.repo.create(None, True)
        self.p = self.repo.get(self.pid)
        self.panel = self.p["pages"]["P001"]["panels"][0]
        self.p.setdefault("drawing_prompts", {})[self.panel["id"]] = {
            "panel_hash":e.digest(self.panel), "identity_hash":identity_hash(self.p,self.panel),
            "payload":{"positive":"  A person reading.\nExactly as written.  ","negative":" blur\n"}}
        e.save(self.repo.folder(self.pid)/"project.json",self.p)
        self.args = {"base_hash":e.digest(self.p),"target":self.panel["id"],"force":True,"checkpoint":"test.safetensors"}
        self.app = SimpleNamespace(repo=self.repo,model=Mock(),comfy=Mock(),prepare_language=Mock())

    def confirmed(self):
        rows = manual.preview(self.repo,self.pid,self.args)["panels"]
        return {**self.args,"confirm_prompts":True,"prompt_rows":rows}

    def test_comic_size_is_frozen_even_if_saved_settings_change(self):
        from comic_workflow import drawing_settings
        prefs = {"mode": "none", "checkpoint": "test.safetensors", "items": [],
                 "aspect_ratio": "9:16", "resolution": "2048"}
        drawing_settings.save(self.repo, self.pid, prefs)
        frozen = manual.prepare_request(self.repo, self.pid, self.confirmed())
        drawing_settings.save(self.repo, self.pid, {**prefs, "resolution": "1024", "aspect_ratio": "1:1"})
        with patch.object(comfyui, "run_batch", return_value={"id": "offline"}) as draw:
            exchange.draw_imported(self.app, self.pid, frozen, lambda _: None, "size")
        for call in draw.call_args_list:
            self.assertEqual(call.kwargs["render_options"]["resolution"], "2048")
            self.assertEqual(call.kwargs["render_options"]["aspect_ratio"], "9:16")

    def test_default_two_literal_candidates_no_model_no_review_no_retry(self):
        frozen = manual.prepare_request(self.repo,self.pid,self.confirmed())
        self.assertEqual(frozen["candidate_count"],2)
        with patch.object(comfyui,"run_batch",side_effect=ValueError("render failed")) as draw:
            result = exchange.draw_imported(self.app,self.pid,frozen,lambda _:None,"run")
        self.assertEqual(draw.call_count,2)
        self.assertEqual(len(result["errors"]),2)
        for call in draw.call_args_list:
            self.assertEqual(call.kwargs["prompt_override"]["positive"],self.p["drawing_prompts"][self.panel["id"]]["payload"]["positive"])
            self.assertFalse(call.kwargs["review_required"])
            self.assertTrue(call.kwargs["render_options"]["direct_prompt"])
        self.app.prepare_language.assert_not_called()
        self.app.model.start.assert_not_called()
        self.assertEqual(self.repo.get(self.pid),self.p)

    def test_confirmation_blank_and_stale_prompts_rejected(self):
        with self.assertRaisesRegex(ValueError,"核对"):
            manual.prepare_request(self.repo,self.pid,self.args)
        for field,value in [("positive",""),("positive",None),("panel_hash","stale")]:
            args=self.confirmed();args["prompt_rows"][0][field]=value
            with self.subTest(field=field,value=value),self.assertRaises(ValueError):
                manual.prepare_request(self.repo,self.pid,args)
        args=self.confirmed();args["base_hash"]="stale"
        with self.assertRaises(ValueError):manual.prepare_request(self.repo,self.pid,args)

    def test_edited_text_persists_and_frozen_job_remains_unchanged(self):
        args=self.confirmed();args["prompt_rows"][0]["positive"]="  User edit\nKeep exactly. "
        first=manual.prepare_request(self.repo,self.pid,args)
        self.assertEqual(manual.preview(self.repo,self.pid,self.args)["panels"][0]["positive"],args["prompt_rows"][0]["positive"])
        args["prompt_rows"][0]["positive"]="Next edit"
        manual.prepare_request(self.repo,self.pid,args)
        self.assertEqual(exchange.load_import(self.repo,self.pid,first["bundle"])["panels"][0]["positive"],"  User edit\nKeep exactly. ")

    def test_missing_prompts_are_blank_in_preview_and_selected_images_preserved(self):
        args={"base_hash":e.digest(self.p)}
        preview=manual.preview(self.repo,self.pid,args)
        self.assertTrue(any(not row["positive"] for row in preview["panels"]))
        index={"batches":[],"selections":{self.panel["id"]:{"panel_hash":e.digest(self.panel),"image":{"id":"keep"}}}}
        e.save(self.repo.folder(self.pid)/"images/index.json",index)
        preview=manual.preview(self.repo,self.pid,args)
        self.assertIn(self.panel["id"],preview["skipped"])
        self.assertEqual(comfyui.drawing_state(self.repo,self.pid),index)

    def test_drawing_another_panel_keeps_newer_import_and_unrelated_manual_text(self):
        args=self.confirmed()
        manual.prepare_request(self.repo,self.pid,args)
        doc=exchange.export_storyboard(self.repo,self.pid)
        first,second,third=doc["panels"][:3]
        for item in [second,third]:
            req={**self.args,"target":item["panel_id"]}
            rows=manual.preview(self.repo,self.pid,req)["panels"]
            rows[0]["positive"]="Manually edited."
            manual.prepare_request(self.repo,self.pid,{**req,"confirm_prompts":True,"prompt_rows":rows})
        first.update(positive="New import.",negative="")
        doc["panels"]=[first]
        exchange.save_import(self.repo,self.pid,dict(document=doc,confirm=True,preview_hash=e.digest(doc)))
        req={**self.args,"target":second["panel_id"]}
        rows=manual.preview(self.repo,self.pid,req)["panels"]
        manual.prepare_request(self.repo,self.pid,{**req,"confirm_prompts":True,"prompt_rows":rows})
        rows=manual.preview(self.repo,self.pid,{"base_hash":self.args["base_hash"]})["panels"]
        self.assertEqual([r["positive"] for r in rows[:3]],["New import.","Manually edited.","Manually edited."])

    def test_server_freezes_manual_job_before_submission(self):
        app=Application.__new__(Application);app.repo=self.repo;app.jobs=Mock()
        app.jobs.submit.return_value="job"
        for action in ["draw","draw_all"]:
            request={**self.confirmed(),"action":action,"project":self.pid}
            self.assertEqual(app.action(request),{"job":"job"})
            frozen=app.jobs.submit.call_args.kwargs["request"]
            self.assertEqual(frozen["action"],"draw_imported")
            self.assertEqual(frozen["candidate_count"],2)
        app.jobs.submit.reset_mock()
        with self.assertRaises(ValueError):app.action({**self.args,"action":"draw","project":self.pid})
        app.jobs.submit.assert_not_called()

    def test_explicit_queued_range_adds_candidates_without_changing_selections(self):
        index={"batches":[],"selections":{self.panel["id"]:{"panel_hash":e.digest(self.panel),"image":{"id":"keep-selected"}}}}
        e.save(self.repo.folder(self.pid)/"images/index.json",index)
        request={"base_hash":e.digest(self.p),"targets":[self.panel["id"]],"force":True,"checkpoint":"test.safetensors","candidate_count":4}
        preview=manual.preview(self.repo,self.pid,request)
        self.assertEqual([row["panel_id"] for row in preview["panels"]],[self.panel["id"]])
        frozen=manual.prepare_request(self.repo,self.pid,{**request,"confirm_prompts":True,"prompt_rows":preview["panels"]})
        self.assertEqual(frozen["targets"],[self.panel["id"]])
        self.assertEqual(frozen["candidate_count"],4)
        self.assertEqual(comfyui.drawing_state(self.repo,self.pid),index)

    def test_queue_freezes_latest_lora_and_text_for_each_task_without_starting(self):
        from comic_workflow.night_queue import NightQueue
        app=Application.__new__(Application);app.repo=self.repo;app.jobs=Mock()
        app.night_queue=NightQueue(app);self.addCleanup(app.night_queue.close)
        with patch("comic_workflow.drawing_settings.load", return_value={"checkpoint":"test.safetensors","mode":"manual","items":[]}), \
             patch("comic_workflow.drawing_settings.loras_for_panel", return_value=[{"file":"first.safetensors","weight":.6}]) as loras:
            args={**self.confirmed(),"action":"queue_drawing","project":self.pid}
            app.action(args)
            loras.return_value=[{"file":"last.safetensors","weight":.8}]
            args["prompt_rows"][0]["positive"]="Changed for the next task"
            app.action(args)
        first,second=[i["request"] for i in app.night_queue.state["items"]]
        key=self.panel["id"]
        self.assertEqual(first["loras_by_panel"][key][0]["file"],"first.safetensors")
        self.assertEqual(second["loras_by_panel"][key][0]["file"],"last.safetensors")
        self.assertNotEqual(exchange.load_import(self.repo,self.pid,first["bundle"])["panels"][0]["positive"],
                            exchange.load_import(self.repo,self.pid,second["bundle"])["panels"][0]["positive"])
        preview=app.night_queue.preview(app.night_queue.state["items"][0]["id"])
        self.assertEqual(preview["panels"][0]["loras"][0]["file"],"first.safetensors")
        self.assertNotEqual(preview["panels"][0]["positive"],"Changed for the next task")
        app.jobs.submit.assert_not_called()
