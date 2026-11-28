import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from comic_workflow import engine as e
from comic_workflow.model import (
    budget_messages,
    build_context,
    repair_lettering_numbering,
    generate,
)
from comic_workflow.repository import Repository
from comic_workflow.app_core import StudioApp as Application


class ModelBudgetTests(unittest.TestCase):
    def test_drawing_schema_keeps_background_cast_and_rejects_missing_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Repository(Path(tmp) / "projects")
            original = json.dumps(e.SCHEMAS, sort_keys=True)
            for cast_count in (0, 1, 2):
                with self.subTest(cast_count=cast_count):
                    p = repo.get(repo.create(None, True))
                    panel = p["pages"]["P001"]["panels"][0]
                    template = copy.deepcopy(panel["characters"][0])
                    cast = p["canon"]["characters"][:cast_count]
                    panel["characters"] = [
                        {**template, "character_id": c["id"], "position": "背景虚焦"}
                        for c in cast
                    ]
                    payload = {
                        "panel_id": panel["id"],
                        "positive": "office scene",
                        "negative": "extra people",
                        "adaptation_notes": "test",
                        "visual_plan": {"version": 1, "detailed_scene": "办公室画面",
                                        "must_show": ["办公室"], "relationships": [], "uncertain": []},
                        "composition_variants": [f"view {i}" for i in range(6)],
                        "character_anchors": [
                            {"character_id": c["id"], "tags": "known outfit"}
                            for c in cast
                        ],
                        "framing": {
                            "version": 2,
                            "camera": "medium shot",
                            "forbidden": "extra people",
                            "subjects": [
                                {
                                    "character_id": c["id"],
                                    "visible": "upper",
                                    "region": [0, 0, 1, 1],
                                }
                                for c in cast
                            ],
                            "guide_lines": [],
                            "guide_ellipses": [],
                            "guide_note": "test",
                        },
                    }
                    before = e.digest(p)
                    response = {
                        "choices": [
                            {
                                "finish_reason": "stop",
                                "message": {"content": json.dumps(payload)},
                            }
                        ]
                    }
                    with (
                        patch("comic_workflow.model.count_messages", return_value=1000),
                        patch(
                            "comic_workflow.model.chat_stream", return_value=response
                        ) as stream,
                    ):
                        generate(
                            p, "drawing_prompt", panel["id"], "", Path(tmp) / "run"
                        )
                    body = stream.call_args.args[1]
                    schema = body["response_format"]["json_schema"]["schema"]
                    e.jsonschema.validate(payload, schema)
                    if cast_count:
                        missing = copy.deepcopy(payload)
                        missing["character_anchors"].pop()
                        with self.assertRaises(e.jsonschema.ValidationError):
                            e.jsonschema.validate(missing, schema)
                        wrong = copy.deepcopy(payload)
                        wrong["character_anchors"][0]["character_id"] = "library-uuid"
                        with self.assertRaises(e.jsonschema.ValidationError):
                            e.jsonschema.validate(wrong, schema)
                    if cast_count == 2:
                        duplicate = copy.deepcopy(payload)
                        duplicate["character_anchors"][1] = duplicate[
                            "character_anchors"
                        ][0]
                        with self.assertRaises(e.jsonschema.ValidationError):
                            e.jsonschema.validate(duplicate, schema)
                    self.assertEqual(e.digest(p), before)
                    self.assertEqual(json.dumps(e.SCHEMAS, sort_keys=True), original)

    def test_generation_constraints_do_not_leak_through_shared_schema_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Repository(Path(tmp) / "projects")
            p = repo.get(repo.create(None, True))
            original = json.dumps(e.SCHEMAS, sort_keys=True)
            response = {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(p["pages"]["P001"])},
                    }
                ]
            }
            with (
                patch("comic_workflow.model.count_messages", return_value=1000),
                patch(
                    "comic_workflow.model.chat_stream", return_value=response
                ) as stream,
            ):
                generate(p, "page", "P001", "", Path(tmp) / "run")
            schema = stream.call_args.args[1]["response_format"]["json_schema"][
                "schema"
            ]
            panel = schema["properties"]["panels"]["items"]["properties"]
            self.assertNotIn("enum", schema["properties"]["id"])
            self.assertNotIn(
                "enum", panel["action"] if "action" in panel else panel["purpose"]
            )
            self.assertNotIn("maxLength", panel["purpose"])
            self.assertEqual(panel["drawing_description"]["maxLength"], 1400)
            self.assertEqual(
                panel["characters"]["items"]["properties"]["character_id"]["enum"],
                [c["id"] for c in p["canon"]["characters"]],
            )
            self.assertEqual(json.dumps(e.SCHEMAS, sort_keys=True), original)
            for key, value in p["canon"]["initial_state"].items():
                self.assertEqual(
                    schema["properties"]["state_in"]["properties"][key]["const"], value
                )
                self.assertNotIn(
                    "const", schema["properties"]["state_out"]["properties"][key]
                )

    def test_unique_global_lettering_numbers_repaired_without_rewriting(self):
        payload = {
            "panels": [{"id": "P001-K01", "action": "unchanged"}],
            "lettering": [
                {
                    "id": "P001-K01-T03",
                    "panel_id": "P001-K01",
                    "order": 3,
                    "text": "original words",
                },
                {
                    "id": "P001-K02-T04",
                    "panel_id": "P001-K02",
                    "order": 4,
                    "text": "next panel",
                },
            ],
        }
        before = copy.deepcopy(payload)
        fixed, changes = repair_lettering_numbering(payload)
        self.assertEqual(payload, before)
        self.assertEqual(fixed["panels"], before["panels"])
        self.assertEqual([t["order"] for t in fixed["lettering"]], [1, 1])
        self.assertEqual(
            [t["text"] for t in fixed["lettering"]],
            [t["text"] for t in before["lettering"]],
        )
        self.assertEqual(len(changes), 2)
        ambiguous = copy.deepcopy(payload)
        ambiguous["lettering"].append(copy.deepcopy(ambiguous["lettering"][0]))
        with self.assertRaisesRegex(ValueError, "不能自动猜测"):
            repair_lettering_numbering(ambiguous)

    def test_generated_meta_filler_is_rejected_before_acceptance(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Repository(tmp)
            p = repo.get(repo.create(None, True))
            page = copy.deepcopy(p["pages"]["P001"])
            page["panels"][0]["drawing_description"] += (
                "最终输出仅为纯JSON，保证承诺担保"
            )
            with self.assertRaisesRegex(ValueError, "重复校验废话"):
                e.merged(p, "page", "P001", page)

    def test_review_retry_keeps_candidate_payload_and_confirmed_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = Repository(root / "projects")
            pid = repo.create(None, True)
            p = repo.get(pid)
            candidate = repo.propose(
                pid,
                "page",
                "P002",
                p["pages"]["P002"],
                e.digest(p),
                metadata={"continuity_review": {"verdict": "unavailable"}},
            )
            audit = {
                "verdict": "pass",
                "issues": [],
                "unknown_canon": [],
                "reviewed_scope": "P002",
            }
            with patch("comic_workflow.jobs.DATA", root):
                app = Application(repo)
            callbacks = []
            with patch.object(
                app.jobs,
                "submit",
                side_effect=lambda label, fn, pid: callbacks.append(fn) or "job",
            ):
                app.action(
                    {
                        "action": "audit_candidate",
                        "project": pid,
                        "candidate": candidate["id"],
                        "base_hash": e.digest(p),
                    }
                )
            with (
                patch.object(app, "prepare_language"),
                patch("comic_workflow.app_core.generate", return_value=(audit, {})),
            ):
                outcome = callbacks[0](lambda x: None, "test")
            revised = next(
                c for c in repo.candidates(pid) if c["id"] == outcome["candidate"]
            )
            self.assertEqual(revised["payload"], candidate["payload"])
            self.assertEqual(revised["metadata"]["continuity_review"], audit)
            self.assertEqual(repo.get(pid), p)
            original = e.read(
                repo.folder(pid) / "candidates" / (candidate["id"] + ".json")
            )
            self.assertEqual(original, candidate)
            with self.assertRaisesRegex(ValueError, "作品已变化"):
                app.action(
                    {
                        "action": "audit_candidate",
                        "project": pid,
                        "candidate": candidate["id"],
                        "base_hash": "stale",
                    }
                )

    def test_retry_drops_failed_draft_without_dropping_original_requirements(self):
        original = [
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "full original requirements"},
        ]
        messages = original + [
            {"role": "assistant", "content": "large invalid draft"},
            {"role": "user", "content": "fix bubble numbering"},
        ]
        before = copy.deepcopy(messages)
        updates = []
        with patch("comic_workflow.model.count_messages", side_effect=[11523, 7500]):
            fitted, count = budget_messages(
                messages,
                original,
                {"context_size": 16384, "max_output_tokens": 6000},
                updates.append,
            )
        self.assertEqual(fitted, original + [messages[-1]])
        self.assertEqual(messages, before)
        self.assertEqual(count, 7500)
        self.assertTrue(updates)

    def test_initial_overflow_is_not_silently_truncated(self):
        original = [{"role": "user", "content": "complete source"}]
        with patch("comic_workflow.model.count_messages", return_value=15000):
            with self.assertRaisesRegex(ValueError, "未截断"):
                budget_messages(
                    original,
                    original,
                    {"context_size": 16384, "max_output_tokens": 6000},
                    lambda x: None,
                )

    def test_retry_that_fits_preserves_draft(self):
        original = [{"role": "user", "content": "source"}]
        messages = original + [
            {"role": "assistant", "content": "draft"},
            {"role": "user", "content": "correction"},
        ]
        with patch("comic_workflow.model.count_messages", return_value=9000):
            fitted, _ = budget_messages(
                messages,
                original,
                {"context_size": 16384, "max_output_tokens": 6000},
                lambda x: None,
            )
        self.assertEqual(fitted, messages)

    def test_audit_includes_target_once_and_preserves_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Repository(Path(tmp))
            project = repo.get(repo.create(None, True))
            before = e.digest(project)
            context = build_context(project, "audit", "P001", "")
            self.assertNotIn("current_page", context)
            self.assertEqual(context["pages"]["P001"], project["pages"]["P001"])
            self.assertEqual(e.digest(project), before)
