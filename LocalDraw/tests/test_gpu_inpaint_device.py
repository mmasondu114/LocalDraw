"""The inpaint adapter must reject an unavailable CUDA device, without fallback."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


class GpuInpaintDeviceTests(unittest.TestCase):
    def test_cpu_device_is_rejected_before_encoding(self):
        source=Path(__file__).resolve().parents[1]/'scripts/comfy_gpu_hooks_node.py'
        spec=importlib.util.spec_from_file_location('gpu_hooks_test',source)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        management=types.ModuleType('comfy.model_management')
        management.get_torch_device=lambda:types.SimpleNamespace(type='cpu')
        comfy=types.ModuleType('comfy');comfy.model_management=management
        native=types.ModuleType('nodes')
        with patch.dict(sys.modules,{'comfy':comfy,'comfy.model_management':management,'nodes':native}):
            with self.assertRaisesRegex(RuntimeError,'禁止回退到 CPU'):
                module.LocalDrawGpuInpaintEncode().encode(None,None,None)

    def test_upscaler_rejects_cpu_before_model_inference(self):
        source=Path(__file__).resolve().parents[1]/'scripts/comfy_gpu_hooks_node.py'
        spec=importlib.util.spec_from_file_location('gpu_upscale_test',source)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        management=types.ModuleType('comfy.model_management')
        management.get_torch_device=lambda:types.SimpleNamespace(type='cpu')
        comfy=types.ModuleType('comfy');comfy.model_management=management
        with patch.dict(sys.modules,{'comfy':comfy,'comfy.model_management':management}):
            with self.assertRaisesRegex(RuntimeError,'禁止回退到 CPU'):
                module.LocalDrawGpuUpscale().upscale(None,None)
