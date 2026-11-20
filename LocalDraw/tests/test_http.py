import json
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from pathlib import Path
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from comic_workflow.server import Application, make_handler
from comic_workflow.repository import Repository
from comic_workflow.model import request_json


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        with patch("comic_workflow.jobs.DATA", root):
            self.app = Application(Repository(root / "projects"))
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.app))
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:" + str(self.http.server_port)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def tearDown(self):
        self.app.night_queue.close()
        self.http.shutdown()
        self.http.server_close()
        self.tmp.cleanup()

    def request(self, body=None, headers=None, path="/api/action"):
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(body).encode() if body else None,
            headers=headers or {},
        )
        with self.opener.open(req) as r:
            return json.load(r)

    def test_bootstrap_and_create(self):
        bootstrap = self.request(path="/api/bootstrap")
        result = self.request(
            {"action": "create", "sample": True}, {"X-Studio-Token": bootstrap["token"]}
        )
        self.assertTrue(result["project"].startswith("story-"))

    def test_night_queue_api_preserves_prompt_and_controls_waiting_list(self):
        headers = {"X-Studio-Token": self.request(path="/api/bootstrap")["token"]}
        model = "test-model.safetensors"
        with patch("comic_workflow.comfyui.discover", return_value={"checkpoints": [model]}):
            queued = self.request({"action": "queue_prompt", "text": "quiet forest", "checkpoint": model,"task_name":"森林 · 第一批"}, headers)
        state = self.request(path="/api/queue")
        self.assertFalse(state["running"])
        self.assertEqual(state["items"][0]["project"], queued["project"])
        self.assertEqual(state["items"][0]["status"], "waiting")
        self.assertEqual(state["items"][0]["label"],"森林 · 第一批")
        self.request({"action":"rename_queue_item","item":queued["queued"],"task_name":"森林 · 暖色版"},headers)
        preview=self.request(path="/api/queue-preview?item="+queued["queued"])
        self.assertEqual(preview["checkpoint"],model)
        self.assertEqual(preview["label"],"森林 · 暖色版")
        self.assertEqual(preview["panels"][0]["positive"],"quiet forest")
        self.assertTrue(self.request(path="/api/status")["features"]["night_queue"])
        self.request({"action": "remove_queue_item", "item": queued["queued"]}, headers)
        self.assertEqual(self.request(path="/api/queue")["items"], [])
        self.assertEqual(self.app.repo.get(queued["project"])["prompt_input"]["text"], "quiet forest")

    def test_paused_drawing_quantity_endpoint_keeps_job_paused(self):
        bootstrap = self.request(path='/api/bootstrap')
        pid = self.app.repo.create(None, True)
        self.app.jobs.save('quantity', {'id':'quantity', 'project':pid, 'label':'draw',
            'created':'1', 'status':'paused', 'request':{'action':'draw_imported',
                'run_key':'run', 'targets':['P001-K01'], 'candidate_count':16}})
        headers = {'X-Studio-Token':bootstrap['token']}
        result = self.request({'action':'set_remaining_candidate_count', 'project':pid,
            'job':'quantity', 'candidate_count':3}, headers)
        self.assertEqual(result['status'],'paused')
        self.assertEqual(result['quantity_change']['new_images'],3)
        self.assertEqual(self.app.jobs.get('quantity')['request']['candidate_slots_by_panel'],
                         {'P001-K01':[0,1,2]})
        with self.assertRaises(urllib.error.HTTPError):
            self.request({'action':'set_remaining_candidate_count', 'project':'wrong',
                'job':'quantity', 'candidate_count':4}, headers)
        self.assertEqual(self.app.jobs.get('quantity')['request']['candidate_count'],3)
        self.assertTrue(self.request(path='/api/status')['features']['remaining_candidate_count'])

    def test_finished_view_is_served_with_drawing_script(self):
        with self.opener.open(self.base + "/drawing.js") as response:
            self.assertEqual(response.status, 200)
            self.assertIn("renderFinishedComic", response.read().decode("utf-8"))
        with self.opener.open(self.base + "/") as response:
            self.assertNotIn('src="/drawing.js"', response.read().decode("utf-8"))
        with self.opener.open(self.base + "/app.js") as response:
            self.assertIn('script.src = "/drawing.js"', response.read().decode("utf-8"))

    def test_lightweight_view_endpoints_avoid_model_discovery(self):
        pid = self.app.repo.create(None, True)
        with patch('comic_workflow.server.comfyui.discover', side_effect=AssertionError('unneeded model scan')):
            self.assertEqual(self.request(path='/api/projects'), self.app.repo.listing())
            self.assertEqual(self.request(path='/api/images?id='+pid), {'images':{'batches':[], 'selections':{}}})
            self.assertIsNone(self.request(path='/api/drawing-settings?id='+pid))
        with patch('comic_workflow.server.comfyui.discover', return_value={'ready':None}) as discover:
            self.request(path='/api/drawing?id='+pid)
            discover.assert_called_once_with(probe=False)

    def test_bubble_planning_requires_explicit_model_use(self):
        pid = self.app.repo.create(None, True)
        with patch.object(self.app, "prepare_language") as prepare, patch.object(self.app.jobs, "submit", return_value="bubble-test-job") as submit:
            with self.assertRaises(ValueError):
                self.app.action({"action": "plan_bubbles", "project": pid})
            submit.assert_not_called()
            result = self.app.action({"action": "plan_bubbles", "project": pid, "use_model": True})
            self.assertEqual(result["job"], "bubble-test-job")
            prepare.assert_not_called()
        with patch("comic_workflow.bubble_planner.load", return_value={"pages": {}, "stale_pages": []}):
            self.assertEqual(self.request(path="/api/bubble-plan?id=" + pid)["pages"], {})

    def test_prompt_exchange_export_preview_and_import(self):
        pid = self.app.repo.create(None, True)
        headers = {"X-Studio-Token": self.app.token}
        doc = self.request(path="/api/prompt-exchange?id=" + pid)
        self.assertTrue(doc["panels"][0]["storyboard"]["action"])
        doc["panels"] = doc["panels"][:1]
        doc["panels"][0].update(positive="A person reads a book.", negative="blur")
        preview = self.request({"action":"preview_prompt_import", "project":pid, "document":doc}, headers)
        self.assertTrue(preview["valid"])
        with self.assertRaises(urllib.error.HTTPError):
            self.request({"action":"save_prompt_import", "project":pid, "document":doc, "preview_hash":preview["preview_hash"]}, headers)
        saved = self.request({"action":"save_prompt_import", "project":pid, "document":doc, "preview_hash":preview["preview_hash"], "confirm":True}, headers)
        self.assertEqual(self.request(path="/api/prompt-import?id="+pid)["bundle"]["id"], saved["id"])

    def test_local_prompt_routes_queue_without_inference(self):
        pid = self.app.repo.create(None, True)
        self.assertIsNone(self.request(path="/api/local-prompts?id=" + pid)["draft"])
        with patch.object(self.app.jobs, "submit", return_value="prompt-job") as submit, \
             patch.object(self.app.model, "start") as start:
            response = self.request({"action": "fill_local_prompts", "project": pid},
                                    {"X-Studio-Token": self.app.token})
            self.assertEqual(response, {"job": "prompt-job"})
            self.assertEqual(submit.call_args.kwargs["request"]["action"], "fill_local_prompts")
            start.assert_not_called()

    def test_rejects_cross_origin_write(self):
        with self.assertRaises(urllib.error.HTTPError):
            self.request(
                {"action": "create", "sample": True},
                {"X-Studio-Token": self.app.token, "Origin": "https://example.com"},
            )

    def test_complete_story_json_import_roundtrip_without_model_calls(self):
        from comic_workflow import engine as e
        from comic_workflow.paths import ROOT
        source = e.read(ROOT / "config/example.json")
        doc = {"brief": source["brief"], "canon": source["canon"], "pages": list(source["pages"].values())}
        headers = {"X-Studio-Token": self.app.token}
        with patch.object(self.app, "prepare_language") as model, patch.object(self.app.jobs, "submit") as jobs:
            preview = self.request({"action": "preview_story_import", "document": "\ufeff" + json.dumps(doc)}, headers)
            self.assertEqual(self.app.repo.listing(), [])
            req = {"action": "save_story_import", "document": doc, "preview_hash": preview["preview_hash"],
                   "import_key": "e" * 32, "confirm": True}
            saved = self.request(req, headers)
            again = self.request(req, headers)
            self.assertEqual(saved["project"], again["project"])
            snapshot = self.request(path="/api/project?id=" + saved["project"])
            self.assertEqual(list(snapshot["project"]["pages"].values()), doc["pages"])
            model.assert_not_called()
            jobs.assert_not_called()
        with self.opener.open(self.base + "/story-import.js") as response:
            self.assertIn("openStoryImport", response.read().decode("utf-8"))

    def test_invalid_story_import_returns_error_without_creating_project(self):
        with self.assertRaises(urllib.error.HTTPError) as failed:
            self.request({"action": "preview_story_import", "document": {"pages": []}},
                         {"X-Studio-Token": self.app.token})
        self.assertEqual(failed.exception.code, 400)
        self.assertIn("brief", json.load(failed.exception)["error"])
        self.assertEqual(self.app.repo.listing(), [])

    def test_cancel_endpoint_requires_confirmation_and_preserves_progress(self):
        from comic_workflow import engine as e
        e.save(self.app.jobs.root / "paused.json", {
            "id": "paused", "project": "test", "label": "draw", "created": "1", "status": "paused",
            "request": {"candidate_count": 16}, "progress": {"completed": 258, "total": 480},
        })
        headers = {"X-Studio-Token": self.app.token}
        with self.assertRaises(urllib.error.HTTPError):
            self.request({"action": "cancel_job", "job": "paused"}, headers)
        self.assertEqual(self.app.jobs.get("paused")["status"], "paused")
        result = self.request({"action": "cancel_job", "job": "paused", "confirm": True}, headers)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(self.app.jobs.get("paused")["progress"]["completed"], 258)

    def test_drawing_options_advertise_character_selection(self):
        with (
            patch("comic_workflow.server.comfyui.discover", return_value={"ready": False, "checkpoints": [], "loras": []}),
            patch("comic_workflow.server.inspect_local", return_value={}),
        ):
            result = self.request(path="/api/drawing-options")
        self.assertIs(result["prompt_characters"], True)

    def test_trash_api_roundtrip(self):
        from comic_workflow import engine as e
        pid = self.app.repo.create(None, True)
        headers = {"X-Studio-Token": self.app.token}
        self.request({"action": "delete_project", "project": pid, "confirm": True,
                      "base_hash": e.digest(self.app.repo.get(pid))}, headers)
        self.assertEqual(self.request(path="/api/trash")[0]["id"], pid)
        self.assertEqual(self.request(path="/api/bootstrap")["projects"], [])
        self.request({"action": "restore_deleted_project", "project": pid}, headers)
        self.assertEqual(self.request(path="/api/trash"), [])
        self.assertEqual(self.request(path="/api/bootstrap")["projects"][0]["id"], pid)

    def test_new_workspace_is_blank_and_archives_input_without_touching_story(self):
        from comic_workflow import engine as e
        pid = self.app.repo.create(None, True)
        before = e.digest(self.app.repo.get(pid))
        headers = {"X-Studio-Token": self.app.token}
        previous = {"stage": "script", "updated": 100, "views": {
            "script:": {"fields": {"story-text": "上次未写完的故事", "story-preferences": "保留结局"}}}}
        self.request({"action": "save_workspace", "project": "new", "state": previous}, headers)
        fresh = self.request({"action": "new_workspace", "previous_state": previous,
                              "draft_result": {"payload": {"story": "已整理的草稿"}}}, headers)
        self.assertEqual(fresh["views"]["script:"]["fields"]["story-text"], "")
        self.assertEqual(fresh["stage"], "script")
        drafts = self.request(path="/api/input-drafts")
        self.assertEqual(drafts[0]["state"], previous)
        self.assertEqual(drafts[0]["result"]["payload"]["story"], "已整理的草稿")
        self.assertEqual(self.request(path="/api/workspace?id=new"), fresh)
        with self.assertRaises(urllib.error.HTTPError):
            self.request({"action": "save_workspace", "project": "new", "state": previous}, headers)
        self.assertEqual(before, e.digest(self.app.repo.get(pid)))

    def test_new_workspace_archives_the_newest_saved_input(self):
        headers = {"X-Studio-Token": self.app.token}
        newer = {"updated": 200, "views": {"script:": {"fields": {"story-text": "新输入"}}}}
        self.request({"action": "save_workspace", "project": "new", "state": newer}, headers)
        self.request({"action": "new_workspace", "previous_state": {"updated": 100}}, headers)
        archived = self.request(path="/api/input-drafts")[0]["state"]
        self.assertEqual(archived["updated"], newer["updated"])
        self.assertEqual(archived["views"], newer["views"])

    def test_rejects_missing_token(self):
        with self.assertRaises(urllib.error.HTTPError):
            self.request({"action": "create", "sample": True})

    def test_enhancement_base_image_can_be_opened_with_bounded_filename(self):
        pid = self.app.repo.create(None, True)
        folder = self.app.repo.folder(pid) / "images/hand-test"
        folder.mkdir(parents=True)
        (folder / "base-1.png").write_bytes(b"base fixture")
        url = self.base + f"/api/image?id={pid}&bundle=hand-test&file=base-1.png"
        with self.opener.open(url) as response:
            self.assertEqual(response.read(), b"base fixture")
        for name in ["base-17.png", "../base-1.png", "base-1.png/../../project.json"]:
            with self.subTest(name=name), self.assertRaises(urllib.error.HTTPError):
                self.opener.open(self.base + f"/api/image?id={pid}&bundle=hand-test&file="
                                 + urllib.parse.quote(name, safe=""))

    def test_rejects_host_and_path_escape(self):
        with self.assertRaises(urllib.error.HTTPError):
            self.request(path="/api/bootstrap", headers={"Host": "attacker.test"})
        with self.assertRaises(urllib.error.HTTPError):
            self.request(path="/api/project?id=../outside")

    def test_comfy_empty_success_response(self):
        class Response:
            def __enter__(s):
                return s

            def __exit__(s, *args):
                pass

            def read(s):
                return b""

        with patch("urllib.request.OpenerDirector.open", return_value=Response()):
            self.assertEqual(
                request_json("http://127.0.0.1:8188/free", {"unload_models": True}), {}
            )

    def test_project_and_comparison_images_remain_accessible(self):
        from PIL import Image

        root = Path(self.tmp.name)
        project_image = root / "projects/test/images/batch/1.png"
        art_image = root / "test-results/art-style/run/seed-A.png"
        for file in (project_image, art_image):
            file.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (8, 8), "white").save(file)
        with patch("comic_workflow.server.DATA", root):
            for endpoint, file in [
                ("/api/image?id=test&bundle=batch&file=1.png", project_image),
                ("/api/art-image?folder=run&file=seed-A.png", art_image),
            ]:
                with self.opener.open(self.base + endpoint) as response:
                    self.assertEqual(response.headers["Content-Type"], "image/png")
                    self.assertEqual(response.read(), file.read_bytes())
            with self.assertRaises(urllib.error.HTTPError):
                self.opener.open(self.base + "/api/art-image?folder=..&file=seed-A.png")

    def test_workspace_roundtrip_and_stale_write_protection(self):
        pid = self.app.repo.create(None, True)
        state = {
            "stage": "drawing",
            "updated": 200,
            "views": {"drawing:": {"scroll": 1200, "fields": {"draft": "保留编辑"}}},
        }
        headers = {"X-Studio-Token": self.app.token}
        self.request(
            {"action": "save_workspace", "project": pid, "state": state}, headers
        )
        read = self.request(path="/api/workspace?id=" + pid)
        self.assertEqual(read["views"], state["views"])
        self.assertEqual(read["stage"], "drawing")
        with self.assertRaises(urllib.error.HTTPError):
            self.request(
                {
                    "action": "save_workspace",
                    "project": pid,
                    "state": {**state, "updated": 100},
                },
                headers,
            )
        self.assertEqual(self.request(path="/api/workspace?id=" + pid)["updated"], 200)
