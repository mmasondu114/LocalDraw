"""Load pinned author mesh code without constructing its CPU-only detector.

The network classes and projection/ray methods are unchanged source. Only the
constructor's later MediaPipe setup is excluded; reviewed CUDA boxes replace
that front end in the diagnostic caller.
"""
import ast
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
PORT = ROOT / 'data/published-solutions/comfyui-controlnet-aux'


def load_native_runtime():
    sys.path[:0] = [str(ROOT / 'data/mesh-hand-runtime/python-libs'), str(PORT / 'src'), str(PORT), 'E:/AI/ComfyUI']
    sys.argv = [sys.argv[0], '--cuda-device', '0', '--gpu-only']
    import torch
    import numpy as np
    import gc
    from types import SimpleNamespace
    from torchvision import transforms
    from trimesh import Trimesh
    from trimesh.ray.ray_triangle import RayMeshIntersector
    import transformers.file_utils
    if not hasattr(transformers.file_utils, 'PYTORCH_PRETRAINED_BERT_CACHE'):
        transformers.file_utils.PYTORCH_PRETRAINED_BERT_CACHE = transformers.file_utils.TRANSFORMERS_CACHE
    from custom_mesh_graphormer.modeling.bert import BertConfig, Graphormer, Graphormer_Hand_Network
    from custom_mesh_graphormer.modeling._mano import MANO, Mesh
    from custom_mesh_graphormer.modeling.hrnet.hrnet_cls_net_gridfeat import get_cls_net_gridfeat
    from custom_mesh_graphormer.modeling.hrnet.config import config as hrnet_config
    from custom_mesh_graphormer.modeling.hrnet.config import update_config as hrnet_update_config
    from custom_mesh_graphormer.utils.miscellaneous import set_seed
    from comfy.model_management import soft_empty_cache
    if not torch.cuda.is_available():
        raise RuntimeError('手部网格模型必须在 CUDA 推理；禁止 CPU 回退')
    records = {}
    for name in ('graphormer', 'hrnet'):
        record = json.loads((ROOT / f'data/lora-curation/research/mesh-hand-{name}-verified.json').read_text(encoding='utf-8'))
        path = ROOT / record['file']
        assert path.stat().st_size == record['size']
        with path.open('rb') as stream:
            assert hashlib.file_digest(stream, 'sha256').hexdigest() == record['sha256']
        records[name] = record
    source_path = PORT / 'src/custom_controlnet_aux/mesh_graphormer/pipeline.py'
    tree = ast.parse(source_path.read_text(encoding='utf-8'))
    original = next(item for item in tree.body if isinstance(item, ast.ClassDef) and item.name == 'MeshGraphormerMediapipe')
    constructor = next(item for item in original.body if isinstance(item, ast.FunctionDef) and item.name == '__init__')
    # Stop exactly before the source opens the MediaPipe detector model. No
    # detector is created, and its CPU execution cannot be reached.
    cutoff = next(index for index, item in enumerate(constructor.body) if isinstance(item, ast.With))
    constructor.body = constructor.body[:cutoff]
    methods = [constructor] + [item for item in original.body if isinstance(item, ast.FunctionDef) and item.name in ('get_rays', 'run_inference')]
    original.body = methods
    original.bases = []
    native_module = ast.fix_missing_locations(ast.Module(body=[original], type_ignores=[]))
    args = SimpleNamespace(num_workers=2, seed=88, device=torch.device('cuda:0'),
        input_feat_dim='2051,512,128', hidden_feat_dim='1024,256,64', which_gcn='0,0,1',
        num_hidden_layers=4, num_attention_heads=4, hidden_size=-1, intermediate_size=-1,
        config_name='', model_name_or_path=str(PORT / 'src/custom_mesh_graphormer/modeling/bert/bert-base-uncased'),
        run_eval_only=True, resume_checkpoint=str(ROOT / records['graphormer']['file']),
        hrnet_checkpoint=str(ROOT / records['hrnet']['file']), arch='hrnet-w64', mesh_type='hand')
    namespace = dict(torch=torch, np=np, gc=gc, os=__import__('os'), Path=Path,
        BertConfig=BertConfig, Graphormer=Graphormer, Graphormer_Network=Graphormer_Hand_Network,
        MANO=MANO, Mesh=Mesh, hrnet_config=hrnet_config, hrnet_update_config=hrnet_update_config,
        get_cls_net_gridfeat=get_cls_net_gridfeat, transforms=transforms, set_seed=set_seed,
        soft_empty_cache=soft_empty_cache, Trimesh=Trimesh, RayMeshIntersector=RayMeshIntersector,
        args=args, __file__=str(source_path))
    exec(compile(native_module, str(source_path), 'exec'), namespace)
    runtime = namespace['MeshGraphormerMediapipe'](args)
    state = torch.load(args.resume_checkpoint, map_location='cpu', weights_only=True)
    missing_parameters = sorted(set(dict(runtime._model.named_parameters())) - set(state))
    if missing_parameters:
        raise RuntimeError('作者权重缺少网络参数：' + ', '.join(missing_parameters[:5]))
    loading = runtime._model.load_state_dict(state, strict=False)
    del state
    for model in (runtime._model, runtime.mano_model):
        if any(value.device.type != 'cuda' for value in list(model.parameters()) + list(model.buffers())):
            raise RuntimeError('作者网络或 MANO 未完整放入 GPU')
    if any(value.device.type != 'cuda' for value in runtime.mesh_sampler._D + runtime.mesh_sampler._U):
        raise RuntimeError('作者网格采样矩阵未完整放入 GPU')
    if not all(torch.isfinite(value).all().item() for value in runtime._model.parameters()):
        raise RuntimeError('作者网格模型参数包含非有限值')
    return runtime, {'models': records, 'constructor_detector_creation_excluded': True,
        'native_source': source_path.relative_to(ROOT).as_posix(),
        'native_source_sha256': hashlib.sha256(source_path.read_bytes()).hexdigest(),
        'loading_missing_buffers': list(loading.missing_keys), 'loading_unexpected_keys': list(loading.unexpected_keys),
        'source_methods': ['get_rays', 'run_inference'], 'network_forward_modified': False,
        'device': 'cuda:0', 'cpu_neural_fallback': False,
        'geometry_only': 'Author ray casting and projected-coordinate conversion use ordinary CPU geometry after CUDA neural output.'}
