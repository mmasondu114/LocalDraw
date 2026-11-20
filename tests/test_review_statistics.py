import tempfile
import unittest
from pathlib import Path

from comic_workflow import engine as e, art_presets
from comic_workflow.repository import Repository
from comic_workflow.review_statistics import review_statistics


class ReviewStatisticsTests(unittest.TestCase):
    def test_per_panel_counts_keep_zero_success_and_uncertain_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repository(Path(directory) / "projects")
            pid = repo.create(None, True)
            prefs = {"art_preset": "lora"}
            e.save(repo.folder(pid) / "drawing-settings.json", prefs)
            current = art_presets.signature(prefs)

            def batch(panel, verdicts, signature=current, diagnostic=False):
                return {
                    "panel_id": panel,
                    "art_preset_hash": signature,
                    "render_options": {"diagnostic": diagnostic},
                    "images": [{"review": {"verdict": v}} for v in verdicts],
                }

            e.save(
                repo.folder(pid) / "images/index.json",
                {
                    "selections": {},
                    "batches": [
                        batch("P001-K01", ["reject", "pass", "pending"]),
                        batch("P001-K01", ["reject"]),
                        batch("P001-K02", ["reject", "uncertain"]),
                        batch("P001-K03", ["pending"]),
                        batch("P001-K01", ["pass"], diagnostic=True),
                        batch("P001-K01", ["reject"] * 10, signature=None),
                    ],
                },
            )
            result = review_statistics(repo, pid)
            self.assertEqual(len(result["groups"]), 3)
            group = next(g for g in result["groups"] if g["current"])
            self.assertEqual(group["rejections_per_pass"], 3)
            self.assertEqual(group["reviewed"], 4)
            self.assertEqual(group["panels_without_pass"], 2)
            panels = group["panel_details"]
            self.assertEqual(panels[0]["rejections_per_pass"], 2)
            self.assertIsNone(panels[1]["rejections_per_pass"])
            self.assertEqual(panels[1]["counts"]["uncertain"], 1)
            self.assertIsNone(panels[2]["rejections_per_pass"])


if __name__ == "__main__":
    unittest.main()
