import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from comic_workflow import engine as e, prompt_exchange as x, comfyui, drawing_settings
from comic_workflow.repository import Repository


class PromptExchangeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Repository(Path(self.tmp.name) / "projects")
        self.pid = self.repo.create(None, True)
        self.project = self.repo.get(self.pid)
        self.doc = x.export_storyboard(self.repo, self.pid)
        for row in self.doc["panels"]:
            row.update(positive="  A person returning a book.\nQuiet library.  ", negative=" text, blur\n")

    def save(self, doc=None):
        doc = doc or self.doc
        result = x.preview(self.repo, self.pid, doc)
        return x.save_import(self.repo, self.pid, {"document": doc, "preview_hash": result["preview_hash"], "confirm": True})

    def test_export_contains_actions_characters_and_stable_ids(self):
        row = self.doc["panels"][0]
        self.assertTrue(row["storyboard"]["action"])
        self.assertTrue(row["storyboard"]["characters"])
        self.assertTrue(row["page_id"] and row["panel_id"])
        self.assertEqual(self.repo.get(self.pid), self.project)
        self.assertTrue(self.doc["prompt_writing_guidance"])
        self.assertEqual({c["id"] for c in row["storyboard"]["visible_character_profiles"]},
                         {c["in_this_panel"]["character_id"] for c in row["storyboard"]["characters"]})

    def test_preview_and_import_preserve_text_and_story(self):
        before = e.digest(self.project)
        self.assertFalse((self.repo.folder(self.pid)/"prompt-imports").exists())
        bundle = self.save()
        self.assertEqual(bundle["panels"][0]["positive"], self.doc["panels"][0]["positive"])
        self.assertEqual(bundle["panels"][0]["negative"], self.doc["panels"][0]["negative"])
        self.assertEqual(e.digest(self.repo.get(self.pid)), before)
        self.assertEqual(x.current_import(self.repo, self.pid)["bundle"], bundle)
        self.assertIn("identical_prompts", {w["code"] for w in x.current_import(self.repo, self.pid)["warnings"]})

    def test_partial_import_reports_missing_without_inventing_prompts(self):
        self.doc["panels"] = self.doc["panels"][:1]
        result = x.preview(self.repo, self.pid, self.doc)
        self.assertTrue(result["valid"])
        self.assertTrue(result["missing"])
        self.assertEqual(len(self.save()["panels"]), 1)

    def test_invalid_files_cannot_be_saved(self):
        cases = []
        for changes in [{"project_id":"wrong"}, {"storyboard_hash":"old"}, {"format":"wrong"}]:
            cases.append({**copy.deepcopy(self.doc), **changes})
        duplicate=copy.deepcopy(self.doc);duplicate["panels"].append(duplicate["panels"][0]);cases.append(duplicate)
        for field,value in [("panel_id","P999-K99"),("positive",""),("negative",None),("page_id","P999")]:
            doc=copy.deepcopy(self.doc);doc["panels"][0][field]=value;cases.append(doc)
        for doc in cases:
            with self.subTest(doc=doc["panels"][0]["panel_id"]):
                self.assertFalse(x.preview(self.repo,self.pid,doc)["valid"])
                with self.assertRaises(ValueError):self.save(doc)

    def test_confirmation_and_preview_hash_are_required(self):
        for args in [{"confirm":False},{"confirm":True,"preview_hash":"changed"}]:
            with self.assertRaises(ValueError):
                x.save_import(self.repo,self.pid,{"document":self.doc,**args})

    def test_story_change_invalidates_import_but_keeps_previous_file(self):
        bundle=self.save()
        self.project["pages"][self.doc["panels"][0]["page_id"]]["panels"][0]["main_instant"]="Someone closes a book."
        e.save(self.repo.folder(self.pid)/"project.json",self.project)
        self.assertTrue(x.current_import(self.repo,self.pid)["stale"])
        with self.assertRaises(ValueError):x.prepare_request(self.repo,self.pid,{"bundle":bundle["id"]})
        self.assertEqual(x.load_import(self.repo,self.pid,bundle["id"]),bundle)

    def test_render_uses_two_candidates_by_default_and_resumes_without_duplicates(self):
        self.doc["panels"]=self.doc["panels"][:2]
        bundle=self.save();request=x.prepare_request(self.repo,self.pid,{"bundle":bundle["id"],"checkpoint":"test.safetensors"})
        app=SimpleNamespace(repo=self.repo,model=Mock(),comfy=Mock()); calls=[]
        def render(repo,pid,target,update,model,count,mode,loras,indices,**kwargs):
            calls.append(kwargs)
            self.assertEqual(kwargs["prompt_override"]["positive"],self.doc["panels"][0]["positive"])
            self.assertFalse(kwargs["review_required"])
            self.assertEqual(loras,[])
            index=comfyui.drawing_state(repo,pid)
            image_file=str(len(calls))+"/1.png"
            path=repo.folder(pid)/"images"/image_file;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b"test")
            batch={"id":str(len(calls)),"status":"done","panel_id":target,"candidate_indices":indices,
                   "render_options":kwargs["render_options"],"images":[{"id":str(len(calls)),"file":image_file}]}
            index["batches"].append(batch);e.save(repo.folder(pid)/"images/index.json",index)
            return batch
        with patch.object(comfyui,"run_batch",side_effect=render):
            result=x.draw_imported(app,self.pid,request,lambda _:None,"first")
            x.draw_imported(app,self.pid,request,lambda _:None,"resume")
        self.assertEqual(result["total"],4)
        self.assertEqual(len(calls),4)
        self.assertEqual(e.digest(self.repo.get(self.pid)),e.digest(self.project))

    def test_ambiguous_submission_stops_without_duplicate(self):
        bundle=self.save();request=x.prepare_request(self.repo,self.pid,{"bundle":bundle["id"]})
        row=bundle["panels"][0]
        batch={"id":"uncertain","panel_id":row["panel_id"],"status":"uncertain","candidate_indices":[0],
               "render_options":{"import_run":request["run_key"]},"images":[]}
        e.save(self.repo.folder(self.pid)/"images/index.json",{"batches":[batch],"selections":{}})
        app=SimpleNamespace(repo=self.repo,model=Mock(),comfy=Mock())
        with patch("comic_workflow.comfy_recovery.recover_batch",return_value=batch), patch.object(comfyui,"run_batch") as render:
            with self.assertRaises(ValueError):x.draw_imported(app,self.pid,request,lambda _:None,"resume")
            render.assert_not_called()

    def test_fixed_reference_reaches_each_serial_candidate_without_changing_prompt(self):
        from comic_workflow import anima_consistency as ac
        self.doc['panels']=self.doc['panels'][:2]
        bundle=self.save()
        ids=[r['panel_id'] for r in bundle['panels']]
        fixed={'reference':'consistency/frozen.png','reference_sha256':'hash'}
        args={'bundle':bundle['id'],'checkpoint':ac.CHECKPOINT,
              'references_by_panel':dict.fromkeys(ids,'reference-id')}
        with patch.object(ac,'freeze_reference',return_value=fixed) as freeze:
            request=x.prepare_request(self.repo,self.pid,args)
            freeze.assert_called_once()
        self.assertEqual(request['references_by_panel'],dict.fromkeys(ids,fixed))
        app=SimpleNamespace(repo=self.repo,model=Mock(),comfy=Mock())
        with patch.object(comfyui,'run_batch',return_value={'id':'test'}) as render:
            x.draw_imported(app,self.pid,request,lambda _:None,'reference-test')
        self.assertEqual(render.call_count,4)
        for call in render.call_args_list:
            self.assertEqual(call.args[5],1)
            self.assertEqual(call.kwargs['render_options']['consistency_reference'],fixed)
            self.assertTrue(call.kwargs['render_options']['consistency_lora'])
            self.assertEqual(call.kwargs['prompt_override']['positive'],self.doc['panels'][0]['positive'])
        with self.assertRaisesRegex(ValueError,'仅支持'):
            x.prepare_request(self.repo,self.pid,{**args,'checkpoint':'other'})
        with self.assertRaisesRegex(ValueError,'本次绘制'):
            x.prepare_request(self.repo,self.pid,{**args,'references_by_panel':{'P999-K99':'ref'}})

    def test_import_freezes_per_panel_loras_for_every_candidate_and_resume(self):
        self.doc["panels"] = self.doc["panels"][:2]
        bundle = self.save()
        cast = self.project["canon"]["characters"]
        items = [{"file":"sdxl/style.safetensors","name":"Style","character_id":"","selected":True,
                  "weight":0.6,"confirmed":True}]
        for character in cast:
            items.append({"file":"sdxl/"+character["id"]+".safetensors","name":character["name"],
                          "character_id":"project:"+character["id"],"selected":False,"weight":0.7,"confirmed":True})
        prefs = {"mode":"characters","checkpoint":"test.safetensors","items":items}
        drawing_settings.save(self.repo,self.pid,prefs)
        with patch.object(comfyui,"lora_labels",return_value={}):
            request = x.prepare_request(self.repo,self.pid,{"bundle":bundle["id"],"candidate_count":2})
        for _, panel in x.panels(self.project):
            if panel["id"] not in request["targets"]:continue
            expected = {"sdxl/style.safetensors"} | {"sdxl/"+c["character_id"]+".safetensors" for c in panel["characters"]}
            self.assertEqual({l["file"] for l in request["loras_by_panel"][panel["id"]]},expected)
        drawing_settings.save(self.repo,self.pid,{**prefs,"mode":"none"})
        app = SimpleNamespace(repo=self.repo,model=Mock(),comfy=Mock())
        calls = []
        def render(repo,pid,target,update,model,count,mode,loras,indices,**kwargs):
            self.assertEqual(loras,request["loras_by_panel"][target])
            self.assertEqual(model,"test.safetensors")
            calls.append(target)
            image_file = f"frozen/{len(calls)}.png"
            path = repo.folder(pid)/"images"/image_file
            path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b"test")
            batch = {"id":str(len(calls)),"status":"done","panel_id":target,"candidate_indices":indices,
                     "render_options":kwargs["render_options"],"images":[{"file":image_file}]}
            index = comfyui.drawing_state(repo,pid);index["batches"].append(batch)
            e.save(repo.folder(pid)/"images/index.json",index)
            loras.clear()  # Rendering must not mutate the saved job snapshot.
            return batch
        with patch.object(comfyui,"run_batch",side_effect=render):
            x.draw_imported(app,self.pid,request,lambda _:None,"first")
            x.draw_imported(app,self.pid,request,lambda _:None,"resume")
        self.assertEqual(len(calls),4)

    def test_selected_character_reference_is_frozen_only_for_visible_panels(self):
        from comic_workflow import anima_consistency as ac
        bundle = self.save()
        ids = [r['panel_id'] for r in bundle['panels']]
        prefs = {'mode': 'none', 'checkpoint': ac.CHECKPOINT, 'items': [],
                 'consistency_lora': True, 'reference_character_id': 'C1'}
        drawing_settings.save(self.repo, self.pid, prefs)
        chosen = {'character_id': 'C1', 'source_image_id': 'original'}
        frozen = {'character_id': 'C1', 'reference': 'consistency/original.png',
                  'reference_sha256': 'frozen', 'reference_mode': 'selected_character_original'}
        def select(project, panel, model, settings, **kwargs):
            return chosen if panel['id'] == ids[0] else None
        with patch.object(ac, 'selected_character_reference', side_effect=select), \
             patch.object(ac, 'freeze_character_reference', return_value=frozen) as freeze:
            request = x.prepare_request(self.repo, self.pid, {'bundle': bundle['id']})
        freeze.assert_called_once_with(chosen)
        self.assertEqual(request['references_by_panel'], {ids[0]: frozen})
        drawing_settings.save(self.repo, self.pid, {**prefs, 'reference_character_id': ''})
        app = SimpleNamespace(repo=self.repo, model=Mock(), comfy=Mock())
        with patch.object(comfyui, 'run_batch', return_value={'id': 'sample'}) as render:
            x.draw_imported(app, self.pid, request, lambda _: None, 'frozen-character')
        for call in render.call_args_list:
            options = call.kwargs['render_options']
            if call.args[2] == ids[0]:
                self.assertEqual(options['consistency_reference'], frozen)
            else:
                self.assertNotIn('consistency_reference', options)
