import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from comic_workflow import comfyui, engine as e, prompt_models, quick_sample
from comic_workflow.jobs import Jobs
from comic_workflow.repository import Repository
from comic_workflow.lora_folders import matches


class PromptSessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Repository(Path(self.tmp.name) / "projects")
        self.addCleanup(self.repo._temporary.cleanup)

    def test_prompt_session_is_not_a_work_and_can_be_released(self):
        checkpoint = "anima-aesthetic-v1.1.safetensors"
        with patch.object(comfyui, "discover", return_value={"checkpoints": [checkpoint]}):
            pid = quick_sample.create(self.repo, {"text": "A blue mountain", "checkpoint": checkpoint})["project"]
        self.assertTrue(self.repo.get(pid)["transient"])
        self.assertEqual(self.repo.listing(), [])
        self.assertFalse(list(self.repo.root.rglob("project.json")))
        folder = self.repo.folder(pid)
        self.repo.release_transient(pid)
        self.assertFalse(folder.exists())
        with self.assertRaises(ValueError):
            self.repo.folder(pid)
        with self.assertRaises(ValueError):
            self.repo.release_transient("../outside")

    def test_bulk_delete_validates_entire_selection_and_is_recoverable(self):
        ids = [self.repo.create(None, True) for _ in range(2)]
        items = [{"id": pid, "base_hash": e.digest(self.repo.get(pid))} for pid in ids]
        with self.assertRaises(ValueError):
            self.repo.delete_many([items[0], {**items[1], "base_hash": "stale"}])
        self.assertEqual(len(self.repo.listing()), 2)
        self.assertEqual(self.repo.trash(), [])
        e.save(self.repo.folder(ids[1]) / "images/index.json", {"batches": [{"status": "uncertain"}]})
        self.repo.delete_many(items)
        self.assertEqual(self.repo.listing(), [])
        for pid in ids:
            self.repo.restore_deleted(pid)
        self.assertEqual(len(self.repo.listing()), 2)

    def test_transient_job_never_writes_history(self):
        with patch("comic_workflow.jobs.DATA", Path(self.tmp.name)):
            jobs = Jobs()
        jid = jobs.submit("temporary", lambda update, jid: {"value": "private prompt"}, "tmp-test")
        for _ in range(200):
            if jid not in jobs.controls:
                break
            time.sleep(.01)
        self.assertEqual(jobs.get(jid)["status"], "done")
        self.assertEqual(list(jobs.root.glob("*.json")), [])

    def test_clear_images_preserves_inputs_and_allows_new_results(self):
        with patch("comic_workflow.jobs.DATA", Path(self.tmp.name)):
            jobs = Jobs()
        app = SimpleNamespace(repo=self.repo, jobs=jobs)
        pid = self.repo.create_transient()
        folder = self.repo.folder(pid)
        project = {"quick_sample": True, "prompt_input": {"text": "blue mountain", "count": 2}}
        e.save(folder / "project.json", project)
        (folder / "images/batch").mkdir(parents=True)
        (folder / "images/batch/old.png").write_bytes(b"old image")
        e.save(folder / "images/index.json", {"batches": [{"images": [{"id": "old"}]}],
                                              "selections": {"panel": "old"}})
        sibling = self.repo.create_transient()
        e.save(self.repo.folder(sibling) / "project.json", project)
        self.assertEqual(quick_sample.clear_images(app, pid), {"cleared": 1})
        self.assertFalse((folder / "images").exists())
        self.assertEqual(self.repo.get(pid), project)
        self.assertEqual(self.repo.get(sibling), project)
        self.assertEqual(comfyui.drawing_state(self.repo, pid), {"batches": [], "selections": {}})
        self.assertEqual(quick_sample.clear_images(app, pid), {"cleared": 0})
        e.save(folder / "images/index.json", {"batches": [{"images": [{"id": "new"}]}]})
        self.assertEqual(comfyui.drawing_state(self.repo, pid)["batches"][0]["images"], [{"id": "new"}])

    def test_saved_queue_output_is_backed_up_and_uncertain_work_is_not_cleared(self):
        with patch("comic_workflow.jobs.DATA", Path(self.tmp.name)):
            jobs = Jobs()
        with patch.object(comfyui,"discover",return_value={"checkpoints":["test.safetensors"]}):
            pid=quick_sample.create(self.repo,{"text":"quiet lake","checkpoint":"test.safetensors"},persistent=True)["project"]
        app=SimpleNamespace(repo=self.repo,jobs=jobs)
        image=self.repo.folder(pid)/"images/batch/one.png"
        image.parent.mkdir(parents=True);image.write_bytes(b"keep-original")
        index=self.repo.folder(pid)/"images/index.json"
        e.save(index,{"batches":[{"status":"uncertain","images":[{"file":"batch/one.png"}]}],"selections":{}})
        with self.assertRaisesRegex(ValueError,"状态未确认"):
            quick_sample.clear_images(app,pid)
        self.assertTrue(image.exists())
        e.save(index,{"batches":[{"status":"done","images":[{"file":"batch/one.png"}]}],"selections":{}})
        result=quick_sample.clear_images(app,pid)
        self.assertEqual((Path(result["backup"])/"images/batch/one.png").read_bytes(),b"keep-original")
        self.assertFalse(image.exists())
        self.assertEqual(self.repo.get(pid)["prompt_input"]["text"],"quiet lake")

    def test_clear_images_rejects_active_jobs_and_regular_projects(self):
        with patch("comic_workflow.jobs.DATA", Path(self.tmp.name)):
            jobs = Jobs()
        app = SimpleNamespace(repo=self.repo, jobs=jobs)
        pid = self.repo.create_transient()
        e.save(self.repo.folder(pid) / "project.json", {"quick_sample": True})
        path = self.repo.folder(pid) / "images/index.json"
        e.save(path, {"batches": []})
        for status in ("running", "pausing", "paused", "cancelling"):
            jobs.memory["active"] = {"project": pid, "status": status, "created": 1}
            with self.assertRaises(ValueError):
                quick_sample.clear_images(app, pid)
            self.assertTrue(path.exists())
        jobs.memory.clear()
        jobs.controls["finishing"] = {"job": {"project": pid, "status": "done"}}
        with self.assertRaises(ValueError):
            quick_sample.clear_images(app, pid)
        self.assertTrue(path.exists())
        jobs.controls.clear()
        regular = self.repo.create(None, True)
        with self.assertRaises(ValueError):
            quick_sample.clear_images(app, regular)

    def test_new_models_have_distinct_graphs_and_compatible_lora_folders(self):
        for checkpoint, profile in prompt_models.MODELS.items():
            cfg = {**prompt_models.defaults(checkpoint), "width": 832, "height": 1216, "literal_prompt": True}
            graph = comfyui.graph(checkpoint, "literal scene", "watermark", 42, cfg)
            if profile[1] == "anima38":
                self.assertEqual(graph["1"]["class_type"], "Anima38BV2Loader")
            else:
                self.assertEqual(graph["1"]["inputs"]["unet_name"], checkpoint)
            if profile[1] == "krea2":
                self.assertEqual(graph["3"]["class_type"], "ConditioningZeroOut")
                self.assertEqual(graph["5"]["inputs"]["steps"], 8)
                self.assertFalse(matches("anime/example.safetensors", checkpoint))
                self.assertFalse(matches("sdxl/example.safetensors", checkpoint))
                self.assertTrue(matches("krea2/example.safetensors", checkpoint))
                self.assertEqual(graph["10"]["inputs"]["clip_name"],
                                 "qwen3vl_4b_bf16.safetensors" if "bf16" in checkpoint else "qwen3vl_4b_fp8_scaled.safetensors")
            elif profile[1] == "moss_image":
                self.assertEqual(graph["10"]["inputs"]["clip_name"], "qwen3.5_9b_bf16.safetensors")
                self.assertEqual(graph["10"]["inputs"]["type"], "moss_image")
                self.assertEqual(graph["12"]["inputs"]["vae_name"], "flux2-vae.safetensors")
                self.assertEqual(graph["4"]["class_type"], "EmptyFlux2LatentImage")
                self.assertEqual(graph["3"]["inputs"]["text"], "watermark")
                self.assertEqual(graph["5"]["inputs"]["steps"], 25)
                self.assertTrue(matches("moss_image/example.safetensors", checkpoint))
                for wrong in ("anime", "sdxl", "krea2", "legacy"):
                    self.assertFalse(matches(wrong + "/example.safetensors", checkpoint))
            else:
                self.assertTrue(matches("anime/example.safetensors", checkpoint))
                self.assertFalse(matches("sdxl/example.safetensors", checkpoint))

    def test_prompt_workspace_persists_and_delete_cache_15_step_cap(self):
        from comic_workflow import image_delete_cache as idc
        from comic_workflow.repository import PROMPT_WORKSPACE_ID

        with patch("comic_workflow.jobs.DATA", Path(self.tmp.name)):
            jobs = Jobs()
        app = SimpleNamespace(repo=self.repo, jobs=jobs)
        ws_dir = self.repo.folder(PROMPT_WORKSPACE_ID)
        e.save(ws_dir / "project.json", {"quick_sample": True, "transient": True, "prompt_input": {"text": "keep me"}})
        self.assertTrue(self.repo.has_prompt_workspace())
        self.assertEqual(self.repo.listing(), [])

        # Populate 18 base images + 1 second-pass variant attached to img-18
        imgs = []
        for i in range(1, 19):
            rel = f"b1/{i}.png"
            p = ws_dir / "images" / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(f"png-{i}".encode("utf-8"))
            imgs.append({"id": f"img-{i}", "file": rel, "seed": i})
        sp_rel = "sp1/1.png"
        sp_path = ws_dir / "images" / sp_rel
        sp_path.parent.mkdir(parents=True, exist_ok=True)
        sp_path.write_bytes(b"sp-18")
        e.save(
            ws_dir / "images/index.json",
            {
                "batches": [
                    {"id": "b1", "status": "done", "images": imgs},
                    {
                        "id": "sp1",
                        "kind": "second_pass",
                        "source_image_id": "img-18",
                        "status": "done",
                        "images": [{"id": "sp1-1", "file": sp_rel, "seed": 99}],
                    },
                ],
                "selections": {},
                "second_pass_applied": {"img-18": "sp1-1"},
            },
        )

        # Workspace survives transient temp cleanup and fresh Repository re-open
        self.repo._temporary.cleanup()
        reopened = Repository(Path(self.tmp.name) / "projects")
        self.addCleanup(reopened._temporary.cleanup)
        self.assertTrue(reopened.has_prompt_workspace())
        self.assertEqual(len(comfyui.drawing_state(reopened, PROMPT_WORKSPACE_ID)["batches"][0]["images"]), 18)
        app = SimpleNamespace(repo=reopened, jobs=jobs)

        # Delete 17 images (img-1 .. img-17); after 15 steps oldest cache entries are permanently deleted
        entry_dirs = []
        cache_dir = idc.cache_root(reopened)
        for i in range(1, 18):
            res = idc.delete_image(app, PROMPT_WORKSPACE_ID, f"img-{i}")
            entry_dirs.append(cache_dir / res["cache_entry_id"])
            self.assertFalse((ws_dir / f"images/b1/{i}.png").exists())
            self.assertTrue((cache_dir / res["cache_entry_id"] / f"files/b1/{i}.png").is_file())
            if i <= 15:
                self.assertEqual(res["undo_depth"], i)
                self.assertEqual(res["evicted"], 0)
            else:
                self.assertEqual(res["undo_depth"], 15)
                self.assertEqual(res["evicted"], 1)

        # Entries 1 and 2 (beyond 15 steps) must be permanently deleted from cache
        self.assertFalse(entry_dirs[0].exists())
        self.assertFalse(entry_dirs[1].exists())
        for d in entry_dirs[2:]:
            self.assertTrue(d.is_dir())

        # Undo 15 times restores img-17 down to img-3; 16th undo reports nothing left
        for expected_i in range(17, 2, -1):
            undone = idc.undo_delete_image(app, PROMPT_WORKSPACE_ID)
            self.assertTrue(undone["restored"])
            self.assertEqual(undone["restored_image_id"], f"img-{expected_i}")
            self.assertEqual((ws_dir / f"images/b1/{expected_i}.png").read_bytes(), f"png-{expected_i}".encode("utf-8"))
        self.assertFalse(idc.undo_delete_image(app, PROMPT_WORKSPACE_ID)["restored"])
        self.assertFalse((ws_dir / "images/b1/1.png").exists())
        self.assertFalse((ws_dir / "images/b1/2.png").exists())

        # Deleting base image with applied second-pass variant moves both in 1 step and restores both in 1 undo
        res_group = idc.delete_image(app, PROMPT_WORKSPACE_ID, "img-18", delete_variants=True)
        self.assertEqual(res_group["deleted_count"], 2)
        self.assertFalse((ws_dir / "images/b1/18.png").exists())
        self.assertFalse((ws_dir / "images/sp1/1.png").exists())
        self.assertNotIn("img-18", comfyui.drawing_state(reopened, PROMPT_WORKSPACE_ID).get("second_pass_applied", {}))

        undone_group = idc.undo_delete_image(app, PROMPT_WORKSPACE_ID)
        self.assertTrue(undone_group["restored"])
        self.assertEqual(undone_group["restored_count"], 2)
        self.assertEqual((ws_dir / "images/b1/18.png").read_bytes(), b"png-18")
        self.assertEqual((ws_dir / "images/sp1/1.png").read_bytes(), b"sp-18")
        self.assertEqual(
            comfyui.drawing_state(reopened, PROMPT_WORKSPACE_ID).get("second_pass_applied", {}).get("img-18"),
            "sp1-1",
        )


if __name__ == "__main__":
    unittest.main()
