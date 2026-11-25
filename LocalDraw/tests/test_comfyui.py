import copy
import io
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
from comic_workflow import engine as e
from comic_workflow import comfyui, layout
from comic_workflow.repository import Repository


class DrawingTests(unittest.TestCase):
    def setUp(self):
        cfg = {**comfyui.config(), "hand_detail": False}
        config_patch = patch.object(comfyui, "config", return_value=cfg)
        config_patch.start(); self.addCleanup(config_patch.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Repository(self.tmp.name)
        self.pid = self.repo.create(None, True)
        p = self.repo.get(self.pid)
        self.target = "P001-K01"
        self.panel = p["pages"]["P001"]["panels"][0]
        prompt = {
            "panel_id": self.target,
            "positive": "anime woman at counter",
            "negative": "text",
            "composition_variants": ["anime woman at counter"] * 3,
            "character_anchors": [
                {"character_id": c["character_id"], "tags": "silver hair, green eyes"}
                for c in self.panel["characters"]
            ],
            "adaptation_notes": "测试",
        }
        candidate = self.repo.propose(
            self.pid, "drawing_prompt", self.target, prompt, e.digest(p)
        )
        self.repo.accept(self.pid, candidate["id"])

    def tearDown(self):
        self.tmp.cleanup()

    def mock_api(self, path, body=None, timeout=10):
        if path == "/prompt":
            self.prompts.append(body["prompt"])
            return {"prompt_id": str(len(self.prompts)), "node_errors": {}}
        if path.startswith("/history/"):
            key = path.split("/")[-1]
            return {
                key: {
                    "status": {"status_str": "success"},
                    "outputs": {
                        "7": {
                            "images": [
                                {
                                    "filename": "image.png",
                                    "subfolder": "",
                                    "type": "output",
                                }
                            ]
                        }
                    },
                }
            }
        raise AssertionError(path)

    def test_external_prompt_reaches_graph_unchanged_without_saved_adaptation(self):
        from comic_workflow import anima
        project = self.repo.get(self.pid)
        project["drawing_prompts"] = {}
        e.save(self.repo.folder(self.pid) / "project.json", project)
        before = e.digest(project)
        positive = "  Two people in a library.\nA returns a book to B.  "
        negative = " text, blurry\n"
        row = {"panel_id": self.target, "panel_hash": e.digest(self.panel), "positive": positive, "negative": negative}
        self.prompts=[]
        buf=io.BytesIO();Image.new("RGB",(32,32),"white").save(buf,format="PNG");raw=buf.getvalue()
        class Response:
            def __enter__(self):return self
            def __exit__(self,*_):pass
            def read(self,_):return raw
        class Opener:
            def open(self,*_,**kwargs):return Response()
        for model in ["test.safetensors",anima.CHECKPOINT]:
            file = ("anime" if model == anima.CHECKPOINT else "sdxl") + "/external-test.safetensors"
            lora = {"file":file,"weight":0.65,"clip_weight":0.45,"trigger_words":"do not append this",
                    "compatible_checkpoint":model,"confirmed_compatibility":True,
                    "character_id":"project:" + self.panel["characters"][0]["character_id"]}
            with self.subTest(model=model), patch.object(comfyui,"discover",return_value={"ready":True,"checkpoints":[model],"loras":[file]}), \
                 patch.object(comfyui,"api",self.mock_api), patch("urllib.request.build_opener",return_value=Opener()), \
                 patch("comic_workflow.lora_binding.route",side_effect=AssertionError("Frozen import must not reroute")), \
                 patch("comic_workflow.telemetry.websocket.create_connection",side_effect=ConnectionError("offline")):
                result=comfyui.run_batch(self.repo,self.pid,self.target,lambda _:None,model,2,"seeds",[lora],
                    render_options={"external_prompt":True,"steps":12,"aspect_ratio":"7:5","resolution":"1536"},prompt_override=row)
                graph=self.prompts[-1]
                latent = next(n for n in graph.values() if n["class_type"] in {"EmptyLatentImage", "EmptySD3LatentImage"})
                self.assertEqual((latent["inputs"]["width"], latent["inputs"]["height"]), (1536, 1096))
                self.assertEqual(graph["2"]["inputs"]["prompt" if model==anima.CHECKPOINT else "text"],positive)
                self.assertEqual(graph["3"]["inputs"]["text"],negative)
                self.assertEqual(graph["5"]["inputs"]["steps"],12)
                self.assertFalse(result["review_required"])
                self.assertEqual(result["images"][0]["positive"],positive)
                self.assertEqual(len(result["images"]),2)
                for workflow in self.prompts[-2:]:
                    loaders = [n for n in workflow.values() if n["class_type"] == "LoraLoader"]
                    self.assertEqual(len(loaders),1)
                    self.assertEqual(loaders[0]["inputs"]["lora_name"],file)
                    self.assertEqual(loaders[0]["inputs"]["strength_model"],0.65)
                    self.assertEqual(loaders[0]["inputs"]["strength_clip"],0.45)
                self.assertEqual(result["loras"],[lora])
        self.assertEqual(e.digest(self.repo.get(self.pid)),before)

    def test_three_candidates_and_restart_selection(self):
        self.prompts = []
        buf = io.BytesIO()
        Image.new("RGB", (80, 80), "#ccbbaa").save(buf, format="PNG")
        raw = buf.getvalue()

        class Response:
            def __enter__(s):
                return s

            def __exit__(s, *a):
                pass

            def read(s, n):
                return raw

        class Opener:
            def open(s, *a, **k):
                return Response()

        with (
            patch.object(
                comfyui,
                "discover",
                return_value={"ready": True, "checkpoints": ["test.safetensors"]},
            ),
            patch.object(comfyui, "api", self.mock_api),
            patch(
                "comic_workflow.telemetry.websocket.create_connection",
                side_effect=ConnectionError("offline test"),
            ),
            patch("urllib.request.build_opener", return_value=Opener()),
        ):
            b = comfyui.run_batch(
                self.repo, self.pid, self.target, lambda x: None, "test.safetensors"
            )
        self.assertEqual(len(b["images"]), 3)
        self.assertEqual(len(self.prompts), 3)
        self.assertTrue(
            all(
                "silver hair, green eyes" in graph["2"]["inputs"]["text"]
                for graph in self.prompts
            )
        )
        self.assertEqual(len(set(x["5"]["inputs"]["seed"] for x in self.prompts)), 3)
        self.assertEqual({x["4"]["inputs"]["batch_size"] for x in self.prompts}, {1})
        comfyui.select_image(self.repo, self.pid, self.target, b["images"][1]["id"])
        reopened = Repository(self.tmp.name)
        self.assertEqual(
            comfyui.drawing_state(reopened, self.pid)["selections"][self.target][
                "image"
            ]["id"],
            b["images"][1]["id"],
        )

    def test_unapproved_prompt_never_submits(self):
        with self.assertRaises(ValueError):
            comfyui.run_batch(self.repo, self.pid, "P001-K02", lambda x: None)


    def test_rejected_image_requires_manual_choice_and_keeps_verdict(self):
        image = {
            "id": "failed-1",
            "file": "failed/1.png",
            "review": {"verdict": "reject", "reason": "wrong pose"},
        }
        batch = {
            "id": "failed",
            "panel_id": self.target,
            "panel_hash": e.digest(self.panel),
            "images": [image],
        }
        e.save(
            self.repo.folder(self.pid) / "images/index.json",
            {"batches": [batch], "selections": {}},
        )
        with self.assertRaisesRegex(ValueError, "手动选择"):
            comfyui.select_image(self.repo, self.pid, self.target, image["id"])
        result = comfyui.select_image(
            self.repo, self.pid, self.target, image["id"], allow_rejected=True
        )
        self.assertTrue(result["selections"][self.target]["manual_override"])
        self.assertEqual(result["batches"][0]["images"][0]["review"], image["review"])
        reopened = comfyui.drawing_state(Repository(self.tmp.name), self.pid)
        self.assertEqual(
            reopened["selections"][self.target]["image"]["review"]["verdict"], "reject"
        )

    def test_pending_image_can_be_explicitly_chosen_without_changing_review(self):
        image = {"id": "pending-1", "file": "pending/1.png", "review": {"verdict": "pending"}}
        batch = {"id": "pending", "panel_id": self.target, "panel_hash": e.digest(self.panel), "images": [image]}
        e.save(self.repo.folder(self.pid) / "images/index.json", {"batches": [batch], "selections": {}})
        result = comfyui.select_image(self.repo, self.pid, self.target, image["id"], allow_unreviewed=True)
        self.assertTrue(result["selections"][self.target]["manual_unreviewed"])
        self.assertEqual(result["batches"][0]["images"][0]["review"]["verdict"], "pending")
        batch["panel_hash"] = "stale"
        e.save(self.repo.folder(self.pid) / "images/index.json", {"batches": [batch], "selections": {}})
        with self.assertRaises(ValueError):
            comfyui.select_image(self.repo, self.pid, self.target, image["id"], allow_unreviewed=True)

    def test_manual_choice_does_not_allow_pending_or_stale_image(self):
        for verdict, panel_hash in [
            ("pending", e.digest(self.panel)),
            ("reject", "old-hash"),
        ]:
            with self.subTest(verdict=verdict, panel_hash=panel_hash):
                batch = {
                    "id": "test",
                    "panel_id": self.target,
                    "panel_hash": panel_hash,
                    "images": [{"id": "test-1", "review": {"verdict": verdict}}],
                }
                e.save(
                    self.repo.folder(self.pid) / "images/index.json",
                    {"batches": [batch], "selections": {}},
                )
                with self.assertRaises(ValueError):
                    comfyui.select_image(
                        self.repo, self.pid, self.target, "test-1", allow_rejected=True
                    )


    def test_modified_panel_requires_new_prompt(self):
        p = self.repo.get(self.pid)
        panel = copy.deepcopy(self.panel)
        panel["purpose"] += "修改"
        payload = {
            "panel": panel,
            "lettering": [
                x
                for x in p["pages"]["P001"]["lettering"]
                if x["panel_id"] == self.target
            ],
        }
        c = self.repo.propose(self.pid, "panel", self.target, payload, e.digest(p))
        self.repo.accept(self.pid, c["id"])
        with self.assertRaises(ValueError):
            comfyui.run_batch(self.repo, self.pid, self.target, lambda x: None)

    def test_layout_requires_current_selection(self):
        p = self.repo.get(self.pid)
        l = layout.default_layout(p["pages"]["P001"])
        with self.assertRaises(ValueError):
            layout.compose(self.repo, self.pid, "P001", l)

    def test_lora_graph_wires_model_and_clip(self):
        g = comfyui.graph(
            "base.safetensors",
            "positive",
            "negative",
            1,
            comfyui.config(),
            [{"file": "character.safetensors", "weight": 0.7, "clip_weight": 0.6}],
        )
        self.assertEqual(g["5"]["inputs"]["model"], ["8", 0])
        self.assertEqual(g["2"]["inputs"]["clip"], ["8", 1])
        self.assertEqual(g["8"]["inputs"]["strength_model"], 0.7)

    def test_lora_incompatible_base_rejected(self):
        with patch.object(
            comfyui,
            "discover",
            return_value={
                "ready": True,
                "checkpoints": ["test.safetensors"],
                "loras": ["x.safetensors"],
            },
        ):
            with self.assertRaises(ValueError):
                comfyui.run_batch(
                    self.repo,
                    self.pid,
                    self.target,
                    lambda x: None,
                    "test.safetensors",
                    loras=[{"file": "x.safetensors", "compatible_checkpoint": "other"}],
                )

    def test_layout_bounds_and_direction(self):
        page = copy.deepcopy(self.repo.get(self.pid)["pages"]["P001"])
        page["panels"].append(copy.deepcopy(page["panels"][0]))
        page["panels"][-1]["id"] = "P001-K04"
        page["reading_direction"] = "RTL"
        l = layout.default_layout(page, "balanced")
        self.assertGreater(l["panels"]["P001-K01"]["x"], l["panels"]["P001-K02"]["x"])
        l["panels"]["P001-K01"]["w"] = 100000
        with self.assertRaises(ValueError):
            layout.validate_layout(l, page)

    def test_chinese_lettering_export_separate_and_valid(self):
        import xml.etree.ElementTree as ET

        p = self.repo.get(self.pid)
        page = p["pages"]["P001"]
        folder = self.repo.folder(self.pid) / "images/test-batch"
        folder.mkdir(parents=True)
        Image.new("RGB", (128, 128), "#ddd").save(folder / "1.png")
        selections = {
            panel["id"]: {
                "image": {"file": "test-batch/1.png", "id": panel["id"]},
                "panel_hash": e.digest(panel),
            }
            for panel in page["panels"]
        }
        e.save(
            self.repo.folder(self.pid) / "images/index.json",
            {"selections": selections, "batches": []},
        )
        result = layout.compose(
            self.repo, self.pid, "P001", layout.default_layout(page)
        )
        out = self.repo.folder(self.pid) / "layouts" / result["bundle"]
        text = (out / "lettering.svg").read_text(encoding="utf-8")
        ET.fromstring(text)
        self.assertIn("您丢了什么？", text)
        self.assertNotIn("<image", text)
        with Image.open(out / "page.png") as img:
            self.assertEqual(img.size, (1600, 2264))

    def test_sixteen_candidates_and_uncertain_submission_are_durable(self):
        self.prompts = []
        buf = io.BytesIO()
        Image.new("RGB", (80, 80), "#aabbcc").save(buf, format="PNG")

        class Response:
            def __enter__(s):
                return s

            def __exit__(s, *args):
                pass

            def read(s, n):
                return buf.getvalue()

        with (
            patch.object(
                comfyui,
                "discover",
                return_value={"ready": True, "checkpoints": ["test.safetensors"]},
            ),
            patch.object(comfyui, "api", self.mock_api),
            patch(
                "comic_workflow.telemetry.websocket.create_connection",
                side_effect=ConnectionError("offline"),
            ),
            patch("urllib.request.OpenerDirector.open", return_value=Response()),
        ):
            batch = comfyui.run_batch(
                self.repo, self.pid, self.target, lambda _: None, "test.safetensors", 16
            )
        self.assertEqual([i["view_index"] for i in batch["images"]], list(range(1, 17)))
        self.assertTrue(
            (self.repo.folder(self.pid) / "images" / batch["id"] / "16.png").is_file()
        )
        with (
            patch.object(
                comfyui,
                "discover",
                return_value={"ready": True, "checkpoints": ["test.safetensors"]},
            ),
            patch.object(comfyui, "api", side_effect=TimeoutError("reply lost")),
            patch(
                "comic_workflow.telemetry.websocket.create_connection",
                side_effect=ConnectionError("offline"),
            ),
            self.assertRaises(TimeoutError),
        ):
            comfyui.run_batch(
                self.repo,
                self.pid,
                self.target,
                lambda _: None,
                "test.safetensors",
                1,
                candidate_indices=[15],
            )
        unknown = comfyui.drawing_state(self.repo, self.pid)["batches"][-1]
        self.assertEqual(unknown["status"], "uncertain")
        self.assertEqual(unknown["candidate_indices"], [15])
