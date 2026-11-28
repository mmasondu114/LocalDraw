"""Offline unit & PySide6 widget tests for the pure native LocalDraw desktop app."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from comic_workflow import comfyui, drawing_models
from comic_workflow.app_core import StudioApp, UIStateStore
from comic_workflow.paths import ROOT
from comic_workflow.repository import Repository


class NativeArchitectureTests(unittest.TestCase):
    def test_zero_web_and_http_layers(self):
        self.assertFalse((ROOT / "web").exists(), "Legacy web/ directory must be removed")
        self.assertFalse((ROOT / "src/comic_workflow/server.py").exists(), "Legacy HTTP server.py must be removed")
        self.assertFalse((ROOT / "src/comic_workflow/startup.py").exists(), "Legacy HTTP startup.py must be removed")
        self.assertFalse((ROOT / "runtime/desktop").exists(), "Legacy WebView2 runtime/desktop must be removed")
        self.assertTrue((ROOT / "LocalDraw.exe").is_file(), "Pure native LocalDraw.exe must exist")
        self.assertTrue((ROOT / "src/comic_workflow/app_core.py").is_file())
        self.assertTrue((ROOT / "src/comic_workflow/qt_app/main_window.py").is_file())

    def test_ui_state_store_persists_theme_weights_and_drafts(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "app-state.json"
            store = UIStateStore(path)
            store.set("theme", "sage")
            store.remember_lora_weight("krea2\\明日方舟\\Krea2_可露希尔_mmasondu_1200步.safetensors", 0.95)
            store.save_prompt_draft("new", {"text": "closure arknights", "count": 2})

            reloaded = UIStateStore(path)
            self.assertEqual(reloaded.get("theme"), "sage")
            self.assertEqual(
                reloaded.lora_weights().get("krea2/明日方舟/Krea2_可露希尔_mmasondu_1200步.safetensors"),
                0.95,
            )
            self.assertEqual(reloaded.prompt_draft("new")["text"], "closure arknights")

    def test_comfy_keep_alive_keeps_service_running_until_shutdown(self):
        proc = SimpleNamespace(poll=lambda: None, preloaded_checkpoint=None)
        with tempfile.TemporaryDirectory() as tmp, patch.object(comfyui, "terminate_tree") as term:
            mgr = comfyui.ComfyManager(keep_alive=True, session_dir=tmp)
            mgr.process = proc
            self.assertTrue(mgr.is_running())
            mgr.stop()
            term.assert_not_called()
            self.assertIs(mgr.process, proc)

            mgr.shutdown()
            term.assert_called_once_with(proc)
            self.assertIsNone(mgr.process)
            self.assertFalse(mgr.is_running())

    def test_comfy_without_keep_alive_stops_on_finish(self):
        proc = SimpleNamespace(poll=lambda: None, preloaded_checkpoint=None)
        with tempfile.TemporaryDirectory() as tmp, patch.object(comfyui, "terminate_tree") as term:
            mgr = comfyui.ComfyManager(keep_alive=False, session_dir=tmp)
            mgr.process = proc
            mgr.stop()
            term.assert_called_once_with(proc)
            self.assertIsNone(mgr.process)

    def test_studio_app_direct_queries_and_keep_alive_toggle(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Repository(Path(tmp) / "projects")
            app = StudioApp(repo=repo, keep_alive_comfy=True)
            try:
                boot = app.bootstrap()
                self.assertIn("projects", boot)
                self.assertIn("jobs", boot)
                st = app.status()
                self.assertTrue(st["drawing_model"]["keep_alive"])
                self.assertTrue(app.comfy.keep_alive)

                res = app.action({"action": "set_comfy_keep_alive", "enabled": False})
                self.assertFalse(res["keep_alive"])
                self.assertFalse(app.comfy.keep_alive)

                res = app.action({"action": "set_comfy_keep_alive", "enabled": True})
                self.assertTrue(res["keep_alive"])
                self.assertTrue(app.comfy.keep_alive)

                cat = app.wardrobe_catalog()
                self.assertGreater(len(cat.get("characters", [])), 0)
                preview = app.action({
                    "action": "wardrobe_preview",
                    "character": "amiya_(arknights)",
                    "scope": "character",
                    "level": "sfw",
                    "style": "学院",
                    "seed": 8,
                })
                self.assertIn("rabbit ears", preview.get("text", ""))
            finally:
                app.shutdown()


class NativePySide6UITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        cls.qt_app = QApplication.instance() or QApplication(["localdraw-test"])

    @classmethod
    def tearDownClass(cls):
        import gc

        app = getattr(cls, "qt_app", None)
        if app is not None:
            for w in list(app.topLevelWidgets()):
                try:
                    w.close()
                    w.deleteLater()
                except RuntimeError:
                    pass
            app.sendPostedEvents()
            app.processEvents()
        gc.collect()

    def _make_app(self, tmp_dir: str) -> StudioApp:
        repo = Repository(Path(tmp_dir) / "projects")
        return StudioApp(repo=repo, keep_alive_comfy=True)

    def test_main_window_home_prompt_lora_search_and_second_pass(self):
        from comic_workflow.qt_app.main_window import StudioMainWindow
        from comic_workflow.qt_app.theme import apply_theme

        with tempfile.TemporaryDirectory() as tmp:
            app = self._make_app(tmp)
            fake_opts = {
                "comfy": {
                    "checkpoints": [
                        "krea2_turbo_bf16.safetensors",
                        "moodyKrea2Mix_v3_Global_BF16_abilirated.safetensors",
                        "moss_image_sft_bf16.safetensors",
                        "2.9DeMixV7.5.safetensors",
                    ],
                    "loras": [
                        "krea2/明日方舟/Krea2_可露希尔_mmasondu_1200步.safetensors",
                        "krea2/画风/Detailer-KREA2.safetensors",
                        "krea2/画风/Krea2Rella_c1-st8000.safetensors",
                        "krea2/画风/v67Lighting_v1.safetensors",
                    ],
                    "default_checkpoint": "krea2_turbo_bf16.safetensors",
                },
                "diffusion_models": {
                    "krea2_turbo_bf16.safetensors": {"label": "Krea2 Turbo BF16", "family": "krea2", "steps": 6, "sampler_name": "er_sde", "scheduler": "beta"},
                    "moodyKrea2Mix_v3_Global_BF16_abilirated.safetensors": {"label": "MoodyKrea2 v3", "family": "krea2", "steps": 6, "sampler_name": "er_sde", "scheduler": "simple"},
                    "moss_image_sft_bf16.safetensors": {"label": "MOSS Image SFT", "family": "moss_image", "steps": 20, "sampler_name": "er_sde", "scheduler": "simple"},
                    "2.9DeMixV7.5.safetensors": {"label": "Anima 2.9 DeMix V7.5", "family": "anima", "steps": 28, "sampler_name": "euler_ancestral", "scheduler": "normal"},
                },
                "lora_inspection": {
                    "krea2/明日方舟/Krea2_可露希尔_mmasondu_1200步.safetensors": {"family": "Krea2", "supported": True},
                    "krea2/画风/Detailer-KREA2.safetensors": {"family": "Krea2", "supported": True},
                    "krea2/画风/Krea2Rella_c1-st8000.safetensors": {"family": "Krea2", "supported": True},
                    "krea2/画风/v67Lighting_v1.safetensors": {"family": "Krea2", "supported": True},
                },
                "lora_labels": {
                    "krea2/明日方舟/Krea2_可露希尔_mmasondu_1200步.safetensors": {
                        "trigger_words": "closure_ark",
                        "character_id": "char-closure",
                    }
                },
                "upscale_models": ["4x-UltraSharp.pth", "RealESRGAN_x4plus_anime_6B.pth"],
                "defaults": {"width": 1024, "height": 1024, "steps": 6, "delta_matte_skin": False, "consistency_lora": False},
            }

            try:
                apply_theme(self.qt_app, "linen", "standard")
                with patch.object(app, "drawing_options", return_value=fake_opts):
                    win = StudioMainWindow(app, smoke=True)
                    self.assertEqual(win._root_stack.currentIndex(), 0)

                    # Switch to standalone Prompt Studio
                    win.open_standalone_prompt()
                    self.assertEqual(win._root_stack.currentIndex(), 1)
                    page = win._prompt_page
                    page.load_session(None, board=False)
                    # Select Krea2 checkpoint
                    idx = page.model_combo.findData("krea2_turbo_bf16.safetensors")
                    self.assertGreaterEqual(idx, 0)
                    page.model_combo.setCurrentIndex(idx)

                    # Search Closure LoRA by Chinese & English alias and select it
                    page.positive.setPlainText("portrait, studio light")
                    page._lora_search.setText("可露希尔")
                    self.assertGreaterEqual(page._lora_results.count(), 1)
                    page._on_pick_lora(page._lora_results.item(0))

                    lora_file = "krea2/明日方舟/Krea2_可露希尔_mmasondu_1200步.safetensors"
                    self.assertIn(lora_file, page._selected_loras)
                    # Trigger word stays out of editable positive box and appears in final prompt preview
                    self.assertEqual(page.positive.toPlainText(), "portrait, studio light")
                    self.assertEqual(page._final_preview.toPlainText(), "closure_ark, portrait, studio light")
                    self.assertIn("1 个触发词", page._preview_toggle.text())

                    # Adjust weight and confirm UIStateStore persists it
                    page._selected_loras[lora_file].weight.setValue(0.85)
                    self.assertEqual(app.ui_state.lora_weights().get(lora_file), 0.85)

                    # Verify draw action sends clean text + LoRA trigger_words even with zero characters selected
                    captured = {}
                    def fake_action(payload):
                        captured.update(payload)
                        return {"project": "tmp-test", "job": "job-1"}
                    with patch.object(app, "action", side_effect=fake_action):
                        page._start_draw(queued=False)
                    self.assertEqual(captured.get("action"), "draw_quick_sample")
                    self.assertEqual(captured.get("text"), "portrait, studio light")
                    self.assertEqual(captured.get("characters"), [])
                    self.assertEqual(len(captured.get("loras", [])), 1)
                    self.assertEqual(captured["loras"][0]["file"], lora_file)
                    self.assertEqual(captured["loras"][0]["weight"], 0.85)
                    self.assertEqual(captured["loras"][0]["trigger_words"], "closure_ark")

                    # Test Second Pass secondary panel + Krea2/MOSS/Anima29 presets + Escape exit
                    dummy_img = Path(tmp) / "base.png"
                    from PIL import Image
                    Image.new("RGB", (1536, 864), (120, 100, 90)).save(dummy_img)
                    base_rec = {
                        "id": "img-1",
                        "pid": "tmp-test",
                        "path": dummy_img,
                        "positive": "closure_ark, portrait",
                        "user_positive": "portrait",
                        "negative": "blurry",
                        "batch": {"checkpoint": "krea2_turbo_bf16.safetensors", "loras": [{"file": lora_file, "weight": 0.85}]},
                    }
                    page._gallery_entries = [{
                        "base": base_rec,
                        "variants": [],
                        "applied_id": None,
                        "active": base_rec,
                        "active_id": "img-1",
                        "path": dummy_img,
                    }]
                    page._active_gallery_idx = 0
                    page._enter_second_pass()
                    self.assertEqual(page._left_stack.currentIndex(), 1)
                    self.assertIn(lora_file, page._sp_selected_loras)
                    self.assertEqual(page._sp_pos.toPlainText(), "portrait")
                    self.assertEqual(page._sp_final_preview.toPlainText(), "closure_ark, portrait")

                    # Apply Krea2 preset on 1536x864 (1.5K tier): keeps base Krea2 model (never forces abliterated), 1.25x upscale, euler/simple
                    page._apply_sp_preset("krea2")
                    self.assertEqual(page._sp_model.currentData(), "krea2_turbo_bf16.safetensors")
                    self.assertEqual(page._sp_method.currentData(), "upscale")
                    self.assertEqual(page._sp_scale.currentData(), 1.25)
                    self.assertEqual(page._sp_sampler.currentData(), "euler")
                    self.assertEqual(page._sp_scheduler.currentData(), "simple")
                    self.assertEqual(page._sp_neg.toPlainText(), "")

                    # Apply MOSS preset -> cross-model warning & prompt box auto-expanded
                    page._apply_sp_preset("moss")
                    self.assertEqual(page._sp_model.currentData(), "moss_image_sft_bf16.safetensors")
                    self.assertEqual(page._sp_scale.currentData(), 1.25)
                    self.assertTrue(page._sp_prompt_toggle.isChecked())

                    # Apply Anima 2.9B preset on 1536x864 (1.5K tier) -> 1.25x native without over-upscaling
                    page._apply_sp_preset("anima29")
                    self.assertEqual(page._sp_model.currentData(), "2.9DeMixV7.5.safetensors")
                    self.assertEqual(page._sp_scale.currentData(), 1.25)
                    self.assertEqual(page._sp_post_up.currentData(), 1.0)

                    # Exit Second Pass via Escape handler
                    page._on_escape()
                    self.assertEqual(page._left_stack.currentIndex(), 0)
                    self.assertIsNone(page._sp_state)

                    # Switch theme to Sage & back to Warm Linen
                    apply_theme(self.qt_app, "sage", "large")
                    apply_theme(self.qt_app, "linen", "standard")

                    win.close()
            finally:
                app.shutdown()

    def test_lora_uncheck_removes_auto_inserted_trigger_words(self):
        from comic_workflow.qt_app.main_window import StudioMainWindow
        from comic_workflow.qt_app.widgets import (
            apply_lora_triggers,
            insert_trigger_phrase,
            remove_trigger_phrase,
        )

        # Pure helper edge cases
        p, inserted = insert_trigger_phrase("dark room, soft light", "Closure from Arknights")
        self.assertTrue(inserted)
        self.assertEqual(p, "Closure from Arknights, dark room, soft light")
        self.assertEqual(remove_trigger_phrase(p, "Closure from Arknights"), "dark room, soft light")
        self.assertEqual(remove_trigger_phrase("masterpiece, Closure from Arknights, dark room", "Closure from Arknights"), "masterpiece, dark room")
        self.assertEqual(remove_trigger_phrase("Closure from Arknights", "Closure from Arknights"), "")
        # Should not insert duplicate or remove substring inside another word
        p2, ins2 = insert_trigger_phrase("Closure from Arknights, portrait", "Closure from Arknights")
        self.assertFalse(ins2)
        self.assertEqual(p2, "Closure from Arknights, portrait")
        # apply_lora_triggers deduplicates case-insensitively and skips triggers already in prompt
        self.assertEqual(
            apply_lora_triggers(
                "dark room",
                [{"trigger_words": "Closure"}, {"trigger_words": "closure"}, {"trigger_words": ""}, {"trigger_words": "ink_style"}],
            ),
            "Closure, ink_style, dark room",
        )
        self.assertEqual(
            apply_lora_triggers("Closure standing in dark room", [{"trigger_words": "Closure"}, {"trigger_words": "ink_style"}]),
            "ink_style, Closure standing in dark room",
        )

        with tempfile.TemporaryDirectory() as tmp:
            app = self._make_app(tmp)
            lora_a = "krea2/明日方舟/Krea2_可露希尔_1200步.safetensors"
            lora_b = "krea2/明日方舟/Krea2_可露希尔_1000步.safetensors"
            fake_opts = {
                "comfy": {
                    "checkpoints": ["krea2_turbo_bf16.safetensors"],
                    "loras": [lora_a, lora_b],
                    "default_checkpoint": "krea2_turbo_bf16.safetensors",
                },
                "diffusion_models": {
                    "krea2_turbo_bf16.safetensors": {"label": "Krea2 Turbo BF16", "family": "krea2", "steps": 6},
                },
                "lora_inspection": {
                    lora_a: {"family": "Krea2", "supported": True},
                    lora_b: {"family": "Krea2", "supported": True},
                },
                "lora_labels": {
                    lora_a: {"trigger_words": "Closure from Arknights"},
                    lora_b: {"trigger_words": "Closure from Arknights"},
                },
                "upscale_models": [],
                "defaults": {"width": 1024, "height": 1024},
            }
            try:
                with patch.object(app, "drawing_options", return_value=fake_opts):
                    win = StudioMainWindow(app, smoke=True)
                    win.open_standalone_prompt()
                    page = win._prompt_page
                    page.load_session(None, board=False)
                    page.positive.setPlainText("sunlit workshop, warm light")

                    # 1. Check LoRA A -> editable box stays clean; trigger appears in final prompt preview
                    page._add_selected_lora(lora_a)
                    self.assertEqual(page.positive.toPlainText(), "sunlit workshop, warm light")
                    self.assertEqual(page._final_preview.toPlainText(), "Closure from Arknights, sunlit workshop, warm light")

                    # 2. Uncheck LoRA A -> trigger removed from final preview; editable box untouched
                    page._selected_loras[lora_a].check.setChecked(False)
                    self.assertNotIn(lora_a, page._selected_loras)
                    self.assertEqual(page.positive.toPlainText(), "sunlit workshop, warm light")
                    self.assertEqual(page._final_preview.toPlainText(), "sunlit workshop, warm light")

                    # 3. If user manually typed the trigger, final preview never duplicates it & unchecking never erases it
                    page.positive.setPlainText("Closure from Arknights, custom manual prompt")
                    page._add_selected_lora(lora_a)
                    self.assertEqual(page._final_preview.toPlainText(), "Closure from Arknights, custom manual prompt")
                    page._selected_loras[lora_a].check.setChecked(False)
                    self.assertEqual(page.positive.toPlainText(), "Closure from Arknights, custom manual prompt")

                    # 4. Shared trigger across two LoRAs: single trigger in final preview, no bleed warn, clean removal
                    page.positive.setPlainText("night corridor")
                    page._add_selected_lora(lora_a)
                    page._add_selected_lora(lora_b)
                    self.assertEqual(page.positive.toPlainText(), "night corridor")
                    self.assertEqual(page._final_preview.toPlainText(), "Closure from Arknights, night corridor")
                    self.assertTrue(page._lora_bleed_warn.isHidden())
                    page._selected_loras[lora_a].check.setChecked(False)
                    self.assertEqual(page._final_preview.toPlainText(), "Closure from Arknights, night corridor")
                    page._selected_loras[lora_b].check.setChecked(False)
                    self.assertEqual(page._final_preview.toPlainText(), "night corridor")
                    win.close()
            finally:
                app.shutdown()

    def test_zero_emojis_or_dingbats_in_qt_ui_code(self):
        import re

        qt_dir = ROOT / "src/comic_workflow/qt_app"
        forbidden = re.compile(r"[⌂⏱✦▦⇩▤☺☰⚠▸▾←→‹›✓○＋🎨🚀⚙✨🔥💡]")
        hits = []
        for py in sorted(qt_dir.glob("*.py")):
            for lineno, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
                if forbidden.search(line):
                    hits.append(f"{py.name}:{lineno}: {line.strip()}")
        self.assertEqual(hits, [], f"Found forbidden emoji/dingbat symbols:\n" + "\n".join(hits))

    def test_custom_warm_terracotta_icon_and_win_taskbar_identity(self):
        from PIL import Image
        from comic_workflow.qt_app.win_shell import APP_USER_MODEL_ID, set_process_app_id

        ico = ROOT / "scripts/windows/LocalDraw.ico"
        self.assertTrue(ico.is_file())
        img = Image.open(ico)
        self.assertGreaterEqual(len(img.info.get("sizes") or []), 5)
        # Verify center-top background is warm terracotta (R > G > B), not old green
        rgb = img.convert("RGBA").resize((64, 64)).getpixel((12, 12))
        self.assertGreater(rgb[0], rgb[1])
        self.assertGreater(rgb[1], rgb[2])
        if os.name == "nt":
            self.assertTrue(set_process_app_id(APP_USER_MODEL_ID))

    def test_preferred_checkpoint_persistence_and_inline_preload_controls(self):
        from comic_workflow.qt_app.dialogs import DrawingModelDialog
        from comic_workflow.qt_app.main_window import StudioMainWindow

        with tempfile.TemporaryDirectory() as tmp:
            app = self._make_app(tmp)
            fake_models = [
                {"value": "Anima-3.8B-v1.1.safetensors", "label": "Anima 3.8B"},
                {"value": "krea2_turbo_bf16.safetensors", "label": "Krea2 Turbo · BF16 高精度"},
                {"value": "moss_image_sft_bf16.safetensors", "label": "MOSS Image SFT"},
            ]
            fake_opts = {
                "comfy": {
                    "checkpoints": [m["value"] for m in fake_models],
                    "loras": [],
                    "configured": "Anima-3.8B-v1.1.safetensors",
                },
                "preloaded_checkpoint": None,
                "preferred_checkpoint": "krea2_turbo_bf16.safetensors",
                "diffusion_models": {
                    "Anima-3.8B-v1.1.safetensors": {"label": "Anima 3.8B", "family": "anima38", "steps": 30},
                    "krea2_turbo_bf16.safetensors": {"label": "Krea2 Turbo · BF16 高精度", "family": "krea2", "steps": 8},
                    "moss_image_sft_bf16.safetensors": {"label": "MOSS Image SFT", "family": "moss_image", "steps": 25},
                },
                "lora_inspection": {},
                "lora_labels": {},
                "upscale_models": [],
                "defaults": {"width": 1024, "height": 1024},
            }
            try:
                # 1. Remembered last_checkpoint overrides configured Anima 3.8B
                app.remember_checkpoint("krea2_turbo_bf16.safetensors")
                self.assertEqual(
                    app.preferred_checkpoint([m["value"] for m in fake_models]),
                    "krea2_turbo_bf16.safetensors",
                )
                # Reloading a fresh StudioApp reads persisted last_checkpoint from app-state.json
                app2 = self._make_app(tmp)
                try:
                    self.assertEqual(
                        app2.preferred_checkpoint([m["value"] for m in fake_models]),
                        "krea2_turbo_bf16.safetensors",
                    )
                finally:
                    app2.shutdown()

                # 2. DrawingModelDialog defaults to remembered model (NOT index 0 Anima-3.8B)
                fake_snap = {
                    "models": fake_models,
                    "loaded": None,
                    "comfy_running": False,
                    "keep_alive": True,
                    "default": "Anima-3.8B-v1.1.safetensors",
                    "preferred": "krea2_turbo_bf16.safetensors",
                    "preferred_label": "Krea2 Turbo · BF16 高精度",
                    "busy": None,
                }
                with patch.object(app, "drawing_model_snapshot", return_value=fake_snap), \
                        patch.object(app, "drawing_options", return_value=fake_opts):
                    dlg = DrawingModelDialog(app)
                    self.assertEqual(dlg._combo.currentData(), "krea2_turbo_bf16.safetensors")
                    dlg.close()

                    # 3. Header + PromptStudio inline preload/unload buttons track model state & remember switches
                    win = StudioMainWindow(app, smoke=True)
                    win.open_standalone_prompt()
                    page = win._prompt_page
                    self.assertEqual(page._current_checkpoint(), "krea2_turbo_bf16.safetensors")
                    self.assertFalse(win._unload_draw_btn.isVisible())
                    self.assertEqual(page._inline_preload_btn.text(), "预加载")

                    # Switch to MOSS in PromptStudio -> remembered globally immediately
                    moss_idx = page.model_combo.findData("moss_image_sft_bf16.safetensors")
                    page.model_combo.setCurrentIndex(moss_idx)
                    self.assertEqual(app.ui_state.get("last_checkpoint"), "moss_image_sft_bf16.safetensors")

                    # Simulate MOSS loaded -> inline button flips to 卸载 & header shows 卸载显存
                    page.update_drawing_model_state({"loaded": "moss_image_sft_bf16.safetensors", "comfy_running": True}, busy=False)
                    self.assertEqual(page._inline_preload_btn.text(), "卸载")

                    # Clicking inline preload/unload dispatches unload_drawing_model
                    with patch.object(app, "action", return_value={"job": "j-unload"}) as act:
                        page._toggle_inline_preload()
                        act.assert_called_once_with({"action": "unload_drawing_model"})

                    win.close()
            finally:
                app.shutdown()

    def test_lora_auto_trigger_boundaries_fallbacks_paste_and_second_pass(self):
        import json
        import struct
        from PySide6.QtGui import QGuiApplication
        from PIL import Image
        from comic_workflow.lora_inspection import (
            extract_trigger_from_safetensors,
            insert_trigger_phrase,
            remove_trigger_phrase,
            resolve_lora_trigger,
            trigger_present_in_prompt,
        )
        from comic_workflow.qt_app.main_window import StudioMainWindow

        # 1. Word-boundary matching: short trigger 'ew' must NOT match inside 'view' or 'new'
        self.assertFalse(trigger_present_in_prompt("three-quarter view, new coat", "ew"))
        p_ew, ins = insert_trigger_phrase("three-quarter view, new coat", "ew")
        self.assertTrue(ins)
        self.assertEqual(p_ew, "ew, three-quarter view, new coat")
        self.assertTrue(trigger_present_in_prompt(p_ew, "ew"))
        self.assertEqual(remove_trigger_phrase(p_ew, "ew"), "three-quarter view, new coat")

        # 2. 3-tier fallback: trigger_words -> appearance_prompt -> safetensors header
        with tempfile.TemporaryDirectory() as tmp:
            lora_root = Path(tmp) / "loras"
            st_path = lora_root / "krea2" / "meta_only.safetensors"
            st_path.parent.mkdir(parents=True, exist_ok=True)
            hdr = json.dumps({
                "__metadata__": {
                    "ss_tag_frequency": json.dumps({"1_VSN_cos": {"VSN_cos": 5}}),
                }
            }).encode("utf-8")
            st_path.write_bytes(struct.pack("<Q", len(hdr)) + hdr + b"\x00" * 16)
            self.assertEqual(extract_trigger_from_safetensors(st_path), "VSN_cos")

            self.assertEqual(
                resolve_lora_trigger(
                    "krea2/app_only.safetensors",
                    {"krea2/app_only.safetensors": {"trigger_words": "", "appearance_prompt": "amiya_ark, rabbit ears"}},
                    allow_safetensors=False,
                ),
                "amiya_ark, rabbit ears",
            )
            self.assertEqual(
                resolve_lora_trigger(
                    "krea2/meta_only.safetensors",
                    {"krea2/meta_only.safetensors": {"trigger_words": ""}},
                    allow_safetensors=True,
                    lora_root=lora_root,
                ),
                "VSN_cos",
            )

            # 3. UI flow: paste preserves selected LoRA triggers, first-time edit auto-inserts, Second Pass auto-inserts/removes
            app = self._make_app(tmp)
            lora_short = "krea2/明日方舟/角色/维什戴尔_版本3272296.safetensors"
            lora_empty = "krea2/画风/BlankStyle.safetensors"
            lora_sp = "krea2/明日方舟/角色/Krea2_可露希尔_mmasondu_1200步.safetensors"
            fake_opts = {
                "comfy": {
                    "checkpoints": ["krea2_turbo_bf16.safetensors"],
                    "loras": [lora_short, lora_empty, lora_sp],
                    "default_checkpoint": "krea2_turbo_bf16.safetensors",
                },
                "diffusion_models": {
                    "krea2_turbo_bf16.safetensors": {"label": "Krea2 Turbo BF16", "family": "krea2", "steps": 8},
                },
                "lora_inspection": {
                    lora_short: {"family": "Krea2", "supported": True},
                    lora_empty: {"family": "Krea2", "supported": True},
                    lora_sp: {"family": "Krea2", "supported": True},
                },
                "lora_labels": {
                    lora_short: {"trigger_words": "ew"},
                    lora_empty: {"trigger_words": ""},
                    lora_sp: {"trigger_words": "Closure from Arknights"},
                },
                "upscale_models": [],
                "defaults": {"width": 1024, "height": 1024},
            }
            try:
                with patch.object(app, "drawing_options", return_value=fake_opts), \
                        patch.object(comfyui, "lora_labels", return_value=dict(fake_opts["lora_labels"])):
                    win = StudioMainWindow(app, smoke=True)
                    win.open_standalone_prompt()
                    page = win._prompt_page
                    page.load_session(None, board=False)
                    page.positive.setPlainText("three-quarter view, new jacket")

                    # Picking short-trigger LoRA keeps editable box clean and injects 'ew' in final preview even with 'view'/'new'
                    page._add_selected_lora(lora_short)
                    self.assertEqual(page.positive.toPlainText(), "three-quarter view, new jacket")
                    self.assertEqual(page._final_preview.toPlainText(), "ew, three-quarter view, new jacket")
                    self.assertTrue(page._lora_bleed_warn.isHidden())

                    # Pasting brand-new prompt keeps editable box clean while final preview still includes selected LoRA trigger
                    QGuiApplication.clipboard().setText("quiet alley at sunset")
                    page._paste_positive()
                    self.assertEqual(page.positive.toPlainText(), "quiet alley at sunset")
                    self.assertEqual(page._final_preview.toPlainText(), "ew, quiet alley at sunset")

                    # Editing trigger on untagged LoRA updates final preview (not positive) and triggers multi-LoRA bleed hint
                    page._add_selected_lora(lora_empty)
                    with patch.object(app, "action", return_value={}):
                        page._selected_loras[lora_empty].trigger_edit.setText("ink_wash_style")
                        page._selected_loras[lora_empty]._save_meta()
                    self.assertEqual(page.positive.toPlainText(), "quiet alley at sunset")
                    self.assertEqual(page._final_preview.toPlainText(), "ew, ink_wash_style, quiet alley at sunset")
                    self.assertFalse(page._lora_bleed_warn.isHidden())

                    # Manual '插入正文' inserts trigger into editable box without duplicating in final preview & clears bleed warning
                    page._insert_lora_trigger(lora_short, silent=False)
                    self.assertEqual(page.positive.toPlainText(), "ew, quiet alley at sunset")
                    self.assertEqual(page._final_preview.toPlainText(), "ink_wash_style, ew, quiet alley at sunset")
                    self.assertTrue(page._lora_bleed_warn.isHidden())

                    # Second Pass: picking/removing LoRA keeps _sp_pos clean and updates _sp_final_preview
                    dummy_img = Path(tmp) / "sp_base.png"
                    Image.new("RGB", (1024, 1024), (80, 90, 100)).save(dummy_img)
                    base_rec = {
                        "id": "img-sp",
                        "pid": "tmp-test",
                        "path": dummy_img,
                        "positive": "dark workshop",
                        "user_positive": "dark workshop",
                        "negative": "",
                        "batch": {"checkpoint": "krea2_turbo_bf16.safetensors", "loras": []},
                    }
                    page._gallery_entries = [{
                        "base": base_rec, "variants": [], "applied_id": None,
                        "active": base_rec, "active_id": "img-sp", "path": dummy_img,
                    }]
                    page._enter_second_pass()
                    page._sp_lora_search.setText("可露希尔")
                    self.assertGreaterEqual(page._sp_lora_results.count(), 1)
                    page._on_pick_sp_lora(page._sp_lora_results.item(0))
                    self.assertEqual(page._sp_pos.toPlainText(), "dark workshop")
                    self.assertEqual(page._sp_final_preview.toPlainText(), "Closure from Arknights, dark workshop")
                    page._remove_sp_lora(lora_sp)
                    self.assertEqual(page._sp_pos.toPlainText(), "dark workshop")
                    self.assertEqual(page._sp_final_preview.toPlainText(), "dark workshop")
                    win.close()
            finally:
                app.shutdown()

    def test_compact_persistent_job_banner_and_cleaned_gpt_tips(self):
        from comic_workflow.qt_app.main_window import StudioMainWindow

        with tempfile.TemporaryDirectory() as tmp:
            app = self._make_app(tmp)
            try:
                win = StudioMainWindow(app, smoke=True)
                win.open_standalone_prompt()
                page = win._prompt_page

                # Top full-width banner hidden on Prompt Studio; left-column banner stays visible
                self.assertTrue(win._job_banner.isHidden())
                self.assertFalse(page._job_banner.isHidden())

                # Initial idle state
                page.update_jobs([])
                self.assertFalse(page._job_banner.isHidden())
                self.assertEqual(page._job_banner._state_badge.text(), "待命")

                # Active running job
                page.update_jobs([{
                    "id": "j-run",
                    "label": "提示词出图",
                    "status": "running",
                    "message": "绘制第 1/2 张",
                    "progress": {"completed": 1, "total": 2, "unit": "张已处理"},
                }])
                self.assertEqual(page._job_banner._state_badge.text(), "正在进行")
                self.assertEqual(page._job_banner._bar.maximum(), 2)
                self.assertEqual(page._job_banner._bar.value(), 1)

                # Finished idle state: never hides, displays "上一个任务已完成"
                page.update_jobs([{
                    "id": "j-run",
                    "label": "提示词出图",
                    "status": "done",
                    "message": "已完成 2 张",
                }])
                self.assertFalse(page._job_banner.isHidden())
                self.assertEqual(page._job_banner._state_badge.text(), "已完成")
                self.assertIn("上一个任务已完成", page._job_banner._title.text())
                self.assertEqual(page._job_banner._bar.value(), page._job_banner._bar.maximum())
                win.close()
            finally:
                app.shutdown()

        # Static guard ensuring removed GPT tips do not regress
        forbidden_tips = [
            "不调用 Qwen",
            "不经过 Qwen",
            "不自动审核或补画",
            "不另建世界观或剧情方案",
            "提示词含中文，可用 Flash Next",
            "潜空间放大且重绘 < 0.40",
            "分块模式重绘 > 0.35",
        ]
        checked_files = [
            ROOT / "src/comic_workflow/quick_sample.py",
            ROOT / "src/comic_workflow/prompt_exchange.py",
            ROOT / "src/comic_workflow/app_core.py",
            ROOT / "src/comic_workflow/jobs.py",
            ROOT / "src/comic_workflow/qt_app/prompt_page.py",
        ]
        for path in checked_files:
            content = path.read_text(encoding="utf-8")
            for phrase in forbidden_tips:
                self.assertNotIn(phrase, content, f"Removed GPT tip still found in {path.name}: {phrase}")

    def test_krea2_abliterated_encoder_toggle_and_graph_routing(self):
        from comic_workflow import prompt_models
        from comic_workflow.qt_app.main_window import StudioMainWindow

        base_cfg = {"width": 1024, "height": 1024, "steps": 8, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple"}
        with patch.object(prompt_models, "krea2_abliterated_available", return_value=True):
            # Default for krea2_turbo_bf16 is original qwen3vl_4b_bf16 (not abliterated)
            g_off = prompt_models.graph("krea2_turbo_bf16.safetensors", "Closure from Arknights", "", 42, dict(base_cfg))
            self.assertEqual(g_off["10"]["inputs"]["clip_name"], "qwen3vl_4b_bf16.safetensors")
            # Explicit True routes to qwen3vl_4b_abliterated_bf16 when file exists
            g_on = prompt_models.graph(
                "krea2_turbo_bf16.safetensors",
                "Closure from Arknights",
                "",
                42,
                {**base_cfg, "krea2_abliterated_encoder": True},
            )
            if (prompt_models.model_directory(ROOT) / "text_encoders" / prompt_models.KREA2_ABLITERATED_CLIP).is_file():
                self.assertEqual(g_on["10"]["inputs"]["clip_name"], prompt_models.KREA2_ABLITERATED_CLIP)
            # All checkpoints default to False (never auto-enable abliterated encoder)
            self.assertFalse(prompt_models.default_krea2_abliterated("moodyKrea2Mix_v3_Global_BF16_abilirated.safetensors"))
            self.assertFalse(prompt_models.default_krea2_abliterated("krea2_turbo_bf16.safetensors"))

        with tempfile.TemporaryDirectory() as tmp:
            from PIL import Image

            app = self._make_app(tmp)
            fake_opts = {
                "comfy": {
                    "checkpoints": [
                        "krea2_anime_uwu_v2_w8a8.safetensors",
                        "krea2_turbo_bf16.safetensors",
                        "moodyKrea2Mix_v3_Global_BF16_abilirated.safetensors",
                        "moss_image_sft_bf16.safetensors",
                    ],
                    "loras": [],
                    "configured": "krea2_turbo_bf16.safetensors",
                    "krea2_abliterated_available": True,
                },
                "diffusion_models": {
                    "krea2_anime_uwu_v2_w8a8.safetensors": {"label": "Krea2 UWU v2", "family": "krea2", "steps": 8},
                    "krea2_turbo_bf16.safetensors": {"label": "Krea2 Turbo BF16", "family": "krea2", "steps": 8},
                    "moodyKrea2Mix_v3_Global_BF16_abilirated.safetensors": {"label": "Moody Krea2", "family": "krea2", "steps": 8},
                    "moss_image_sft_bf16.safetensors": {"label": "MOSS", "family": "moss_image", "steps": 25},
                },
                "lora_inspection": {},
                "lora_labels": {},
                "upscale_models": ["4x-UltraSharp.pth"],
            }
            try:
                with patch.object(app, "drawing_options", return_value=fake_opts):
                    win = StudioMainWindow(app, smoke=True)
                    win.open_standalone_prompt()
                    page = win._prompt_page
                    page.load_session(None, board=False)
                    # krea2_turbo_bf16 -> toggle visible, default unchecked
                    self.assertFalse(page._krea_box.isHidden())
                    self.assertFalse(page.krea_ablit_chk.isChecked())
                    # Switch to moody abliterated -> stays UNCHECKED by default
                    page.model_combo.setCurrentIndex(page.model_combo.findData("moodyKrea2Mix_v3_Global_BF16_abilirated.safetensors"))
                    self.assertFalse(page.krea_ablit_chk.isChecked())
                    # Switch to MOSS -> hidden
                    page.model_combo.setCurrentIndex(page.model_combo.findData("moss_image_sft_bf16.safetensors"))
                    self.assertTrue(page._krea_box.isHidden())
                    # Switch back to krea2_turbo_bf16, user toggles ON, verify submitted payload carries True
                    page.model_combo.setCurrentIndex(page.model_combo.findData("krea2_turbo_bf16.safetensors"))
                    page.krea_ablit_chk.setChecked(True)
                    page.positive.setPlainText("Closure from Arknights, workshop")
                    captured = {}
                    with patch.object(app, "action", side_effect=lambda req: captured.update(req) or {"project": "tmp-1", "job": "j-1"}):
                        page._start_draw(queued=False)
                    self.assertTrue(captured.get("krea2_abliterated_encoder"))

                    # Second pass strictly mirrors round-1 abliterated state (True -> True, False -> False)
                    # and resolution-adaptive Krea2 preset picks sharp 1.0x + 1.5x post-upscale on 2048x1152
                    p2k = Path(tmp) / "base_2k.png"
                    Image.new("RGB", (2048, 1152), (40, 50, 60)).save(p2k)
                    base_on = {
                        "id": "b-on", "pid": "tmp-1", "path": p2k, "positive": "test", "negative": "",
                        "width": 2048, "height": 1152,
                        "batch": {
                            "checkpoint": "krea2_anime_uwu_v2_w8a8.safetensors",
                            "loras": [],
                            "render_options": {"krea2_abliterated_encoder": True},
                        },
                    }
                    page._gallery_entries = [{"base": base_on, "variants": [], "applied_id": None, "active": base_on, "active_id": "b-on", "path": p2k}]
                    page._active_gallery_idx = 0
                    page._enter_second_pass()
                    self.assertTrue(page._sp_krea_ablit_chk.isChecked())
                    page._apply_sp_preset("krea2")
                    self.assertEqual(page._sp_model.currentData(), "krea2_anime_uwu_v2_w8a8.safetensors")
                    self.assertEqual(page._sp_scale.currentData(), 1.0)
                    self.assertEqual(page._sp_post_up.currentData(), 1.5)
                    self.assertEqual(page._sp_sampler.currentData(), "euler")
                    self.assertEqual(page._sp_scheduler.currentData(), "simple")
                    self.assertTrue(page._sp_krea_ablit_chk.isChecked())
                    page._exit_second_pass(silent=True)

                    # Round 1 did NOT use abliterated -> SP stays unchecked even when switching to moodyKrea2Mix
                    p1k = Path(tmp) / "base_1k.png"
                    Image.new("RGB", (1216, 832), (40, 50, 60)).save(p1k)
                    base_off = {
                        "id": "b-off", "pid": "tmp-1", "path": p1k, "positive": "test", "negative": "",
                        "width": 1216, "height": 832,
                        "batch": {
                            "checkpoint": "krea2_anime_uwu_v2_w8a8.safetensors",
                            "loras": [],
                            "render_options": {"krea2_abliterated_encoder": False},
                        },
                    }
                    page._gallery_entries = [{"base": base_off, "variants": [], "applied_id": None, "active": base_off, "active_id": "b-off", "path": p1k}]
                    page._active_gallery_idx = 0
                    page._enter_second_pass()
                    self.assertFalse(page._sp_krea_ablit_chk.isChecked())
                    page._apply_sp_preset("krea2")
                    self.assertEqual(page._sp_scale.currentData(), 1.5)
                    self.assertEqual(page._sp_post_up.currentData(), 1.0)
                    self.assertFalse(page._sp_krea_ablit_chk.isChecked())
                    page._set_combo_data(page._sp_model, "moodyKrea2Mix_v3_Global_BF16_abilirated.safetensors")
                    self.assertFalse(page._sp_krea_ablit_chk.isChecked())
                    win.close()
            finally:
                app.shutdown()

    def test_comfy_detach_and_reattach_across_restarts(self):
        from comic_workflow import processes

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            cfg = {"endpoint": "http://127.0.0.1:18188", "private_service": True}
            fake_proc = SimpleNamespace(pid=424242, poll=lambda: None, preloaded_checkpoint="krea2_anime_uwu_v2_w8a8.safetensors")
            with (
                patch.object(comfyui, "DATA", data_dir),
                patch.object(comfyui, "config", return_value=cfg),
                patch.object(comfyui, "is_pid_running", return_value=True),
                patch.object(processes, "is_pid_running", return_value=True),
                patch.object(
                    comfyui,
                    "request_json",
                    return_value={"system": {"argv": ["main.py", "--user-directory", str(data_dir / "comfy-user")]}},
                ),
                patch.object(comfyui, "terminate_tree") as term,
                patch.object(comfyui, "_private_process", None),
                patch.object(comfyui, "_verified_process", None),
            ):
                mgr1 = comfyui.ComfyManager(keep_alive=True, session_dir=data_dir)
                mgr1.process = fake_proc
                comfyui._private_process = fake_proc
                mgr1.detach()
                # Detach must NOT terminate ComfyUI and must persist session file
                term.assert_not_called()
                self.assertIsNone(mgr1.process)
                self.assertTrue((data_dir / "comfy-session.json").is_file())

                # New LocalDraw session re-attaches seamlessly and inherits preloaded checkpoint
                mgr2 = comfyui.ComfyManager(keep_alive=True, session_dir=data_dir)
                self.assertTrue(mgr2.is_running())
                self.assertEqual(mgr2.process.pid, 424242)
                self.assertEqual(mgr2.preloaded, "krea2_anime_uwu_v2_w8a8.safetensors")

                # Explicit unload/shutdown DOES terminate the attached process and removes session file
                mgr2.shutdown()
                term.assert_called_once()
                self.assertEqual(term.call_args.args[0].pid, 424242)
                self.assertFalse((data_dir / "comfy-session.json").is_file())

                # Stale/dead PID session file is automatically cleaned up without calling network
                (data_dir / "comfy-session.json").write_text(
                    '{"pid": 999999, "port": 18188, "user_directory": '
                    + __import__("json").dumps(str(data_dir / "comfy-user"))
                    + '}',
                    encoding="utf-8",
                )
            with (
                patch.object(comfyui, "DATA", data_dir),
                patch.object(comfyui, "config", return_value=cfg),
                patch.object(comfyui, "is_pid_running", return_value=False),
                patch.object(comfyui, "request_json") as req_dead,
            ):
                mgr3 = comfyui.ComfyManager(keep_alive=True, session_dir=data_dir)
                self.assertFalse(mgr3.is_running())
                req_dead.assert_not_called()
                self.assertFalse((data_dir / "comfy-session.json").is_file())

            # Transient timeout while PID is alive must NOT delete comfy-session.json
            (data_dir / "comfy-session.json").write_text(
                '{"pid": 777777, "port": 18188, "user_directory": '
                + __import__("json").dumps(str(data_dir / "comfy-user"))
                + '}',
                encoding="utf-8",
            )
            with (
                patch.object(comfyui, "DATA", data_dir),
                patch.object(comfyui, "config", return_value=cfg),
                patch.object(comfyui, "is_pid_running", return_value=True),
                patch.object(comfyui, "request_json", side_effect=TimeoutError("busy")),
            ):
                mgr_busy = comfyui.ComfyManager(keep_alive=True, session_dir=data_dir)
                self.assertFalse(mgr_busy.is_running())
                self.assertTrue((data_dir / "comfy-session.json").is_file())

            # Missing session file + own orphaned ComfyUI listening on port -> auto-adopts & saves session
            (data_dir / "comfy-session.json").unlink(missing_ok=True)
            own_stats = {
                "system": {
                    "argv": [
                        "main.py",
                        "--reserve-vram",
                        "2",
                        "--disable-smart-memory",
                        "--user-directory",
                        str(data_dir / "comfy-user"),
                    ]
                },
                "devices": [{"type": "cuda", "name": "RTX 5090 D v2"}],
            }
            cfg_auto = {**cfg, "memory_mode": "auto_cuda"}
            import socket as _socket

            with (
                patch.object(comfyui, "DATA", data_dir),
                patch.object(comfyui, "config", return_value=cfg_auto),
                patch.object(comfyui, "discover", return_value={"ready": False}),
                patch.object(_socket.socket, "bind", side_effect=OSError("port in use")),
                patch.object(comfyui, "find_listening_pid", return_value=888888),
                patch.object(comfyui, "is_pid_running", return_value=True),
                patch.object(processes, "is_pid_running", return_value=True),
                patch.object(comfyui, "request_json", return_value=own_stats),
                patch.object(comfyui, "_private_process", None),
                patch.object(comfyui, "_verified_process", None),
            ):
                mgr_adopt = comfyui.ComfyManager(keep_alive=True, session_dir=data_dir)
                mgr_adopt.start()
                self.assertTrue(mgr_adopt.is_running())
                self.assertEqual(mgr_adopt.process.pid, 888888)
                self.assertTrue((data_dir / "comfy-session.json").is_file())

            # Missing session file + FOREIGN service on port -> still refuses with isolation error
            (data_dir / "comfy-session.json").unlink(missing_ok=True)
            foreign_stats = {
                "system": {"argv": ["main.py", "--user-directory", "C:/other/studio/comfy-user"]},
                "devices": [{"type": "cuda"}],
            }
            with (
                patch.object(comfyui, "DATA", data_dir),
                patch.object(comfyui, "config", return_value=cfg_auto),
                patch.object(comfyui, "discover", return_value={"ready": False}),
                patch.object(_socket.socket, "bind", side_effect=OSError("port in use")),
                patch.object(comfyui, "find_listening_pid", return_value=999991),
                patch.object(comfyui, "is_pid_running", return_value=True),
                patch.object(comfyui, "request_json", return_value=foreign_stats),
                patch.object(comfyui, "_private_process", None),
                patch.object(comfyui, "_verified_process", None),
            ):
                mgr_foreign = comfyui.ComfyManager(keep_alive=True, session_dir=data_dir)
                with self.assertRaisesRegex(ValueError, "副本绘图端口已被占用"):
                    mgr_foreign.start()

    def test_close_event_exits_cleanly_while_keeping_comfy_alive(self):
        from PySide6.QtGui import QCloseEvent
        from PySide6.QtWidgets import QApplication, QMessageBox
        from comic_workflow.qt_app.main_window import StudioMainWindow

        with tempfile.TemporaryDirectory() as tmp:
            app = self._make_app(tmp)
            fake_proc = SimpleNamespace(pid=515151, poll=lambda: None, preloaded_checkpoint="krea2_turbo_bf16.safetensors")
            app.comfy.process = fake_proc
            try:
                win = StudioMainWindow(app, smoke=False)
                win._poll_timer.stop()

                # 1. Idle close: no confirmation popup, detaches ComfyUI (does NOT kill it), quits QApplication
                with (
                    patch.object(QMessageBox, "question") as ask,
                    patch.object(QApplication, "quit") as qt_quit,
                    patch.object(comfyui, "terminate_tree") as term,
                ):
                    ev = QCloseEvent()
                    win.closeEvent(ev)
                    self.assertTrue(ev.isAccepted())
                    ask.assert_not_called()
                    term.assert_not_called()
                    qt_quit.assert_called_once()
                    self.assertTrue(app.comfy.session_file.is_file())
            finally:
                app.shutdown(keep_comfy=False)

        # 2. Active task running: prompt asks confirmation; No ignores close, Yes interrupts & exits
        with tempfile.TemporaryDirectory() as tmp:
            app2 = self._make_app(tmp)
            app2.comfy.process = SimpleNamespace(pid=616161, poll=lambda: None, preloaded_checkpoint=None)
            try:
                win2 = StudioMainWindow(app2, smoke=False)
                win2._poll_timer.stop()
                with (
                    patch.object(app2, "has_active_work", return_value=True),
                    patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No),
                    patch.object(QApplication, "quit") as qt_quit,
                ):
                    ev_no = QCloseEvent()
                    win2.closeEvent(ev_no)
                    self.assertFalse(ev_no.isAccepted())
                    qt_quit.assert_not_called()
                    self.assertFalse(app2._shutdown_done)

                with (
                    patch.object(app2, "has_active_work", return_value=True),
                    patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes),
                    patch.object(comfyui, "api") as api_mock,
                    patch.object(comfyui, "terminate_tree") as term,
                    patch.object(QApplication, "quit") as qt_quit,
                ):
                    ev_yes = QCloseEvent()
                    win2.closeEvent(ev_yes)
                    self.assertTrue(ev_yes.isAccepted())
                    api_mock.assert_called_once_with("/interrupt", {}, timeout=1.5)
                    term.assert_not_called()
                    qt_quit.assert_called_once()
            finally:
                app2.shutdown(keep_comfy=False)

    def test_second_pass_gallery_apply_candidates_and_controls(self):
        from PIL import Image
        from comic_workflow import engine as e, quick_sample, second_pass
        from comic_workflow.qt_app.main_window import StudioMainWindow

        with tempfile.TemporaryDirectory() as tmp:
            app = self._make_app(tmp)
            fake_opts = {
                "comfy": {
                    "checkpoints": ["krea2_turbo_bf16.safetensors"],
                    "loras": [],
                    "configured": "krea2_turbo_bf16.safetensors",
                },
                "diffusion_models": {
                    "krea2_turbo_bf16.safetensors": {
                        "label": "Krea2 Turbo BF16",
                        "family": "krea2",
                        "steps": 8,
                        "sampler_name": "er_sde",
                        "scheduler": "beta",
                    },
                },
                "lora_inspection": {},
                "lora_labels": {},
                "upscale_models": ["4x-UltraSharp.pth"],
            }
            try:
                pid = app.repo.create_transient()
                proj = quick_sample.prepare_sample(app.repo, {
                    "text": "1girl, sunlit workshop",
                    "negative": "",
                    "checkpoint": "krea2_turbo_bf16.safetensors",
                    "count": 2,
                    "aspect_ratio": "3:2",
                })
                proj["transient"] = True
                e.save(app.repo.folder(pid) / "project.json", proj)

                img_dir = app.repo.folder(pid) / "images"
                (img_dir / "b1").mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (1024, 680), (40, 80, 120)).save(img_dir / "b1/1.png")
                Image.new("RGB", (1024, 680), (60, 90, 130)).save(img_dir / "b1/2.png")
                index = {
                    "batches": [{
                        "id": "b1",
                        "panel_id": "P001-K01",
                        "panel_hash": "h1",
                        "checkpoint": "krea2_turbo_bf16.safetensors",
                        "status": "done",
                        "loras": [],
                        "images": [
                            {"id": "b1-1", "file": "b1/1.png", "seed": 11, "positive": "1girl, sunlit workshop", "negative": ""},
                            {"id": "b1-2", "file": "b1/2.png", "seed": 12, "positive": "1girl, sunlit workshop", "negative": ""},
                        ],
                    }],
                    "selections": {},
                }
                e.save(img_dir / "index.json", index)

                with patch.object(app, "drawing_options", return_value=fake_opts):
                    win = StudioMainWindow(app, smoke=True)
                    win.open_standalone_prompt()
                    page = win._prompt_page
                    page.load_session(pid, board=False)

                    # Newest first: index 0 is b1-2, index 1 is b1-1
                    self.assertEqual(len(page._gallery_entries), 2)
                    self.assertEqual(page._gallery_entries[0]["base"]["id"], "b1-2")

                    # Enter Second Pass on b1-2
                    page._enter_second_pass()
                    self.assertEqual(page._sp_state["base_id"], "b1-2")
                    self.assertEqual(page._sp_state["view_id"], "b1-2")
                    self.assertTrue(page._thumb_scroll.isHidden())
                    self.assertTrue(page._clear_imgs_btn.isHidden())

                    # Start single-image second pass -> backend writes second_pass batch with render_options.second_pass=True
                    with patch.object(app, "action", return_value={"job": "sp-job-1"}):
                        page._run_second_pass()
                    (img_dir / "sp1").mkdir(parents=True, exist_ok=True)
                    Image.new("RGB", (1432, 952), (180, 120, 90)).save(img_dir / "sp1/1.png")
                    index["batches"].append({
                        "id": "sp1",
                        "kind": "second_pass",
                        "source_image_id": "b1-2",
                        "panel_id": "P001-K01",
                        "checkpoint": "krea2_turbo_bf16.safetensors",
                        "status": "done",
                        "loras": [],
                        "second_pass": {"source_image_id": "b1-2", "scale": 1.4, "mode": "full", "method": "latent", "denoise": 0.5, "steps": 8},
                        "render_options": {"direct_prompt": True, "second_pass": True, "sample_job": "sp-job-1"},
                        "images": [{"id": "sp1-1", "file": "sp1/1.png", "seed": 21, "width": 1432, "height": 952}],
                    })
                    index["second_pass_applied"] = {"b1-2": "sp1-1"}
                    e.save(img_dir / "index.json", index)

                    page.refresh_gallery(live_update=False)
                    # Must auto-switch viewer to sp1-1, show applied badge, and show [原图, 已应用] candidate strip below
                    self.assertEqual(page._sp_state["view_id"], "sp1-1")
                    self.assertEqual(page._viewer.current_path(), img_dir / "sp1/1.png")
                    self.assertEqual(page._img_badge.text(), "已应用二采")
                    self.assertTrue(page._apply_sp_btn.isHidden())
                    self.assertFalse(page._unapply_sp_btn.isHidden())
                    self.assertFalse(page._thumb_scroll.isHidden())
                    self.assertEqual(len(page._thumb_cards), 2)
                    self.assertEqual([cid for cid, _ in page._thumb_cards], ["b1-2", "sp1-1"])

                    # Run a 2-image second pass (manual apply) -> must switch to first new candidate and show 应用此图
                    page._sp_count.setValue(2)
                    with patch.object(app, "action", return_value={"job": "sp-job-2"}):
                        page._run_second_pass()
                    (img_dir / "sp2").mkdir(parents=True, exist_ok=True)
                    Image.new("RGB", (1432, 952), (90, 180, 120)).save(img_dir / "sp2/1.png")
                    Image.new("RGB", (1432, 952), (120, 90, 200)).save(img_dir / "sp2/2.png")
                    index["batches"].append({
                        "id": "sp2",
                        "kind": "second_pass",
                        "source_image_id": "b1-2",
                        "panel_id": "P001-K01",
                        "checkpoint": "krea2_turbo_bf16.safetensors",
                        "status": "done",
                        "loras": [],
                        "second_pass": {"source_image_id": "b1-2", "scale": 1.4, "mode": "full", "method": "latent", "denoise": 0.5, "steps": 8},
                        "render_options": {"direct_prompt": True, "second_pass": True, "sample_job": "sp-job-2"},
                        "images": [
                            {"id": "sp2-1", "file": "sp2/1.png", "seed": 31, "width": 1432, "height": 952},
                            {"id": "sp2-2", "file": "sp2/2.png", "seed": 32, "width": 1432, "height": 952},
                        ],
                    })
                    e.save(img_dir / "index.json", index)

                    page.refresh_gallery(live_update=False)
                    self.assertEqual(page._sp_state["view_id"], "sp2-1")
                    self.assertEqual(page._viewer.current_path(), img_dir / "sp2/1.png")
                    self.assertFalse(page._apply_sp_btn.isHidden())
                    self.assertEqual(len(page._thumb_cards), 4)

                    # Alt+4 switches to sp2-2; clicking 应用此图 applies sp2-2 via real backend action
                    page._on_alt_number(3)
                    self.assertEqual(page._sp_state["view_id"], "sp2-2")
                    page._apply_active_sp()
                    self.assertEqual(page._img_badge.text(), "已应用二采")
                    self.assertTrue(page._apply_sp_btn.isHidden())
                    self.assertEqual(page._gallery_entries[0]["applied_id"], "sp2-2")

                    # Lightbox in second-pass mode opens the 4 second-pass candidates at index 3 (sp2-2)
                    with patch("comic_workflow.qt_app.prompt_page.ImageLightboxDialog") as lb_cls:
                        page._open_lightbox()
                        lb_cls.assert_called_once()
                        lb_paths, lb_start = lb_cls.call_args.args[0], lb_cls.call_args.args[1]
                        self.assertEqual(len(lb_paths), 4)
                        self.assertEqual(lb_start, 3)
                        self.assertEqual(lb_paths[3], img_dir / "sp2/2.png")

                    # Unapply restores original and switches preview to original
                    page._unapply_active_sp()
                    self.assertEqual(page._sp_state["view_id"], "b1-2")
                    self.assertIsNone(page._gallery_entries[0]["applied_id"])
                    self.assertTrue(page._unapply_sp_btn.isHidden())

                    # Exit second pass -> normal gallery shows badge for 3 candidates and restores toolbar
                    page._exit_second_pass()
                    self.assertIsNone(page._sp_state)
                    self.assertEqual(page._img_badge.text(), "3 张二采候选")
                    self.assertFalse(page._clear_imgs_btn.isHidden())

                    # Double-click operation logic:
                    # 1. Double-click main first-pass viewer -> enters Second Pass without opening Lightbox
                    from PySide6.QtCore import QEvent, QPointF, Qt
                    from PySide6.QtGui import QMouseEvent

                    def _send_double_click(widget):
                        pos = QPointF(20.0, 20.0)
                        p1 = QMouseEvent(QEvent.Type.MouseButtonPress, pos, pos, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
                        r1 = QMouseEvent(QEvent.Type.MouseButtonRelease, pos, pos, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
                        d2 = QMouseEvent(QEvent.Type.MouseButtonDblClick, pos, pos, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
                        r2 = QMouseEvent(QEvent.Type.MouseButtonRelease, pos, pos, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
                        widget.mousePressEvent(p1)
                        widget.mouseReleaseEvent(r1)
                        widget.mouseDoubleClickEvent(d2)
                        widget.mouseReleaseEvent(r2)

                    with patch("comic_workflow.qt_app.prompt_page.ImageLightboxDialog") as lb_cls:
                        _send_double_click(page._viewer)
                        self.assertIsNotNone(page._sp_state)
                        self.assertEqual(page._sp_state["base_id"], "b1-2")
                        self.assertEqual(page._left_stack.currentIndex(), 1)
                        self.assertFalse(page._viewer._click_timer.isActive())
                        lb_cls.assert_not_called()

                    # 2. Double-click second-pass candidate on main viewer -> auto-applies without opening Lightbox
                    page._select_sp_view("sp2-1")
                    with patch("comic_workflow.qt_app.prompt_page.ImageLightboxDialog") as lb_cls:
                        _send_double_click(page._viewer)
                        self.assertEqual(page._gallery_entries[0]["applied_id"], "sp2-1")
                        self.assertEqual(page._img_badge.text(), "已应用二采")
                        lb_cls.assert_not_called()

                    # 3. Double-click another second-pass candidate thumbnail -> selects & auto-applies sp2-2
                    thumb_sp2_2 = dict(page._thumb_cards)["sp2-2"]
                    _send_double_click(thumb_sp2_2)
                    self.assertEqual(page._sp_state["view_id"], "sp2-2")
                    self.assertEqual(page._gallery_entries[0]["applied_id"], "sp2-2")

                    # 4. Double-click base/original thumbnail in second-pass mode -> safe no-op (keeps sp2-2 applied)
                    thumb_base = dict(page._thumb_cards)["b1-2"]
                    _send_double_click(thumb_base)
                    self.assertEqual(page._gallery_entries[0]["applied_id"], "sp2-2")

                    # 5. Exit second pass & double-click first-pass thumbnail #2 (b1-1) -> enters Second Pass on b1-1
                    page._exit_second_pass()
                    thumb_b1_1 = dict(page._thumb_cards)["b1-1"]
                    _send_double_click(thumb_b1_1)
                    self.assertIsNotNone(page._sp_state)
                    self.assertEqual(page._sp_state["base_id"], "b1-1")

                    # 6. Single-click on main viewer in fit mode still opens Lightbox when deferred click fires
                    with patch("comic_workflow.qt_app.prompt_page.ImageLightboxDialog") as lb_cls:
                        pos = QPointF(20.0, 20.0)
                        page._viewer.mousePressEvent(QMouseEvent(QEvent.Type.MouseButtonPress, pos, pos, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
                        page._viewer.mouseReleaseEvent(QMouseEvent(QEvent.Type.MouseButtonRelease, pos, pos, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
                        self.assertTrue(page._viewer._click_timer.isActive())
                        page._viewer._click_timer.stop()
                        page._viewer._emit_deferred_click()
                        lb_cls.assert_called_once()
                    win.close()
            finally:
                app.shutdown()

    def test_operation_logic_shortcuts_upscale_only_validation_and_session(self):
        from PIL import Image
        from comic_workflow.qt_app.main_window import StudioMainWindow

        with tempfile.TemporaryDirectory() as tmp:
            app = self._make_app(tmp)
            fake_opts = {
                "comfy": {
                    "checkpoints": ["krea2_turbo_bf16.safetensors"],
                    "loras": [],
                    "configured": "krea2_turbo_bf16.safetensors",
                },
                "diffusion_models": {
                    "krea2_turbo_bf16.safetensors": {"label": "Krea2 Turbo BF16", "family": "krea2", "steps": 8},
                },
                "lora_inspection": {},
                "lora_labels": {},
                "upscale_models": ["4x-UltraSharp.pth"],
            }
            try:
                with patch.object(app, "drawing_options", return_value=fake_opts):
                    win = StudioMainWindow(app, smoke=True)
                    win.open_standalone_prompt()
                    page = win._prompt_page

                    # 1. Invalid JSON in JSON tab aborts _start_draw instead of drawing stale positive text
                    page.positive.setPlainText("old prompt")
                    page._set_mode_tab(1)
                    page._json_edit.setPlainText("{not valid json")
                    with patch.object(app, "action") as act:
                        page._start_draw(queued=False)
                        act.assert_not_called()

                    # 2. Persistent prompt-xxx session must NOT send persistent project id to draw_quick_sample
                    page._set_mode_tab(0)
                    page.positive.setPlainText("fresh prompt")
                    page._session_pid = "prompt-persistent123"
                    captured = {}
                    with patch.object(app, "action", side_effect=lambda r: captured.update(r) or {"project": "tmp-new", "job": "j1"}):
                        page._start_draw(queued=False)
                    self.assertEqual(captured.get("action"), "draw_quick_sample")
                    self.assertNotIn("project", captured)

                    # 3. Second Pass: Ctrl+Enter routes to _run_second_pass, bad seed rejected, upscale_only disables sampling
                    dummy = Path(tmp) / "base.png"
                    Image.new("RGB", (2048, 1152), (50, 60, 70)).save(dummy)
                    base_rec = {
                        "id": "img-1",
                        "pid": "tmp-new",
                        "path": dummy,
                        "positive": "sunset street",
                        "negative": "",
                        "batch": {"checkpoint": "krea2_turbo_bf16.safetensors", "loras": []},
                    }
                    page._gallery_entries = [{
                        "base": base_rec, "variants": [], "applied_id": None,
                        "active": base_rec, "active_id": "img-1", "path": dummy,
                    }]
                    page._active_gallery_idx = 0
                    page._enter_second_pass()
                    # >=2MP starts at 1.0x
                    self.assertEqual(page._sp_scale.currentData(), 1.0)

                    # Invalid seed is rejected instead of silently ignored
                    page._sp_seed.setText("-42")
                    with patch.object(app, "action") as act:
                        page._on_ctrl_enter()
                        act.assert_not_called()

                    # Switching to upscale_only bumps 1.0x -> 1.5x and disables sampling widgets
                    page._sp_seed.clear()
                    page._set_combo_data(page._sp_method, "upscale_only")
                    self.assertEqual(page._sp_scale.currentData(), 1.5)
                    self.assertFalse(page._sp_denoise.isEnabled())
                    self.assertFalse(page._sp_count.isEnabled())
                    self.assertTrue(page._sp_lora_section.isHidden())
                    self.assertEqual(page._sp_start_btn.text(), "开始纯超分锐化")

                    sp_payload = {}
                    with patch.object(app, "action", side_effect=lambda r: sp_payload.update(r) or {"job": "sp-up"}):
                        page._on_ctrl_enter()
                    self.assertEqual(sp_payload.get("action"), "second_pass")
                    self.assertEqual(sp_payload.get("method"), "upscale_only")
                    self.assertEqual(sp_payload.get("count"), 1)
                    self.assertEqual(sp_payload.get("mode"), "full")
                    self.assertEqual(sp_payload.get("post_upscale"), 1.0)
                    self.assertEqual(sp_payload.get("loras"), [])
                    win.close()
            finally:
                app.shutdown()

    def test_gallery_preview_cache_reuses_thumbnails_and_switches_fast(self):
        import time
        from PIL import Image
        from comic_workflow.qt_app.main_window import StudioMainWindow
        from comic_workflow.qt_app.widgets import image_cache

        with tempfile.TemporaryDirectory() as tmp:
            app = self._make_app(tmp)
            fake_opts = {
                "comfy": {"checkpoints": ["krea2_turbo_bf16.safetensors"], "loras": [], "configured": "krea2_turbo_bf16.safetensors"},
                "diffusion_models": {"krea2_turbo_bf16.safetensors": {"label": "Krea2 Turbo", "family": "krea2", "steps": 8}},
                "lora_inspection": {},
                "lora_labels": {},
                "upscale_models": [],
            }
            try:
                image_cache().clear()
                entries = []
                for i in range(6):
                    p = Path(tmp) / f"img_{i}.png"
                    Image.new("RGB", (1536, 1024), (30 * i, 100, 200 - 20 * i)).save(p)
                    rec = {
                        "id": f"b-{i}",
                        "pid": "tmp-fast",
                        "path": p,
                        "seed": 100 + i,
                        "batch": {"checkpoint": "krea2_turbo_bf16.safetensors", "loras": []},
                    }
                    entries.append({
                        "base": rec, "variants": [], "applied_id": None,
                        "active": rec, "active_id": rec["id"], "path": p,
                    })

                with patch.object(app, "drawing_options", return_value=fake_opts):
                    win = StudioMainWindow(app, smoke=True)
                    win.open_standalone_prompt()
                    page = win._prompt_page
                    page._gallery_entries = entries
                    page._active_gallery_idx = 0
                    page._render_gallery()

                    first_card_ids = [id(card) for _, card in page._thumb_cards]
                    self.assertEqual(len(first_card_ids), 6)

                    # Warm cache by stepping through once, then measure cached round-trip switches
                    for _ in range(6):
                        page._step_gallery(1)
                    self.qt_app.processEvents()

                    t0 = time.perf_counter()
                    for _ in range(12):
                        page._step_gallery(1)
                    elapsed_ms = (time.perf_counter() - t0) * 1000.0

                    # Thumbnail widgets must be reused (zero widget rebuilds during image switch)
                    after_card_ids = [id(card) for _, card in page._thumb_cards]
                    self.assertEqual(first_card_ids, after_card_ids)
                    # 12 cached switches must be near-instant (< 150ms total even on slow CI)
                    self.assertLess(elapsed_ms, 150.0)
                    win.close()
            finally:
                app.shutdown()

    def test_full_scrollable_lora_and_character_lists(self):
        from PIL import Image
        from comic_workflow.qt_app.main_window import StudioMainWindow

        with tempfile.TemporaryDirectory() as tmp:
            app = self._make_app(tmp)
            loras = [f"krea2/角色/角色_{i:03d}.safetensors" for i in range(95)]
            chars = [{"id": f"c-{i:03d}", "name": f"测试角色_{i:03d}"} for i in range(65)]
            fake_opts = {
                "comfy": {"checkpoints": ["krea2_turbo_bf16.safetensors"], "loras": loras, "configured": "krea2_turbo_bf16.safetensors"},
                "diffusion_models": {"krea2_turbo_bf16.safetensors": {"label": "Krea2 Turbo", "family": "krea2", "steps": 8}},
                "lora_inspection": {f: {"family": "Krea2", "supported": True} for f in loras},
                "lora_labels": {},
                "upscale_models": [],
            }
            try:
                with patch.object(app, "drawing_options", return_value=fake_opts), \
                     patch.object(app, "characters_data", return_value={"characters": chars}), \
                     patch.object(app, "status", return_value={"comfy": {"ready": True}, "model": {"ready": False}, "projects": []}):
                    win = StudioMainWindow(app)
                    page = win._prompt_page
                    page.load_session(None, board=False)
                    self.assertEqual(page._lora_results.count(), 95)
                    self.assertEqual(page._char_results.count(), 65)
                    self.assertTrue(page._lora_results.uniformItemSizes())
                    self.assertTrue(page._char_results.uniformItemSizes())

                    dummy = Path(tmp) / "sp.png"
                    Image.new("RGB", (1024, 1024), (60, 60, 60)).save(dummy)
                    base_rec = {
                        "id": "img-sp", "pid": "p1", "path": dummy, "positive": "test",
                        "negative": "", "width": 1024, "height": 1024,
                        "batch": {"checkpoint": "krea2_turbo_bf16.safetensors", "loras": []},
                    }
                    page._gallery_entries = [{
                        "base": base_rec, "variants": [], "applied_id": None,
                        "active": base_rec, "active_id": "img-sp", "path": dummy,
                    }]
                    page._active_gallery_idx = 0
                    page._enter_second_pass()
                    self.assertEqual(page._sp_lora_results.count(), 95)
                    self.assertTrue(page._sp_lora_results.uniformItemSizes())
                    win.close()
            finally:
                app.shutdown()

    def test_floating_toast_popup_no_startup_spam_and_button_dialog_motion(self):
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QEnterEvent, QMouseEvent
        from PySide6.QtWidgets import QApplication
        from comic_workflow.qt_app.dialogs import DisplaySettingsDialog
        from comic_workflow.qt_app.main_window import StudioMainWindow
        from comic_workflow.qt_app.motion import MotionEngine, _InteractiveOverlay

        with tempfile.TemporaryDirectory() as tmp:
            app = self._make_app(tmp)
            historical_failed = [{"id": "old-fail-1", "status": "failed", "label": "旧任务", "error": "历史错误"}]
            live_failed = [*historical_failed, {"id": "new-fail-2", "status": "failed", "label": "新任务", "error": "实时错误"}]
            status_seq = [
                {"comfy": {"ready": False}, "model": {"ready": False}, "projects": [], "jobs": historical_failed},
                {"comfy": {"ready": False}, "model": {"ready": False}, "projects": [], "jobs": historical_failed},
                {"comfy": {"ready": False}, "model": {"ready": False}, "projects": [], "jobs": live_failed},
            ]
            call_idx = {"n": 0}

            def next_status():
                i = min(call_idx["n"], len(status_seq) - 1)
                call_idx["n"] += 1
                return status_seq[i]

            try:
                with patch.object(app, "status", side_effect=next_status):
                    win = StudioMainWindow(app)
                    win.resize(1280, 800)
                    win.show()
                    self.qt_app.processEvents()

                    # Global MotionEngine must be installed on QApplication
                    self.assertIsInstance(getattr(QApplication.instance(), "_ld_motion_engine", None), MotionEngine)

                    # Historical failed job on startup must NOT pop up unprompted
                    self.assertFalse(win._toast.isVisible())

                    # Floating toast is NOT inserted in central layout (zero layout shift)
                    self.assertIs(win._toast.parentWidget(), win)
                    self.assertEqual(win.centralWidget().layout().indexOf(win._toast), -1)

                    # New runtime failure triggers floating toast popup cleanly
                    win._poll_status()
                    self.qt_app.processEvents()
                    self.assertTrue(win._toast.isVisible())
                    self.assertIn("新任务失败", win._toast._label.text())
                    self.assertTrue(win._toast._error)

                    # Dismiss animates out cleanly
                    win._toast.dismiss()
                    self.assertTrue(win._toast._closing)

                    # Button interactive overlay activates on hover and press
                    btn = win._dl_btn
                    self.assertEqual(btn.cursor().shape(), Qt.CursorShape.PointingHandCursor)
                    enter = QEnterEvent(QPointF(8, 8), QPointF(8, 8), QPointF(8, 8))
                    QApplication.sendEvent(btn, enter)
                    press = QMouseEvent(
                        QEvent.Type.MouseButtonPress,
                        QPointF(8, 8),
                        QPointF(8, 8),
                        Qt.MouseButton.LeftButton,
                        Qt.MouseButton.LeftButton,
                        Qt.KeyboardModifier.NoModifier,
                    )
                    QApplication.sendEvent(btn, press)
                    ov = btn.findChild(_InteractiveOverlay, "_MotionOverlay")
                    self.assertIsNotNone(ov)
                    self.assertTrue(ov._ripple_active)

                    # Dialog entrance animation triggers smoothly on show and cleans up on close
                    dlg = DisplaySettingsDialog(app, win)
                    dlg.show()
                    self.qt_app.processEvents()
                    self.assertIsNotNone(dlg.property("_ld_dlg_anim"))
                    dlg.close()
                    self.qt_app.processEvents()
                    self.assertEqual(dlg.windowOpacity(), 1.0)
                    win.close()
            finally:
                app.shutdown()

    def test_lora_duplicate_disambiguation_and_favorite_star_pinning(self):
        from PIL import Image
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtWidgets import QApplication
        from comic_workflow.qt_app.main_window import StudioMainWindow
        from comic_workflow.qt_app.widgets import (
            ROLE_LORA_FAV,
            lora_disambiguated_names,
            lora_matches_search,
        )

        venti_1000 = "krea2/原神/角色/Krea 2_温迪_mmasondu_1000步.safetensors"
        venti_1600 = "krea2/原神/角色/Krea2_温迪_mmasondu_1600步.safetensors"
        venti_2800 = "krea2/原神/角色/Krea2_温迪_mmasondu_2800步.safetensors"
        venti_ver = "krea2/原神/角色/温迪_版本3180517.safetensors"
        virtuosa = "krea2/明日方舟/角色/Krea2_塑心_mmasondu_1600步.safetensors"
        style_det = "krea2/画风/Detailer-KREA2.safetensors"
        loras = [venti_1000, venti_1600, venti_2800, venti_ver, virtuosa, style_det]

        # 1. Duplicate names get step / version tags; unique LoRAs stay clean without extra clutter
        names = lora_disambiguated_names(loras)
        self.assertEqual(names[venti_1000], "温迪 · 角色版 · 1000步")
        self.assertEqual(names[venti_1600], "温迪 · 角色版 · 1600步")
        self.assertEqual(names[venti_2800], "温迪 · 角色版 · 2800步")
        self.assertEqual(names[venti_ver], "温迪 · 角色版 · 版本3180517")
        self.assertEqual(names[virtuosa], "塑心 · 角色版")
        self.assertEqual(names[style_det], "Detailer-KREA2")
        self.assertEqual(len(set(names.values())), len(loras))

        # Multi-token & version/step search finds exact variant
        self.assertTrue(lora_matches_search(venti_2800, {"pinyin": "wendi"}, "温迪 2800"))
        self.assertFalse(lora_matches_search(venti_1600, {"pinyin": "wendi"}, "温迪 2800"))
        self.assertTrue(lora_matches_search(venti_ver, {"pinyin": "wendi"}, "3180517"))

        with tempfile.TemporaryDirectory() as tmp:
            app = self._make_app(tmp)
            fake_opts = {
                "comfy": {
                    "checkpoints": ["krea2_anime_uwu_v2_w8a8.safetensors"],
                    "loras": loras,
                    "configured": "krea2_anime_uwu_v2_w8a8.safetensors",
                },
                "diffusion_models": {
                    "krea2_anime_uwu_v2_w8a8.safetensors": {"label": "Krea2 UWU v2", "family": "krea2", "steps": 8},
                },
                "lora_inspection": {
                    f: {"family": "Krea2", "supported": True, "pinyin": "wendi" if "温迪" in f else "suxin"}
                    for f in loras
                },
                "lora_labels": {},
                "upscale_models": [],
            }
            try:
                with patch.object(app, "drawing_options", return_value=fake_opts):
                    win = StudioMainWindow(app, smoke=True)
                    win.resize(1360, 840)
                    win.open_standalone_prompt()
                    page = win._prompt_page
                    page.load_session(None, board=False)
                    win.show()
                    self.qt_app.processEvents()

                    # Search "温迪" -> 4 variants displayed with distinct step/version labels
                    page._lora_search.setText("温迪")
                    self.qt_app.processEvents()
                    self.assertEqual(page._lora_results.count(), 4)
                    texts = [page._lora_results.item(i).text() for i in range(4)]
                    self.assertEqual(
                        texts,
                        [
                            "温迪 · 角色版 · 1000步",
                            "温迪 · 角色版 · 1600步",
                            "温迪 · 角色版 · 2800步",
                            "温迪 · 角色版 · 版本3180517",
                        ],
                    )

                    # Click the right-side star on row 2 (2800步) -> favorites WITHOUT picking the LoRA & pins to #1
                    idx2 = page._lora_results.model().index(2, 0)
                    vr = page._lora_results.visualRect(idx2)
                    sr = page._lora_results._ld_star_delegate.star_rect(vr)
                    center = QPointF(sr.center())
                    vp = page._lora_results.viewport()
                    QApplication.sendEvent(
                        vp,
                        QMouseEvent(
                            QEvent.Type.MouseButtonPress,
                            center,
                            center,
                            Qt.MouseButton.LeftButton,
                            Qt.MouseButton.LeftButton,
                            Qt.KeyboardModifier.NoModifier,
                        ),
                    )
                    QApplication.sendEvent(
                        vp,
                        QMouseEvent(
                            QEvent.Type.MouseButtonRelease,
                            center,
                            center,
                            Qt.MouseButton.LeftButton,
                            Qt.MouseButton.NoButton,
                            Qt.KeyboardModifier.NoModifier,
                        ),
                    )
                    self.qt_app.processEvents()

                    # Must NOT have selected the LoRA; must be favorited, persisted, and now #1
                    self.assertEqual(len(page._selected_loras), 0)
                    self.assertTrue(app.ui_state.is_lora_favorite(venti_2800))
                    self.assertEqual(page._lora_results.item(0).data(Qt.ItemDataRole.UserRole), venti_2800)
                    self.assertTrue(page._lora_results.item(0).data(ROLE_LORA_FAV))

                    # Clear & re-search "温迪" and "原神" -> favorited 2800步 stays #1 every time
                    page._lora_search.clear()
                    self.assertEqual(page._lora_results.item(0).data(Qt.ItemDataRole.UserRole), venti_2800)
                    page._lora_search.setText("原神")
                    self.assertEqual(page._lora_results.item(0).data(Qt.ItemDataRole.UserRole), venti_2800)

                    # Selecting it shows the distinguishing step tag and synced star on the selected card
                    page._on_pick_lora(page._lora_results.item(0))
                    self.assertIn(venti_2800, page._selected_loras)
                    self.assertIn("2800步", page._selected_loras[venti_2800]._name_lbl.text())
                    self.assertTrue(page._selected_loras[venti_2800]._favorite)

                    # Second-pass search also pins favorited LoRA to #1 and shows disambiguated labels
                    dummy = Path(tmp) / "sp.png"
                    Image.new("RGB", (1024, 1024), (50, 60, 70)).save(dummy)
                    base_rec = {
                        "id": "img-1", "pid": "p1", "path": dummy, "positive": "test", "negative": "",
                        "width": 1024, "height": 1024,
                        "batch": {"checkpoint": "krea2_anime_uwu_v2_w8a8.safetensors", "loras": []},
                    }
                    page._gallery_entries = [{
                        "base": base_rec, "variants": [], "applied_id": None,
                        "active": base_rec, "active_id": "img-1", "path": dummy,
                    }]
                    page._active_gallery_idx = 0
                    page._enter_second_pass()
                    page._sp_lora_search.setText("温迪")
                    self.assertEqual(page._sp_lora_results.item(0).data(Qt.ItemDataRole.UserRole), venti_2800)
                    self.assertEqual(page._sp_lora_results.item(0).text(), "温迪 · 角色版 · 2800步")
                    self.assertTrue(page._sp_lora_results.item(0).data(ROLE_LORA_FAV))

                    # Favorite persists across fresh UIStateStore reload
                    reloaded = UIStateStore(app.ui_state.path)
                    self.assertEqual(reloaded.favorite_loras(), [venti_2800])

                    # Chinese period (。) typed via KeyPress, IME InputMethodEvent, or paste in LoRA weight converts to ASCII '.'
                    from PySide6.QtGui import QInputMethodEvent, QKeyEvent

                    wspin = page._selected_loras[venti_2800].weight
                    le = wspin.lineEdit()
                    le.setText("0")
                    le.setCursorPosition(1)
                    QApplication.sendEvent(le, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_unknown, Qt.KeyboardModifier.NoModifier, "\u3002"))
                    QApplication.sendEvent(le, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_8, Qt.KeyboardModifier.NoModifier, "8"))
                    QApplication.sendEvent(le, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_5, Qt.KeyboardModifier.NoModifier, "5"))
                    wspin.interpretText()
                    self.assertEqual(le.text(), "0.85")
                    self.assertAlmostEqual(wspin.value(), 0.85, places=2)

                    # IME commit path (Windows Microsoft Pinyin / Sogou commits '。' via QInputMethodEvent)
                    le.setText("1")
                    le.setCursorPosition(1)
                    ime = QInputMethodEvent()
                    ime.setCommitString("\u300225")
                    QApplication.sendEvent(le, ime)
                    wspin.interpretText()
                    self.assertEqual(le.text(), "1.25")
                    self.assertAlmostEqual(wspin.value(), 1.25, places=2)

                    # Direct validate / valueFromText path (e.g. paste '0。65')
                    _, fixed, _ = wspin.validate("0\u300265", 4)
                    self.assertEqual(fixed, "0.65")
                    self.assertAlmostEqual(wspin.valueFromText("0\uff0e65"), 0.65, places=2)
                    win.close()
            finally:
                app.shutdown()

    def test_persistent_prompt_workspace_delete_button_and_ctrl_z_undo(self):
        from PIL import Image
        from PySide6.QtWidgets import QMessageBox
        from comic_workflow import engine as e, image_delete_cache as idc, quick_sample
        from comic_workflow.qt_app.main_window import StudioMainWindow
        from comic_workflow.repository import PROMPT_WORKSPACE_ID

        fake_opts = {
            "comfy": {
                "checkpoints": ["krea2_turbo_bf16.safetensors"],
                "loras": [],
                "configured": "krea2_turbo_bf16.safetensors",
            },
            "diffusion_models": {
                "krea2_turbo_bf16.safetensors": {
                    "label": "Krea2 Turbo BF16",
                    "family": "krea2",
                    "steps": 8,
                },
            },
            "lora_inspection": {},
            "lora_labels": {},
            "upscale_models": ["4x-UltraSharp.pth"],
        }

        with tempfile.TemporaryDirectory() as tmp:
            # Session 1: draw/populate persistent standalone prompt-workspace and close/shutdown app
            app1 = self._make_app(tmp)
            try:
                ws_dir = app1.repo.folder(PROMPT_WORKSPACE_ID)
                proj = quick_sample.prepare_sample(app1.repo, {
                    "text": "sunlit workshop",
                    "negative": "",
                    "checkpoint": "krea2_turbo_bf16.safetensors",
                    "count": 2,
                    "aspect_ratio": "3:2",
                })
                proj["transient"] = True
                e.save(ws_dir / "project.json", proj)
                img_dir = ws_dir / "images"
                (img_dir / "b1").mkdir(parents=True, exist_ok=True)
                (img_dir / "sp1").mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (1024, 680), (40, 80, 120)).save(img_dir / "b1/1.png")
                Image.new("RGB", (1024, 680), (80, 120, 160)).save(img_dir / "b1/2.png")
                Image.new("RGB", (1432, 952), (200, 130, 90)).save(img_dir / "sp1/1.png")
                e.save(
                    img_dir / "index.json",
                    {
                        "batches": [
                            {
                                "id": "b1",
                                "panel_id": "P001-K01",
                                "checkpoint": "krea2_turbo_bf16.safetensors",
                                "status": "done",
                                "loras": [],
                                "images": [
                                    {"id": "b1-1", "file": "b1/1.png", "seed": 11, "positive": "sunlit workshop", "negative": ""},
                                    {"id": "b1-2", "file": "b1/2.png", "seed": 12, "positive": "sunlit workshop", "negative": ""},
                                ],
                            },
                            {
                                "id": "sp1",
                                "kind": "second_pass",
                                "source_image_id": "b1-2",
                                "panel_id": "P001-K01",
                                "checkpoint": "krea2_turbo_bf16.safetensors",
                                "status": "done",
                                "loras": [],
                                "second_pass": {"source_image_id": "b1-2", "scale": 1.4, "mode": "full", "method": "latent", "denoise": 0.32, "steps": 8},
                                "images": [{"id": "sp1-1", "file": "sp1/1.png", "seed": 21, "width": 1432, "height": 952}],
                            },
                        ],
                        "selections": {},
                        "second_pass_applied": {"b1-2": "sp1-1"},
                    },
                )
            finally:
                app1.shutdown(keep_comfy=False)

            # Session 2: reopen LocalDraw -> previously drawn images are preserved automatically!
            app2 = self._make_app(tmp)
            try:
                with patch.object(app2, "drawing_options", return_value=fake_opts):
                    win = StudioMainWindow(app2, smoke=True)
                    win.open_standalone_prompt()
                    page = win._prompt_page
                    win.show()
                    self.qt_app.processEvents()

                    self.assertEqual(page._session_pid, PROMPT_WORKSPACE_ID)
                    self.assertEqual(len(page._gallery_entries), 2)
                    self.assertEqual(page._gallery_entries[0]["base"]["id"], "b1-2")
                    self.assertEqual(page._gallery_entries[0]["applied_id"], "sp1-1")
                    self.assertEqual(page._pager_lbl.text(), "1 / 2")
                    self.assertFalse(page._delete_img_btn.isHidden())
                    self.assertTrue(page._delete_img_btn.isEnabled())

                    # Click '删除当前图片' without any confirmation popup -> moves b1-2 + sp1-1 into cache
                    with patch.object(QMessageBox, "question") as ask:
                        page._delete_img_btn.click()
                        ask.assert_not_called()
                    self.assertEqual(len(page._gallery_entries), 1)
                    self.assertEqual(page._gallery_entries[0]["base"]["id"], "b1-1")
                    self.assertEqual(page._pager_lbl.text(), "1 / 1")
                    self.assertEqual(idc.undo_count(app2.repo, PROMPT_WORKSPACE_ID), 1)
                    self.assertFalse((app2.repo.folder(PROMPT_WORKSPACE_ID) / "images/b1/2.png").exists())

                    # Ctrl+Z restores b1-2 AND its applied second-pass variant sp1-1 cleanly
                    page._on_ctrl_z()
                    self.assertEqual(len(page._gallery_entries), 2)
                    self.assertEqual(page._gallery_entries[page._active_gallery_idx]["base"]["id"], "b1-2")
                    self.assertEqual(page._gallery_entries[page._active_gallery_idx]["applied_id"], "sp1-1")
                    self.assertTrue((app2.repo.folder(PROMPT_WORKSPACE_ID) / "images/b1/2.png").is_file())
                    self.assertTrue((app2.repo.folder(PROMPT_WORKSPACE_ID) / "images/sp1/1.png").is_file())

                    # Inside Second Pass: deleting current variant removes only sp1-1, Ctrl+Z restores it
                    page._enter_second_pass()
                    self.assertEqual(page._sp_state["view_id"], "sp1-1")
                    page._delete_img_btn.click()
                    self.assertIsNotNone(page._sp_state)
                    self.assertEqual(page._sp_state["view_id"], "b1-2")
                    self.assertEqual(len(page._gallery_entries[0]["variants"]), 0)
                    page._on_ctrl_z()
                    self.assertEqual(len(page._gallery_entries[0]["variants"]), 1)
                    self.assertEqual(page._sp_state["view_id"], "sp1-1")
                    self.assertEqual(page._gallery_entries[0]["applied_id"], "sp1-1")
                    page._exit_second_pass()

                    # Deleting all remaining images disables button; Ctrl+Z still brings them back
                    page._delete_img_btn.click()
                    page._delete_img_btn.click()
                    self.assertEqual(len(page._gallery_entries), 0)
                    self.assertTrue(page._delete_img_btn.isHidden())
                    page._on_ctrl_z()
                    self.assertEqual(len(page._gallery_entries), 1)
                    self.assertFalse(page._delete_img_btn.isHidden())

                    # Typing in positive prompt + Ctrl+Z undoes text instead of stealing undo for gallery
                    from PySide6.QtGui import QTextCursor

                    page.positive.setFocus()
                    page.positive.setPlainText("base text")
                    page.positive.moveCursor(QTextCursor.MoveOperation.End)
                    page.positive.insertPlainText(" extra")
                    self.assertEqual(page.positive.toPlainText(), "base text extra")
                    before_undo = idc.undo_count(app2.repo, PROMPT_WORKSPACE_ID)
                    page._on_ctrl_z()
                    self.assertEqual(page.positive.toPlainText(), "base text")
                    self.assertEqual(idc.undo_count(app2.repo, PROMPT_WORKSPACE_ID), before_undo)
                    win.close()
            finally:
                app2.shutdown(keep_comfy=False)


if __name__ == "__main__":
    unittest.main()
