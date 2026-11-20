import copy
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from comic_workflow import engine as e, comfyui
from comic_workflow.batch_drawing import draw_project
from comic_workflow.repository import Repository
from comic_workflow.jobs import Jobs
from comic_workflow import drawing_settings


class BatchDrawingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        telemetry_data = patch("comic_workflow.telemetry.DATA", self.root)
        telemetry_data.start()
        self.addCleanup(telemetry_data.stop)
        self.repo = Repository(self.root / "projects")
        self.pid = self.repo.create(None, True)
        self.p = self.repo.get(self.pid)
        self.panels = [x for page in self.p["pages"].values() for x in page["panels"]]
        self.app = SimpleNamespace(repo=self.repo)
        self.request = {"base_hash": e.digest(self.p), "checkpoint": "test.safetensors"}

    def test_all_panels_sequential_loras_and_final_progress(self):
        seen, updates = [], []
        self.request["loras_by_panel"] = {
            self.panels[0]["id"]: [{"file": "named.safetensors"}]
        }

        def draw(app, pid, args, update, jid):
            seen.append(args)
            update(
                {
                    "message": "绘制",
                    "progress": {"completed": 2, "total": 6, "unit": "张"},
                }
            )
            return {"unresolved_views": [2] if len(seen) == 1 else []}

        with patch("comic_workflow.batch_drawing.reviewed_batch", side_effect=draw):
            result = draw_project(
                self.app, self.pid, self.request, updates.append, "test"
            )
        self.assertEqual([x["target"] for x in seen], [x["id"] for x in self.panels])
        self.assertEqual(seen[0]["loras"], [{"file": "named.safetensors"}])
        self.assertEqual(updates[-1]["progress"]["completed"], len(self.panels))
        self.assertEqual(result["unresolved"][0]["views"], [2])
        self.assertEqual(self.repo.get(self.pid), self.p)

    def test_selection_and_six_reviewed_views_are_reused_but_old_images_are_not(self):
        a, b, old = self.panels[:3]
        index = {
            "selections": {
                a["id"]: {"panel_hash": e.digest(a), "image": {"id": "chosen"}}
            },
            "batches": [],
        }
        for panel, digest in [(b, e.digest(b)), (old, "old-version")]:
            index["batches"].append(
                {
                    "panel_id": panel["id"],
                    "panel_hash": digest,
                    "checkpoint": "test.safetensors",
                    "images": [
                        {"view_index": i, "review": {"verdict": "pass"}}
                        for i in range(1, 7)
                    ],
                }
            )
        e.save(self.repo.folder(self.pid) / "images/index.json", index)
        with patch(
            "comic_workflow.batch_drawing.reviewed_batch",
            return_value={"unresolved_views": []},
        ) as draw:
            result = draw_project(
                self.app, self.pid, self.request, lambda x: None, "reuse"
            )
        self.assertEqual(result["skipped"], [a["id"], b["id"]])
        self.assertEqual(draw.call_count, len(self.panels) - 2)
        self.assertEqual(comfyui.drawing_state(self.repo, self.pid), index)

    def test_incomplete_pages_do_not_start_gpu(self):
        p = copy.deepcopy(self.p)
        del p["pages"][list(p["pages"])[-1]]
        e.save(self.repo.folder(self.pid) / "project.json", p)
        self.request["base_hash"] = e.digest(p)
        with (
            patch("comic_workflow.batch_drawing.reviewed_batch") as draw,
            self.assertRaisesRegex(ValueError, "确认所有页面"),
        ):
            draw_project(self.app, self.pid, self.request, lambda x: None, "missing")
        draw.assert_not_called()

    def test_restored_completed_reports_do_not_distort_gpu_duration_estimates(self):
        with (
            patch(
                "comic_workflow.batch_drawing.reviewed_batch",
                return_value={"unresolved_views": [1], "reused_complete": True},
            ),
            patch("comic_workflow.batch_drawing.record_sample") as record,
        ):
            result = draw_project(
                self.app, self.pid, self.request, lambda x: None, "restored"
            )
        record.assert_not_called()
        self.assertEqual(len(result["completed"]), len(self.panels))
        self.assertEqual(len(result["unresolved"]), len(self.panels))

    def test_mid_run_story_change_stops_before_next_panel(self):
        def draw(*args):
            p = self.repo.get(self.pid)
            p["brief"]["title"] += "用户修改"
            e.save(self.repo.folder(self.pid) / "project.json", p)
            return {"unresolved_views": []}

        with (
            patch(
                "comic_workflow.batch_drawing.reviewed_batch", side_effect=draw
            ) as mocked,
            self.assertRaisesRegex(ValueError, "分镜发生变化"),
        ):
            draw_project(self.app, self.pid, self.request, lambda x: None, "changed")
        self.assertEqual(mocked.call_count, 1)

    def test_failure_keeps_report_and_does_not_submit_more_gpu_work(self):
        with (
            patch(
                "comic_workflow.batch_drawing.reviewed_batch",
                side_effect=ValueError("GPU timeout"),
            ) as draw,
            self.assertRaisesRegex(ValueError, "已有图片和选图保留"),
        ):
            draw_project(self.app, self.pid, self.request, lambda x: None, "failed")
        draw.assert_called_once()
        report = e.read(self.repo.folder(self.pid) / "runs/failed/all-drawing.json")
        self.assertEqual(len(report["errors"]), 1)

    def test_job_numeric_progress_survives_text_updates_and_failure(self):
        with patch("comic_workflow.jobs.DATA", self.root):
            jobs = Jobs()

            def run(update, jid):
                update(
                    {
                        "message": "第一格",
                        "progress": {"completed": 1, "total": 6, "unit": "格"},
                    }
                )
                update("等待模型")
                raise ValueError("中断测试")

            jid = jobs.submit("进度测试", run)
            for _ in range(100):
                job = jobs.get(jid)
                if job["status"] != "running":
                    break
                time.sleep(0.01)
            self.assertEqual(job["status"], "failed")
            self.assertEqual(job["progress"]["completed"], 1)
            self.assertIn("finished_at", job)

    def test_lora_bulk_scopes_and_saved_choices_survive_reload(self):
        cast = self.p["canon"]["characters"]
        names = [x["name"] for x in cast]

        def item(file, scope, names):
            return {
                "file": file,
                "name": file,
                "characters": names,
                "scope": scope,
                "selected": True,
                "confirmed": True,
                "weight": 0.7,
                "trigger_words": "",
            }

        prefs = {
            "mode": "manual",
            "checkpoint": "test.safetensors",
            "items": [item("global", "story", []), item("cast", "characters", names)],
        }
        drawing_settings.save(self.repo, self.pid, prefs)
        self.assertEqual(
            drawing_settings.load(Repository(self.repo.root), self.pid), prefs
        )
        with_cast = {"id": "test", "characters": [{"character_id": cast[0]["id"]}]}
        empty = {"id": "empty", "characters": []}
        self.assertEqual(
            [
                x["file"]
                for x in drawing_settings.loras_for_panel(prefs, self.p, with_cast)
            ],
            ["global", "cast"],
        )
        self.assertEqual(
            [x["file"] for x in drawing_settings.loras_for_panel(prefs, self.p, empty)],
            ["global"],
        )
        self.assertEqual(self.repo.get(self.pid), self.p)
        prefs["mode"] = "none"
        drawing_settings.save(self.repo, self.pid, prefs)
        self.assertEqual(
            len(
                list(
                    (self.repo.folder(self.pid) / "drawing-settings-history").glob(
                        "*.json"
                    )
                )
            ),
            1,
        )

    def test_lora_combined_limit_fails_before_any_gpu_work(self):
        prefs = {
            "mode": "manual",
            "checkpoint": "test.safetensors",
            "items": [
                {
                    "file": str(i),
                    "name": str(i),
                    "scope": "story" if i < 4 else "characters",
                    "characters": [x["name"] for x in self.p["canon"]["characters"]],
                    "selected": True,
                    "confirmed": True,
                    "weight": 0.7,
                }
                for i in range(6)
            ],
        }
        drawing_settings.save(self.repo, self.pid, prefs)
        with (
            patch("comic_workflow.batch_drawing.reviewed_batch") as draw,
            self.assertRaisesRegex(ValueError, "超过五个"),
        ):
            draw_project(self.app, self.pid, self.request, lambda x: None, "limit")
        draw.assert_not_called()
