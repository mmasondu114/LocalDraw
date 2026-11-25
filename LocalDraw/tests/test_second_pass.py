import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from PIL import Image

from comic_workflow import comfyui, engine as e, quick_sample, second_pass
from comic_workflow.repository import Repository


def _png_bytes(width=64, height=64, color=(120, 150, 200)):
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


class _FakeHTTPResponse:
    def __init__(self, payload):
        self._payload = payload

    def read(self, _limit=-1):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class SecondPassTests(unittest.TestCase):
    def setUp(self):
        cfg = {**comfyui.config(), "hand_detail": False, "delta_matte_skin": True}
        p_cfg = patch.object(comfyui, "config", return_value=cfg)
        p_cfg.start()
        self.addCleanup(p_cfg.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Repository(Path(self.tmp.name) / "projects")
        self.anima = "animaDeltaMix38BQwen0_deltaMixV60.safetensors"
        self.krea2 = "krea2_turbo_bf16.safetensors"
        self.moss = "moss_image_sft_bf16.safetensors"
        self.qwen_edit = "qwen_image_edit_2511_fp8mixed.safetensors"
        self.discovery_data = {
            "ready": True,
            "checkpoints": [self.anima, self.krea2, self.moss, self.qwen_edit],
            "loras": ["anime/style_a.safetensors", "krea2/space_b.safetensors"],
            "lora_files": {
                "anime/style_a.safetensors": "anime\\style_a.safetensors",
                "krea2/space_b.safetensors": "krea2\\space_b.safetensors",
            },
        }
        p_disc = patch.object(comfyui, "discover", return_value=self.discovery_data)
        p_disc.start()
        self.addCleanup(p_disc.stop)
        p_insp = patch("comic_workflow.lora_inspection.inspect_local", return_value={
            "anime/style_a.safetensors": {"structurally_compatible": True, "family": "Anima"},
            "krea2/space_b.safetensors": {"structurally_compatible": True, "family": "Krea2"},
        })
        p_insp.start()
        self.addCleanup(p_insp.stop)

    def _create_project_with_base(self, width=832, height=1216, checkpoint=None, loras=None):
        checkpoint = checkpoint or self.anima
        pid = quick_sample.create(self.repo, {
            "text": "1girl, rooftop, sunset, anime illustration",
            "negative": "lowres, blurry",
            "checkpoint": checkpoint,
            "count": 1,
            "aspect_ratio": "2:3",
        }, persistent=True)["project"]
        img_file = self.repo.folder(pid) / "images" / "basebatch" / "1.png"
        img_file.parent.mkdir(parents=True, exist_ok=True)
        img_file.write_bytes(_png_bytes(width, height))
        base_img = {
            "id": "basebatch-1",
            "file": "basebatch/1.png",
            "seed": 42,
            "positive": "1girl, rooftop, sunset, anime illustration",
            "negative": "lowres, blurry",
            "review": {"verdict": "unreviewed"},
        }
        index = {
            "batches": [{
                "id": "basebatch",
                "panel_id": "P001-K01",
                "panel_hash": "hash1",
                "checkpoint": checkpoint,
                "status": "done",
                "loras": loras or [],
                "images": [base_img],
            }],
            "selections": {
                "P001-K01": {"batch": "basebatch", "image": dict(base_img)},
            },
        }
        e.save(self.repo.folder(pid) / "images/index.json", index)
        return pid, "basebatch-1"

    def test_build_graph_cross_model_scale_and_vae_decode_threshold(self):
        cfg = {**comfyui.config(), "steps": 8, "cfg": 1.0, "sampler": "euler", "scheduler": "simple", "delta_matte_skin": False}
        # Default method="latent" uses VAEEncode -> LatentUpscaleBy(bicubic, scale_by=1.4) -> KSampler
        wf_lat, (tw_l, th_l) = second_pass.build_graph(
            self.krea2, "1girl, rooftop", "", 123, cfg,
            [{"file": "krea2/space_b.safetensors", "weight": 0.6, "clip_weight": 0.6}],
            "second-pass/abc.png", (1000, 1024), 1.4, 0.5, has_upscaler=True, method="latent",
        )
        self.assertEqual((tw_l, th_l), (1400, 1432))
        self.assertNotIn("4", wf_lat)
        self.assertNotIn("51", wf_lat)
        self.assertEqual(wf_lat["54"]["class_type"], "VAEEncode")
        self.assertEqual(wf_lat["55"]["class_type"], "LatentUpscaleBy")
        self.assertEqual(wf_lat["55"]["inputs"]["upscale_method"], "bicubic")
        self.assertEqual(wf_lat["55"]["inputs"]["scale_by"], 1.4)
        self.assertEqual(wf_lat["5"]["inputs"]["latent_image"], ["55", 0])
        self.assertEqual(wf_lat["5"]["inputs"]["denoise"], 0.5)
        self.assertEqual(wf_lat["6"]["class_type"], "VAEDecode")

        # method="upscale" @ 1.5x uses RealESRGAN + lanczos -> VAEEncode -> KSampler
        wf, (tw, th) = second_pass.build_graph(
            self.krea2, "1girl, rooftop", "", 123, cfg,
            [{"file": "krea2/space_b.safetensors", "weight": 0.6, "clip_weight": 0.6}],
            "second-pass/abc.png", (832, 1216), 1.5, 0.3, has_upscaler=True, method="upscale",
        )
        self.assertEqual((tw, th), (1248, 1824))
        self.assertNotIn("4", wf)
        self.assertEqual(wf["50"]["class_type"], "LoadImage")
        self.assertEqual(wf["51"]["class_type"], "UpscaleModelLoader")
        self.assertEqual(wf["52"]["class_type"], "ImageUpscaleWithModel")
        self.assertEqual(wf["53"]["class_type"], "ImageScale")
        self.assertEqual(wf["54"]["class_type"], "VAEEncode")
        self.assertEqual(wf["54"]["inputs"]["pixels"], ["53", 0])
        self.assertEqual(wf["5"]["inputs"]["latent_image"], ["54", 0])
        self.assertEqual(wf["5"]["inputs"]["denoise"], 0.3)
        self.assertEqual(wf["5"]["inputs"]["steps"], 8)
        self.assertEqual(wf["6"]["class_type"], "VAEDecode")

        # method="upscale" @ 1.25x skips RealESRGAN and uses pure lanczos
        wf125, _ = second_pass.build_graph(
            self.anima, "1girl", "bad", 99, {**cfg, "steps": 24}, [],
            "second-pass/abc.png", (832, 1216), 1.25, 0.35, has_upscaler=True, method="upscale",
        )
        self.assertNotIn("51", wf125)
        self.assertEqual(wf125["53"]["class_type"], "ImageScale")

        # Forced full pass above 4.5MP uses large-tile VAEDecodeTiled (1024 / 256)
        wf4k, (tw4, th4) = second_pass.build_graph(
            self.anima, "1girl", "", 99, {**cfg, "steps": 24}, [],
            "second-pass/abc.png", (2048, 1152), 2.0, 0.3, has_upscaler=True, method="upscale",
        )
        self.assertEqual((tw4, th4), (4096, 2304))
        self.assertEqual(wf4k["6"]["class_type"], "VAEDecodeTiled")
        self.assertEqual(wf4k["6"]["inputs"]["tile_size"], 1024)
        self.assertEqual(wf4k["6"]["inputs"]["overlap"], 256)

        # Scale 1.0x skips upscaler, ImageScale, and LatentUpscaleBy
        wf1, (tw1, th1) = second_pass.build_graph(
            self.anima, "1girl", "bad", 99, {**cfg, "steps": 24}, [],
            "second-pass/abc.png", (2048, 1152), 1.0, 0.45, has_upscaler=True, method="latent",
        )
        self.assertEqual((tw1, th1), (2048, 1152))
        self.assertNotIn("51", wf1)
        self.assertNotIn("53", wf1)
        self.assertNotIn("55", wf1)
        self.assertEqual(wf1["54"]["inputs"]["pixels"], ["50", 0])
        self.assertEqual(wf1["6"]["class_type"], "VAEDecode")

        # Post-upscale 2.0x runs RealESRGAN + lanczos AFTER VAEDecode without altering KSampler size
        wf_post, (out_w, out_h) = second_pass.build_graph(
            self.anima, "1girl", "bad", 99, {**cfg, "steps": 24}, [],
            "second-pass/abc.png", (2048, 1152), 1.0, 0.35, has_upscaler=True, method="latent", post_upscale=2.0,
        )
        self.assertEqual((out_w, out_h), (4096, 2304))
        self.assertEqual(wf_post["6"]["class_type"], "VAEDecode")
        self.assertEqual(wf_post["61"]["class_type"], "UpscaleModelLoader")
        self.assertEqual(wf_post["62"]["class_type"], "ImageUpscaleWithModel")
        self.assertEqual(wf_post["62"]["inputs"]["image"], ["6", 0])
        self.assertEqual(wf_post["63"]["class_type"], "ImageScale")
        self.assertEqual((wf_post["63"]["inputs"]["width"], wf_post["63"]["inputs"]["height"]), (4096, 2304))
        self.assertEqual(wf_post["7"]["inputs"]["images"], ["63", 0])

        # MOSS requires 16px alignment
        _, (tw_m, th_m) = second_pass.build_graph(
            self.moss, "1girl", "", 7, {**cfg, "steps": 20}, [],
            "second-pass/abc.png", (840, 1208), 1.0, 0.45, has_upscaler=False, method="latent",
        )
        self.assertEqual(tw_m % 16, 0)
        self.assertEqual(th_m % 16, 0)

    def test_resolve_mode_compute_tiles_and_seamless_blend(self):
        # 2048x1152 @ 1.4x (2864x1616 = 4.63MP) stays full on Krea2 (zero ghost clones), auto-tiles at 4K
        self.assertEqual(second_pass.resolve_mode("auto", 2864, 1616, self.krea2), "full")
        self.assertEqual(second_pass.resolve_mode("auto", 4096, 2304, self.krea2), "tiled")
        self.assertEqual(second_pass.resolve_mode("auto", 2048, 1152, self.anima), "full")
        self.assertEqual(second_pass.resolve_mode("auto", 4096, 2304, self.anima), "tiled")
        self.assertEqual(second_pass.resolve_mode("full", 4096, 2304, self.anima), "full")

        grid = second_pass.compute_tiles(4096, 2304, 1024, overlap=192, alignment=8)
        self.assertEqual(len(grid), 3)
        self.assertEqual(len(grid[0]), 5)
        for row in grid:
            for (x0, y0, x1, y1) in row:
                self.assertEqual((x1 - x0, y1 - y0), (1024, 1024))
                self.assertEqual(x0 % 8, 0)
                self.assertEqual(y0 % 8, 0)
        self.assertEqual(grid[0][0][0], 0)
        self.assertEqual(grid[0][-1][2], 4096)
        self.assertEqual(grid[-1][0][3], 2304)

        # Blend uniform color tiles -> exact target size and uniform pixels with zero seam artifacts
        small_grid = second_pass.compute_tiles(1600, 1200, 1024, overlap=192, alignment=8)
        rendered = [
            [(box, Image.new("RGB", (box[2] - box[0], box[3] - box[1]), (100, 180, 220))) for box in row]
            for row in small_grid
        ]
        blended = second_pass.blend_tiles((1600, 1200), rendered)
        self.assertEqual(blended.size, (1600, 1200))
        self.assertEqual(blended.getpixel((0, 0)), (100, 180, 220))
        self.assertEqual(blended.getpixel((800, 600)), (100, 180, 220))
        self.assertEqual(blended.getpixel((1599, 1199)), (100, 180, 220))

    def test_validate_request_isolates_loras_and_rejects_invalid_inputs(self):
        pid, base_id = self._create_project_with_base(
            checkpoint=self.anima,
            loras=[{"file": "anime/style_a.safetensors", "weight": 0.8}],
        )
        spec_same = second_pass.validate_request(self.repo, pid, {
            "source_image_id": base_id,
            "checkpoint": self.anima,
            "scale": 1.5,
        })
        self.assertEqual([l["file"] for l in spec_same["loras"]], ["anime/style_a.safetensors"])
        self.assertEqual(spec_same["steps"], 24)
        self.assertEqual(spec_same["denoise"], 0.45)
        self.assertEqual(spec_same["method"], "latent")
        self.assertEqual(spec_same["effective_mode"], "full")
        self.assertFalse(spec_same["delta_matte_skin"])

        spec_cross = second_pass.validate_request(self.repo, pid, {
            "source_image_id": base_id,
            "checkpoint": self.krea2,
            "scale": 1.4,
            "seed": 777,
        })
        self.assertEqual(spec_cross["loras"], [])
        self.assertEqual(spec_cross["steps"], 8)
        self.assertEqual(spec_cross["denoise"], 0.5)
        self.assertEqual(spec_cross["seed"], 777)

        for bad in (
            {"source_image_id": base_id, "checkpoint": self.qwen_edit},
            {"source_image_id": base_id, "checkpoint": self.krea2, "scale": 3.0},
            {"source_image_id": base_id, "checkpoint": self.krea2, "mode": "bogus"},
            {"source_image_id": base_id, "checkpoint": self.krea2, "method": "bogus"},
            {"source_image_id": base_id, "checkpoint": self.krea2, "post_upscale": 4.0},
            {"source_image_id": base_id, "checkpoint": self.krea2, "denoise": 0.01},
            {"source_image_id": base_id, "checkpoint": self.krea2, "steps": 0},
            {"source_image_id": base_id, "checkpoint": self.krea2, "count": 9},
            {"source_image_id": base_id, "checkpoint": self.krea2, "seed": -1},
            {"source_image_id": base_id, "checkpoint": self.krea2,
             "loras": [{"file": "anime/style_a.safetensors", "weight": 0.7}]},
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                second_pass.validate_request(self.repo, pid, bad)

    def _run_mocked_second_pass(self, pid, request, out_size=(128, 128), submitted=None):
        app = SimpleNamespace(repo=self.repo, model=Mock(), comfy=Mock())
        counter = {"n": 0}
        png_out = _png_bytes(out_size[0], out_size[1], (220, 120, 90))

        def fake_api(path, payload=None):
            if path == "/prompt":
                counter["n"] += 1
                if submitted is not None:
                    submitted.append(payload["prompt"])
                return {"prompt_id": f"p-{counter['n']}"}
            if path.startswith("/history/"):
                jid = path.split("/")[-1]
                return {jid: {"status": {"status_str": "success"},
                              "outputs": {"7": {"images": [{"filename": f"{jid}.png", "type": "output"}]}}}}
            raise AssertionError("unexpected path: " + path)

        fake_opener = Mock()
        fake_opener.open.side_effect = lambda *_a, **_kw: _FakeHTTPResponse(png_out)
        with patch.object(second_pass, "DATA", Path(self.tmp.name) / "data"), \
             patch.object(comfyui, "api", side_effect=fake_api), \
             patch("urllib.request.build_opener", return_value=fake_opener), \
             patch.object(second_pass, "ComfyProgress") as prog:
            prog.return_value.poll.return_value = None
            return second_pass.run(app, pid, request, Mock(), "sp-job")

    def test_single_image_auto_applies_and_can_unapply(self):
        pid, base_id = self._create_project_with_base(width=128, height=128)
        submitted = []
        res = self._run_mocked_second_pass(pid, {
            "source_image_id": base_id,
            "checkpoint": self.anima,
            "scale": 1.5,
            "denoise": 0.35,
            "steps": 24,
            "seed": 100,
            "count": 1,
        }, submitted=submitted)
        self.assertEqual(len(res["images"]), 1)
        self.assertEqual(res["mode"], "full")
        cand_id = res["images"][0]
        self.assertEqual(res["auto_applied"], cand_id)
        # Ensure delta_matte_skin did NOT silently inject matte prompt text
        self.assertNotIn("Matte anime illustration", submitted[0]["2"]["inputs"]["text"])

        state = comfyui.drawing_state(self.repo, pid)
        self.assertEqual(state["second_pass_applied"][base_id], cand_id)
        self.assertEqual(state["selections"]["P001-K01"]["image"]["id"], cand_id)

        reverted = second_pass.unapply_result(self.repo, pid, base_id)
        self.assertNotIn(base_id, reverted.get("second_pass_applied", {}))
        self.assertEqual(reverted["selections"]["P001-K01"]["image"]["id"], base_id)
        _, still_there = second_pass.find_image(reverted, cand_id)
        self.assertIsNotNone(still_there)

    def test_tiled_mode_runs_native_1k_tiles_and_produces_full_size_png(self):
        # 2048x1152 @ 1.5x = 3072x1728 -> auto resolves to tiled mode (4x2 = 8 tiles of 1024x1024)
        pid, base_id = self._create_project_with_base(width=2048, height=1152, checkpoint=self.krea2)
        submitted = []
        res = self._run_mocked_second_pass(pid, {
            "source_image_id": base_id,
            "checkpoint": self.anima,
            "scale": 1.5,
            "mode": "auto",
            "denoise": 0.3,
            "steps": 20,
            "seed": 888,
            "count": 1,
        }, out_size=(1024, 1024), submitted=submitted)
        self.assertEqual(res["mode"], "tiled")
        self.assertEqual(res["tiles"], 8)
        # Verify each sampling workflow used native VAEDecode (not VAEDecodeTiled) on 1K tiles
        sampling_wfs = [wf for wf in submitted if "5" in wf]
        self.assertEqual(len(sampling_wfs), 8)
        for wf in sampling_wfs:
            self.assertEqual(wf["6"]["class_type"], "VAEDecode")
        state = comfyui.drawing_state(self.repo, pid)
        sp_batch = [b for b in state["batches"] if b["id"] == res["batch"]][0]
        self.assertEqual(sp_batch["second_pass"]["mode"], "tiled")
        self.assertEqual(sp_batch["second_pass"]["tiles"], 8)
        out_png = self.repo.folder(pid) / "images" / sp_batch["images"][0]["file"]
        with Image.open(out_png) as final_im:
            self.assertEqual(final_im.size, (3072, 1728))

    def test_multi_image_waits_for_manual_apply(self):
        pid, base_id = self._create_project_with_base(width=128, height=128)
        res = self._run_mocked_second_pass(pid, {
            "source_image_id": base_id,
            "checkpoint": self.anima,
            "scale": 1.25,
            "denoise": 0.4,
            "steps": 12,
            "seed": 500,
            "count": 2,
        })
        self.assertEqual(len(res["images"]), 2)
        self.assertIsNone(res["auto_applied"])

        state = comfyui.drawing_state(self.repo, pid)
        self.assertNotIn(base_id, state.get("second_pass_applied", {}))
        self.assertEqual(state["selections"]["P001-K01"]["image"]["id"], base_id)
        sp_batch = [b for b in state["batches"] if b["id"] == res["batch"]][0]
        self.assertEqual([im["seed"] for im in sp_batch["images"]], [500, 501])

        applied = second_pass.apply_result(self.repo, pid, base_id, res["images"][1])
        self.assertEqual(applied["second_pass_applied"][base_id], res["images"][1])
        self.assertEqual(applied["selections"]["P001-K01"]["image"]["id"], res["images"][1])


if __name__ == "__main__":
    unittest.main()
