import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from PIL import Image

from comic_workflow import engine as e, comfyui, quick_sample
from comic_workflow.repository import Repository
from comic_workflow.characters import CharacterLibrary, DETAILS
from comic_workflow.character_binding import panel_characters


class QuickSampleTests(unittest.TestCase):
    def setUp(self):
        cfg = {**comfyui.config(), "hand_detail": False}
        config_patch = patch.object(comfyui, "config", return_value=cfg)
        config_patch.start(); self.addCleanup(config_patch.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Repository(Path(self.tmp.name) / "projects")
        self.model = "animagine-xl-4.0-opt.safetensors"
        self.req = {"text": " 1girl, /not-a-character, (red hair:1.2) ", "negative": "",
                    "checkpoint": self.model, "count": 3, "aspect_ratio": "16:9"}
        self.discovery = patch.object(comfyui, "discover", return_value={"ready": True, "checkpoints": [self.model]})
        self.discovery.start(); self.addCleanup(self.discovery.stop)

    def create(self):
        return quick_sample.create(self.repo, self.req)["project"]

    def test_matte_setting_survives_saved_project_and_reaches_renderer(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                request = {**self.req, "delta_matte_skin": enabled}
                pid = quick_sample.create(self.repo, request, persistent=True)["project"]
                project = self.repo.get(pid)
                self.assertIs(project["prompt_input"]["delta_matte_skin"], enabled)
                app = SimpleNamespace(repo=self.repo, model=Mock(), comfy=Mock())
                with patch.object(comfyui, "run_batch", return_value={"id": "offline"}) as draw:
                    quick_sample.run(app, pid, {"base_hash": e.digest(project)}, Mock(), "matte")
                self.assertTrue(draw.call_args_list)
                for call in draw.call_args_list:
                    self.assertIs(call.kwargs["render_options"]["delta_matte_skin"], enabled)
        for invalid in (1, "false", None):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                quick_sample.create(self.repo, {**self.req, "delta_matte_skin": invalid})

    def test_custom_ratio_resolution_survive_reload_and_reach_generation(self):
        self.req.update(aspect_ratio="7:5", resolution="1536")
        pid = quick_sample.create(self.repo, self.req, persistent=True)["project"]
        restored = Repository(self.repo.root)
        self.addCleanup(restored._temporary.cleanup)
        project = restored.get(pid)
        self.assertEqual(project["pages"]["P001"]["panels"][0]["aspect_ratio"], "7:5")
        self.assertEqual(project["prompt_input"]["resolution"], "1536")
        app = SimpleNamespace(repo=restored, model=Mock(), comfy=Mock())
        with patch.object(comfyui, "run_batch", return_value={"id": "offline"}) as draw:
            quick_sample.run(app, pid, {"base_hash": e.digest(project)}, Mock(), "sizing")
        for call in draw.call_args_list:
            self.assertEqual(call.kwargs["render_options"]["aspect_ratio"], "7:5")
            self.assertEqual(call.kwargs["render_options"]["resolution"], "1536")

    def test_queued_prompt_is_durable_and_retry_reuses_successful_images(self):
        from comic_workflow.app_core import StudioApp as Application
        from comic_workflow.night_queue import NightQueue
        app = Application.__new__(Application)
        app.repo = self.repo
        app.night_queue = NightQueue(app)
        self.addCleanup(app.night_queue.close)
        app.model = Mock(); app.comfy = Mock()
        result = app.action({**self.req, "action": "queue_prompt"})
        pid = result["project"]
        self.assertFalse(self.repo.get(pid)["transient"])
        self.assertEqual(self.repo.listing()[0]["id"], pid)
        request = app.night_queue.state["items"][0]["request"]
        self.assertEqual(request["candidate_count"], 3)
        restored = Repository(self.repo.root)
        self.addCleanup(restored._temporary.cleanup)
        self.assertEqual(restored.get(pid)["prompt_input"]["text"], self.req["text"])
        image = self.repo.folder(pid) / "images/first/one.png"
        image.parent.mkdir(parents=True); image.write_bytes(b"saved")
        previous = {"id": "first", "candidate_indices": [0], "status": "done",
                    "render_options": {"queue_run": request["run_key"]},
                    "images": [{"file": "first/one.png"}]}
        with patch.object(comfyui, "drawing_state", return_value={"batches": [previous]}), \
             patch.object(comfyui, "run_batch", return_value={"id": "new"}) as draw:
            report = quick_sample.run(app, pid, request, Mock(), "retry")
        self.assertEqual(draw.call_count, 2)
        self.assertEqual([c.kwargs["candidate_indices"] for c in draw.call_args_list], [[1], [2]])
        self.assertEqual(len(report["batches"]), 3)


    def test_queued_resume_recovers_uncertain_result_before_drawing_again(self):
        pid=quick_sample.create(self.repo,{**self.req,"count":1},persistent=True)["project"]
        request={"base_hash":e.digest(self.repo.get(pid)),"run_key":"queued-run"}
        image=self.repo.folder(pid)/"images/first/one.png"
        image.parent.mkdir(parents=True);image.write_bytes(b"saved")
        batch={"id":"first","status":"uncertain","candidate_indices":[0],
               "render_options":{"queue_run":"queued-run"},"images":[{"file":"first/one.png"}]}
        index=self.repo.folder(pid)/"images/index.json"
        e.save(index,{"batches":[batch],"selections":{}})
        app=SimpleNamespace(repo=self.repo,model=Mock(),comfy=Mock())
        def recover(*_):
            app.comfy.start.assert_called_once()
            recovered={**batch,"status":"done"}
            e.save(index,{"batches":[recovered],"selections":{}})
            return recovered
        with patch("comic_workflow.comfy_recovery.recover_batch",side_effect=recover) as recovery, \
             patch.object(comfyui,"run_batch") as draw:
            result=quick_sample.run(app,pid,request,Mock(),"continued")
        recovery.assert_called_once();draw.assert_not_called()
        self.assertEqual(result["batches"],["first"])

    def save_character(self, name="测试角色", image=False):
        library = CharacterLibrary(self.repo.root.parent / "characters")
        profile = {key: "角色外观与性格的详细说明，保留用户提供的设定。" for key in DETAILS}
        profile.update(observed=[], suggested=[], unknowns=[])
        source = {"mode": "text"}
        image_id = None
        if image:
            image_id = "reference-image"
            image_path = library.image_path(image_id)
            image_path.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (16, 16), "blue").save(image_path)
            source = {"mode": "image", "personality": "温柔"}
        return library.save(name, profile, source, image_id)

    def reference_config(self):
        root = Path(self.tmp.name)
        model = root / "models/reference.bin"
        model.parent.mkdir(parents=True, exist_ok=True)
        model.write_bytes(b"test")
        e.save(root / "config/reference-models.json", {
            "enabled": True, "weight": 0.7, "end_at": 0.8,
            "models": [{"directory": "models", "name": "reference.bin", "size": 4}],
        })
        return root

    def test_selected_characters_are_frozen_and_prompts_preserve_user_edits(self):
        first = self.save_character("甲")
        second = self.save_character("乙")
        self.req["characters"] = [
            {"name": "甲", "revision": first["revision"], "prompt": "red hair, white coat"},
            {"name": "乙", "revision": second["revision"], "prompt": "blue hair, black coat"},
        ]
        pid = self.create()
        p = self.repo.get(pid)
        e.validate_project(p, complete=True)
        self.assertEqual(p["prompt_input"]["characters"], self.req["characters"])
        self.assertEqual(p["prompt_input"]["text"], self.req["text"])
        self.assertEqual(p["drawing_prompts"]["P001-K01"]["payload"]["positive"],
                         self.req["text"] + "\n\nred hair, white coat\n\nblue hair, black coat")
        panel = p["pages"]["P001"]["panels"][0]
        self.assertEqual([c["saved_character"]["name"] for c in panel_characters(p, panel)], ["甲", "乙"])
        library = CharacterLibrary(self.repo.root.parent / "characters")
        changed = {**first["profile"], "hair": "现在改成绿色头发"}
        library.save("甲", changed, first["source"], base_revision=1)
        self.assertEqual(self.repo.get(pid)["brief"]["character_refs"][0], first)



    def test_chat_card_is_rejected_before_project_creation_without_rewriting(self):
        card = self.save_character()
        for prompt in [
            "Character Name: Amiya\nAppearance: brown hair\nPersonality: kind\nSpeech Style: Address the user as Doctor",
            "brown hair\n**Interaction Guidelines:** Do not decide the Doctor's actions",
            "棕发\n性格与行为习惯：温柔\n说话方式：称呼用户博士",
        ]:
            request = {**self.req, "characters": [{"name": card["name"], "revision": 1, "prompt": prompt}]}
            with self.subTest(prompt=prompt), self.assertRaisesRegex(ValueError, "聊天设定"):
                quick_sample.create(self.repo, request)
            self.assertEqual(request["characters"][0]["prompt"], prompt)
        self.assertEqual(self.repo.listing(), [])
        self.assertFalse(quick_sample.character_prompt_problem("brown hair, blue eyes, background blur"))












    def test_input_is_unchanged_with_no_character_interpretation(self):
        old = self.repo.create(None, True)
        before = e.digest(self.repo.get(old))
        pid = self.create(); p = self.repo.get(pid)
        e.validate_project(p, complete=True)
        self.assertEqual(p["drawing_prompts"]["P001-K01"]["payload"]["positive"], self.req["text"])
        self.assertEqual(p["brief"]["character_refs"], [])
        self.assertEqual(p["pages"]["P001"]["panels"][0]["aspect_ratio"], "16:9")
        self.assertEqual(e.read(self.repo.folder(pid) / "workspace.json")["stage"], "prompt")
        self.assertEqual(before, e.digest(self.repo.get(old)))

    def test_invalid_inputs_do_not_create_projects(self):
        for override in [{"text": " "}, {"count": 17}, {"count": True},
                         {"checkpoint": "missing"}, {"aspect_ratio": "bad"}]:
            with self.subTest(override=override), self.assertRaises(ValueError):
                quick_sample.create(self.repo, {**self.req, **override})
        self.assertEqual(self.repo.listing(), [])

    def test_runs_requested_count_without_qwen_and_continues_definite_failure(self):
        pid = self.create()
        app = SimpleNamespace(repo=self.repo, model=Mock(), comfy=Mock(), prepare_language=Mock())
        events = []
        with patch.object(comfyui, "run_batch", side_effect=[{"id":"one"}, ValueError("bad image"), {"id":"three"}]) as run:
            result = quick_sample.run(app, pid, {"base_hash":e.digest(self.repo.get(pid))}, events.append, "test")
        self.assertEqual(run.call_count, 3)
        self.assertEqual(result["status"], "done_with_errors")
        self.assertEqual(result["batches"], ["one", "three"])
        self.assertEqual(events[-1]["progress"]["completed"], 3)
        self.assertEqual(events[-1]["eta"]["remaining_seconds"], 0)
        self.assertTrue(all(c.kwargs["render_options"]["direct_prompt"] for c in run.call_args_list))
        app.prepare_language.assert_not_called(); app.model.start.assert_not_called()
        app.comfy.start.assert_called_once(); app.comfy.stop.assert_called_once()

    def test_raw_workflow_sends_exact_positive_and_negative_without_adapter(self):
        pid = self.create(); graphs=[]
        png = io.BytesIO(); Image.new("RGB", (16,16), "green").save(png, format="PNG")
        def api(path, body=None):
            if path == "/prompt":
                graphs.append(body["prompt"]); return {"prompt_id":"test", "node_errors":{}}
            return {"test":{"status":{"status_str":"success"},"outputs":{"7":{"images":[{"filename":"test.png"}]}}}}
        opener = Mock()
        opener.open.return_value.__enter__ = Mock(return_value=SimpleNamespace(read=lambda n:png.getvalue()))
        opener.open.return_value.__exit__ = Mock(return_value=False)
        with patch.object(comfyui, "api", side_effect=api), \
             patch("comic_workflow.telemetry.websocket.create_connection", side_effect=ConnectionError("offline")), \
             patch("urllib.request.build_opener", return_value=opener), \
             patch("comic_workflow.framing.apply_camera", side_effect=AssertionError("must not add words")), \
             patch("comic_workflow.prompt_adapter.compile_prompt", side_effect=AssertionError("must not adapt")):
            batch = comfyui.run_batch(self.repo, pid, "P001-K01", Mock(), self.model, 1,
                render_options={"diagnostic":"small_batch", "direct_prompt":True, "reference":False, "control":False})
        texts = [node["inputs"]["text"] for node in graphs[0].values() if node.get("class_type") == "CLIPTextEncode"]
        self.assertEqual(texts, [self.req["text"], ""])
        self.assertEqual(batch["images"][0]["positive"], self.req["text"])
        self.assertEqual(batch["images"][0]["negative"], "")
        self.assertEqual(batch["checkpoint"], self.model)

    def test_raw_lora_workflow_chains_weights_without_inserting_trigger_words(self):
        pid = self.create(); graphs=[]
        cfg = comfyui.config()
        root = Path(self.tmp.name)
        for name in ["style.safetensors", "face.safetensors"]:
            file=root/"models/loras"/name;file.parent.mkdir(parents=True,exist_ok=True);file.write_bytes(b"mock")
        loras=[{"file":name,"weight":weight,"clip_weight":weight,"trigger_words":"must not be inserted",
                "experimental_checkpoint":self.model,"confirmed_compatibility":False}
               for name,weight in [("style.safetensors",0.65),("face.safetensors",0.9)]]
        png = io.BytesIO(); Image.new("RGB", (16,16), "green").save(png, format="PNG")
        def api(path, body=None):
            if path == "/prompt":
                graphs.append(body["prompt"]); return {"prompt_id":"test", "node_errors":{}}
            return {"test":{"status":{"status_str":"success"},"outputs":{"7":{"images":[{"filename":"test.png"}]}}}}
        opener = Mock()
        opener.open.return_value.__enter__ = Mock(return_value=SimpleNamespace(read=lambda n:png.getvalue()))
        opener.open.return_value.__exit__ = Mock(return_value=False)
        with patch.object(comfyui,"ROOT",root), \
             patch.object(comfyui,"config",return_value=cfg), \
             patch.object(comfyui,"discover",return_value={"ready":True,"checkpoints":[self.model],"loras":[l["file"] for l in loras]}), \
             patch("comic_workflow.lora_inspection.inspect_lora",return_value={"structurally_compatible":True,"family":"SDXL"}), \
             patch.object(comfyui, "api", side_effect=api), \
             patch("comic_workflow.telemetry.websocket.create_connection", side_effect=ConnectionError("offline")), \
             patch("urllib.request.build_opener", return_value=opener), \
             patch("comic_workflow.framing.apply_camera", side_effect=AssertionError("must not add words")), \
             patch("comic_workflow.prompt_adapter.compile_prompt", side_effect=AssertionError("must not adapt")):
            batch = comfyui.run_batch(self.repo, pid, "P001-K01", Mock(), self.model, 1, loras=loras,
                render_options={"diagnostic":"small_batch", "direct_prompt":True, "reference":False, "control":False})
        texts = [node["inputs"]["text"] for node in graphs[0].values() if node.get("class_type") == "CLIPTextEncode"]
        self.assertEqual(texts, [self.req["text"], ""])
        self.assertEqual(batch["images"][0]["positive"], self.req["text"])
        self.assertEqual(batch["images"][0]["negative"], "")
        self.assertEqual(batch["checkpoint"], self.model)

        self.assertEqual(graphs[0]["8"]["inputs"]["strength_model"],0.65)
        self.assertEqual(graphs[0]["9"]["inputs"]["strength_clip"],0.9)
        self.assertEqual(graphs[0]["9"]["inputs"]["model"],["8",0])
        self.assertEqual(graphs[0]["5"]["inputs"]["model"],["9",0])
        self.assertEqual(graphs[0]["2"]["inputs"]["clip"],["9",1])
        self.assertEqual(batch["loras"],loras)

    def test_uncertain_submission_stops_without_duplicate(self):
        pid = self.create(); app=SimpleNamespace(repo=self.repo, model=Mock(), comfy=Mock())
        with patch.object(comfyui,"drawing_state",side_effect=[{"batches":[]},{"batches":[{"status":"uncertain"}]}]), \
             patch.object(comfyui,"run_batch",side_effect=ValueError("timeout")) as run:
            with self.assertRaisesRegex(ValueError,"timeout"):
                quick_sample.run(app,pid,{"base_hash":e.digest(self.repo.get(pid))},Mock(),"uncertain")
        run.assert_called_once(); app.comfy.stop.assert_called_once()

    def test_lora_selection_persists_and_is_forwarded_to_every_image(self):
        self.req["loras"] = [{"file":"style.safetensors", "weight":0.65}, {"file":"character.safetensors", "weight":0.9}]
        info = {item["file"]:{"structurally_compatible":True} for item in self.req["loras"]}
        with patch.object(comfyui,"discover",return_value={"checkpoints":[self.model],"loras":list(info)}), \
             patch("comic_workflow.lora_inspection.inspect_local",return_value=info), \
             patch.object(comfyui,"lora_labels",return_value={"style.safetensors":{"name":"用户画风"}}):
            pid=self.create(); p=self.repo.get(pid)
            saved=p["prompt_input"]["loras"]
            self.assertEqual([l["weight"] for l in saved], [0.65,0.9])
            self.assertEqual(saved[0]["label"], "用户画风")
            self.assertFalse(saved[0]["confirmed_compatibility"])
            app=SimpleNamespace(repo=self.repo,model=Mock(),comfy=Mock(),prepare_language=Mock())
            with patch.object(comfyui,"run_batch",return_value={"id":"test"}) as render:
                quick_sample.run(app,pid,{"base_hash":e.digest(p)},Mock(),"loras")
            self.assertEqual(render.call_count,3)
            for call in render.call_args_list:
                self.assertEqual(call.args[7],saved)
            app.prepare_language.assert_not_called(); app.model.start.assert_not_called()

    def test_checked_character_bound_lora_survives_without_selected_character(self):
        lora_file = "sdxl/character_bound.safetensors"
        self.req["loras"] = [{"file": lora_file, "weight": 0.8}]
        self.req["characters"] = []
        info = {lora_file: {"structurally_compatible": True}}
        labels = {lora_file: {"name": "绑定角色", "character_id": "saved-char-id", "character": "可露希尔"}}
        with patch.object(comfyui, "discover", return_value={"checkpoints": [self.model], "loras": [lora_file]}), \
             patch("comic_workflow.lora_inspection.inspect_local", return_value=info), \
             patch.object(comfyui, "lora_labels", return_value=labels):
            pid = self.create()
            saved = self.repo.get(pid)["prompt_input"]["loras"]
        self.assertEqual([l["file"] for l in saved], [lora_file])
        self.assertEqual(saved[0]["weight"], 0.8)

    def test_invalid_loras_rejected_before_project_or_gpu_creation(self):
        discovery={"checkpoints":[self.model],"loras":["good","bad","unknown"]}
        info={"good":{"structurally_compatible":True},"bad":{"structurally_compatible":False},"unknown":{"structurally_compatible":None}}
        with patch.object(comfyui,"discover",return_value=discovery), \
             patch("comic_workflow.lora_inspection.inspect_local",return_value=info):
            for items in [[{"file":"missing"}], [{"file":"bad"}], [{"file":"unknown"}],
                          [{"file":"good","weight":-1}], [{"file":"good","weight":float("nan")}],
                          [{"file":"good","weight":True}], [{"file":"good"}]*2, [{"file":"good"}]*5]:
                with self.subTest(items=items),self.assertRaises(ValueError):
                    quick_sample.create(self.repo,{**self.req,"loras":items})
        self.assertEqual(self.repo.listing(),[])

    def test_optional_seed_defaults_to_random_and_increments_when_set(self):
        app = SimpleNamespace(repo=self.repo, model=Mock(), comfy=Mock(), prepare_language=Mock())
        with patch.object(comfyui, "discover", return_value={"checkpoints": [self.model], "loras": []}):
            # 1. Omitted seed -> stays absent so comfyui picks random per image.
            pid_rand = self.create()
            p_rand = self.repo.get(pid_rand)
            self.assertNotIn("seed", p_rand["prompt_input"])
            with patch.object(comfyui, "run_batch", return_value={"id": "r"}) as render:
                quick_sample.run(app, pid_rand, {"base_hash": e.digest(p_rand)}, Mock(), "rand")
            self.assertTrue(all("seed" not in call.kwargs["render_options"] for call in render.call_args_list))

            # 2. Explicit seed -> persists and increments across candidates.
            pid_fixed = quick_sample.create(self.repo, {**self.req, "seed": 42, "count": 3})["project"]
            p_fixed = self.repo.get(pid_fixed)
            self.assertEqual(p_fixed["prompt_input"]["seed"], 42)
            with patch.object(comfyui, "run_batch", return_value={"id": "f"}) as render:
                quick_sample.run(app, pid_fixed, {"base_hash": e.digest(p_fixed)}, Mock(), "fixed")
            self.assertEqual([call.kwargs["render_options"]["seed"] for call in render.call_args_list], [42, 43, 44])

            # 3. Invalid seeds rejected.
            for bad in (-1, "42", 1.5, True, 2**53):
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    quick_sample.create(self.repo, {**self.req, "seed": bad})
