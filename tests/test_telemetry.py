import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from comic_workflow.telemetry import chat_stream, ComfyProgress
from comic_workflow.downloader import filehash


class TelemetryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        patched = patch("comic_workflow.telemetry.DATA", self.root)
        patched.start()
        self.addCleanup(patched.stop)

    def stream(self, chunks, done=True):
        raw = b": ping\n\n" + b"".join(
            ("data: " + json.dumps(x, ensure_ascii=False) + "\n\n").encode()
            for x in chunks
        )
        if done:
            raw += b"data: [DONE]\n\n"
        return io.BytesIO(raw)

    def test_real_token_counts_prefill_eta_and_unicode_reassembly(self):
        chunks = [
            {
                "prompt_progress": {
                    "processed": 100,
                    "total": 300,
                    "cache": 0,
                    "time_ms": 1000,
                },
                "choices": [],
            },
            {
                "timings": {"predicted_n": 3, "predicted_per_second": 5},
                "choices": [
                    {"delta": {"content": '{"故事":"雨后'}, "finish_reason": None}
                ],
            },
            {
                "timings": {"predicted_n": 6, "predicted_per_second": 5},
                "choices": [
                    {"delta": {"content": '的纸船"}'}, "finish_reason": "stop"}
                ],
            },
            {"usage": {"completion_tokens": 6, "prompt_tokens": 300}, "choices": []},
        ]
        updates = []
        with patch(
            "comic_workflow.telemetry.urllib.request.build_opener",
            return_value=Mock(open=Mock(return_value=self.stream(chunks))),
        ):
            result = chat_stream(
                "http://localhost/chat",
                {"max_tokens": 100},
                10,
                updates.append,
                self.root,
            )
        self.assertEqual(
            json.loads(result["choices"][0]["message"]["content"]),
            {"故事": "雨后的纸船"},
        )
        prefill = next(
            x["runtime"] for x in updates if x["runtime"].get("phase") == "处理输入"
        )
        self.assertEqual(prefill["remaining_seconds"], 2)
        self.assertEqual(updates[-1]["runtime"]["output_tokens"], 6)
        self.assertNotIn(
            "total", updates[-1]["runtime"]
        )  # Output cap is not a percentage.

    def test_broken_stream_not_accepted_as_complete(self):
        data = [{"choices": [{"delta": {"content": "partial"}, "finish_reason": None}]}]
        with (
            patch(
                "comic_workflow.telemetry.urllib.request.build_opener",
                return_value=Mock(
                    open=Mock(return_value=self.stream(data, done=False))
                ),
            ),
            self.assertRaisesRegex(ValueError, "断开"),
        ):
            chat_stream("http://localhost/chat", {}, 10, lambda x: None)

    def test_truncated_finish_reason_retained_for_callers_validation(self):
        data = [
            {"choices": [{"delta": {"content": "partial"}, "finish_reason": "length"}]}
        ]
        with patch(
            "comic_workflow.telemetry.urllib.request.build_opener",
            return_value=Mock(open=Mock(return_value=self.stream(data))),
        ):
            result = chat_stream("http://localhost/chat", {}, 10, lambda x: None)
        self.assertEqual(result["choices"][0]["finish_reason"], "length")

    def test_comfy_ignores_other_prompts_and_reports_real_step_counts(self):
        ws = Mock()
        ws.recv.side_effect = [
            json.dumps(
                {
                    "type": "progress",
                    "data": {"prompt_id": "other", "value": 99, "max": 100},
                }
            ),
            json.dumps(
                {"type": "executing", "data": {"prompt_id": "ours", "node": "5"}}
            ),
            json.dumps(
                {
                    "type": "progress",
                    "data": {"prompt_id": "ours", "node": "5", "value": 18, "max": 28},
                }
            ),
            json.dumps(
                {
                    "type": "progress",
                    "data": {"prompt_id": "ours", "node": "5", "value": 19, "max": 28},
                }
            ),
        ]
        with patch(
            "comic_workflow.telemetry.websocket.create_connection", return_value=ws
        ):
            monitor = ComfyProgress("http://127.0.0.1:8188", "client")
        graph = {"5": {"class_type": "KSampler"}}
        self.assertIsNone(monitor.poll("ours", graph))
        self.assertEqual(monitor.poll("ours", graph)["phase"], "采样绘图")
        with patch("comic_workflow.telemetry.time.monotonic", return_value=100):
            result = monitor.poll("ours", graph)
        self.assertEqual((result["completed"], result["total"]), (18, 28))
        self.assertNotIn("remaining_seconds", result)
        with patch("comic_workflow.telemetry.time.monotonic", return_value=101):
            result = monitor.poll("ours", graph)
        self.assertEqual(result["remaining_seconds"], 9)
        monitor.close()
        ws.close.assert_called_once()

    def test_disconnected_comfy_stream_is_explicit_not_fake_progress(self):
        with patch(
            "comic_workflow.telemetry.websocket.create_connection",
            side_effect=ConnectionError("test"),
        ):
            monitor = ComfyProgress("http://127.0.0.1:8188", "client")
        result = monitor.poll("id", {})
        self.assertIn("不可用", result["phase"])
        self.assertNotIn("completed", result)

    def test_hash_progress_counts_bytes_actually_read(self):
        import hashlib

        path = self.root / "file"
        path.write_bytes(b"test" * 100)
        values = []
        self.assertEqual(
            filehash(path, lambda n, total: values.append((n, total))),
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        self.assertEqual(values[-1], (400, 400))
