import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from comic_workflow import lora_binding as lb, comfyui, drawing_settings, drawing_tools, engine as e
from comic_workflow.character_binding import identity_hash
from comic_workflow.framing import apply_local_repair


class LoraBindingTests(unittest.TestCase):
    def test_editing_labels_preserves_imported_version_prompt_and_preference(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            file = "明日方舟/测试版本.safetensors"
            e.save(root / "lora-labels.json", {file: {
                "name": "测试角色", "character_id": "saved-a", "character": "甲",
                "trigger_words": "old", "weight": .7, "preferred": True,
                "appearance_prompt": "red hair, dark coat", "source_page": "https://example.com/model",
            }})
            with patch.object(comfyui, "DATA", root), \
                 patch.object(comfyui, "discover", return_value={"loras": [file]}), \
                 patch("comic_workflow.characters.CharacterLibrary.listing", return_value=[{"id": "saved-a", "name": "甲"}]):
                result = comfyui.save_lora_labels([{"file": file, "name": "测试角色", "weight": .8, "trigger_words": "new"}])[file]
            self.assertTrue(result["preferred"])
            self.assertEqual(result["appearance_prompt"], "red hair, dark coat")
            self.assertEqual(result["source_page"], "https://example.com/model")
            self.assertEqual(result["trigger_words"], "new")

    def setUp(self):
        self.p = {"brief": {"character_refs": [
            {"id": "saved-a", "name": "甲", "revision": 1, "profile": {}},
            {"id": "saved-b", "name": "甲乙", "revision": 1, "profile": {}},
        ]}, "canon": {"characters": [{"id": "C001", "name": "甲"}, {"id": "C002", "name": "甲乙"}]}}
        self.panel = {"id": "P001-K01", "characters": [{"character_id": "C001"}, {"character_id": "C002"}], "aspect_ratio": "3:2"}
        self.payload = {"positive": "two people, street", "negative": "blurry", "composition_variants": [],
            "character_anchors": [{"character_id":"C001","tags":"red hair"},{"character_id":"C002","tags":"blue hair"}],
            "framing": {"subjects": [{"character_id":"C001","region":[0,0,.5,1]},
                                     {"character_id":"C002","region":[.5,0,1,1]}]}}
        self.loras = [self.lora("style"), self.lora("genshin/same.safetensors", "saved-a"),
                      self.lora("arknights/same.safetensors", "saved-b")]
        self.cfg = {"width":1216,"height":832,"steps":28,"cfg":5,"sampler_name":"euler_ancestral","scheduler":"normal"}

    def lora(self, file, owner=""):
        return {"file":file,"character_id":owner,"weight":.7,"clip_weight":.7,"trigger_words":file,
                "compatible_checkpoint":"test","confirmed_compatibility":True}

    def resolved(self):
        return lb.route(self.loras,self.p,self.panel)

    def test_absent_character_is_excluded_and_name_prefix_is_not_a_match(self):
        panel={**self.panel,"characters":[{"character_id":"C001"}]}
        result=lb.route(self.loras,self.p,panel)
        self.assertEqual([l["file"] for l in result],["style","genshin/same.safetensors"])
        self.assertEqual(result[1]["target_character_ids"],["C001"])
        self.p["brief"]["character_refs"][0]["id"]="new-card-with-same-name"
        self.assertEqual([l["file"] for l in lb.route(self.loras,self.p,panel)],["style"])

    def test_shared_binding_overrides_stale_client_or_retry_metadata(self):
        labels={"style":{"character_id":"saved-b","character":"甲乙"}}
        panel={**self.panel,"characters":[{"character_id":"C001"}]}
        self.assertEqual(lb.route([self.loras[0]],self.p,panel,labels),[])
        rebound=lb.route(self.resolved(),self.p,self.panel,{self.loras[1]["file"]:{"character_id":""}})
        self.assertNotIn("target_character_ids",rebound[1])
        self.assertEqual(rebound[1]["character_id"],"")

    def test_legacy_multi_owner_is_never_silently_mixed(self):
        item={"file":"old","scope":"characters","characters":["甲","甲乙"]}
        with self.assertRaisesRegex(ValueError,"只能关联一个角色"):
            lb.route([item],self.p,self.panel)

    def test_missing_and_overlapping_regions_keep_all_loras_without_regional_passes(self):
        for region in [None,[.4,0,1,1],[float("nan"),0,1,1]]:
            payload=copy.deepcopy(self.payload)
            payload["framing"]["subjects"][1]["region"]=region
            with self.subTest(region=region):
                loras = self.resolved()
                globals_, passes = lb.plan(loras,self.p,self.panel,payload)
                self.assertEqual(globals_, loras)
                self.assertEqual(passes, [])

    def test_direct_prompt_with_unplaced_characters_can_use_all_character_loras(self):
        self.p['prompt_input'] = {'characters': [
            {'name': '甲', 'prompt': 'red hair'}, {'name': '甲乙', 'prompt': 'blue hair'}]}
        globals_, passes = lb.plan(self.resolved(), self.p, self.panel, {})
        graph = comfyui.graph('test', 'two people, red hair, blue hair', 'neg', 1, self.cfg, globals_)
        self.assertEqual(passes, [])
        self.assertEqual([n['inputs']['lora_name'] for n in graph.values() if n['class_type'] == 'LoraLoader'],
                         [l['file'] for l in self.loras])

    def test_branches_never_inherit_other_character_lora_and_only_composite_mask(self):
        globals_,passes=lb.plan(self.resolved(),self.p,self.panel,self.payload)
        graph=comfyui.graph("test","base","neg",1,self.cfg,globals_)
        lb.apply_passes(graph,passes,globals_,self.cfg,1,"neg")
        self.assertEqual(graph["5"]["inputs"]["model"],["8",0])
        for base,file in [(300,"genshin/same.safetensors"),(340,"arknights/same.safetensors")]:
            self.assertEqual(graph[str(base)]["inputs"]["model"],["1",0])
            self.assertEqual(graph[str(base+1)]["inputs"]["lora_name"],file)
            self.assertEqual(graph[str(base+1)]["inputs"]["model"],[str(base),0])
            self.assertEqual(graph[str(base+10)]["inputs"]["clip"],[str(base+1),1])
            self.assertEqual(graph[str(base+20)]["inputs"]["mask"],[str(base+14),0])
        self.assertEqual(graph["340"]["inputs"]["model"],["1",0])
        self.assertEqual(graph["360"]["inputs"]["destination"],["320",0])
        self.assertEqual(graph["7"]["inputs"]["images"],["360",0])
        self.assertNotIn("blue hair",graph["310"]["inputs"]["text"])
        self.assertNotIn("red hair",graph["350"]["inputs"]["text"])

    def test_local_repair_masks_are_intersected_before_character_compositing(self):
        globals_,passes=lb.plan(self.resolved(),self.p,self.panel,self.payload)
        graph=comfyui.graph("test","base","neg",1,self.cfg,globals_)
        apply_local_repair(graph,"original.png","mask.png","repair")
        lb.apply_passes(graph,passes,globals_,self.cfg,1,"neg")
        self.assertEqual(graph["315"]["inputs"]["operation"],"multiply")
        self.assertEqual(graph["315"]["inputs"]["source"],["212",0])
        self.assertEqual(graph["320"]["inputs"]["destination"],["214",0])
        self.assertEqual(graph["320"]["inputs"]["mask"],["315",0])

    def test_comic_auto_selection_matches_saved_ids(self):
        prefs={"mode":"characters","checkpoint":"test","items":[
            {**l,"name":l["file"],"selected":False,"confirmed":True} for l in self.loras]}
        panel={**self.panel,"characters":[{"character_id":"C002"}]}
        with patch.object(comfyui,"lora_labels",return_value={}):
            result=drawing_settings.loras_for_panel(prefs,self.p,panel)
        self.assertEqual([l["file"] for l in result],["arknights/same.safetensors"])

    def test_crossed_local_repair_uses_owner_and_preserves_binding_without_position_passes(self):
        self.p['pages'] = {'P001': {'panels': [self.panel]}}
        payload = copy.deepcopy(self.payload)
        payload['framing']['subjects'][1]['region'] = [0, 0, 1, 1]
        self.p['drawing_prompts'] = {self.panel['id']: {'panel_hash': e.digest(self.panel),
            'identity_hash': identity_hash(self.p, self.panel), 'payload': payload}}
        original = copy.deepcopy(self.loras)
        graphs = []
        def capture(path, body=None):
            if path == '/prompt': graphs.append(body['prompt'])
            raise ValueError('offline capture')
        with tempfile.TemporaryDirectory() as tmp:
            repo = Mock(); repo.get.return_value = self.p; repo.folder.return_value = Path(tmp)
            with patch.object(comfyui, 'config', return_value={**self.cfg, 'checkpoint': 'test', 'endpoint': 'http://127.0.0.1:8188'}), \
                 patch.object(comfyui, 'discover', return_value={'ready': True, 'checkpoints': ['test'], 'loras': [l['file'] for l in self.loras]}), \
                 patch.object(comfyui, 'api', side_effect=capture), patch.object(comfyui, 'ComfyProgress'), \
                 patch.object(comfyui, 'lora_labels', return_value={}), \
                 patch('comic_workflow.art_presets.get', side_effect=AssertionError('source recipe must be retained')), \
                 patch('comic_workflow.reference_images.references_for_panel', side_effect=AssertionError('no full portraits in cuff patch')), \
                 patch('comic_workflow.framing.apply_camera', side_effect=AssertionError('no other role anchors')):
                with self.assertRaisesRegex(ValueError, 'offline capture'):
                    comfyui.run_batch(repo, 'test', self.panel['id'], Mock(), 'test', 1, loras=self.loras,
                        render_options={'reference': True, 'control': True, 'direct_prompt': True,
                            'local_repair': {'owner': 'C002', 'original': 'original.png', 'mask': 'crossed-cuff.png', 'instruction': 'white scalloped cuff'}})
            graph = graphs[0]
            files = [n['inputs']['lora_name'] for n in graph.values() if n['class_type'] == 'LoraLoader']
            self.assertEqual(files, ['style', 'arknights/same.safetensors'])
            self.assertEqual(sum(n['class_type'] == 'KSampler' for n in graph.values()), 1)
            self.assertEqual(graph['7']['inputs']['images'], ['214', 0])
            self.assertEqual(graph['214']['inputs']['mask'], ['212', 0])
            self.assertEqual(graph['211']['inputs']['image'], 'crossed-cuff.png')
            self.assertIn('white scalloped cuff', graph['2']['inputs']['text'])
            self.assertIn('blue hair', graph['2']['inputs']['text'])
            self.assertNotIn('red hair', graph['2']['inputs']['text'])
            self.assertNotIn('two people', graph['2']['inputs']['text'])
            batch = comfyui.drawing_state(repo, 'test')['batches'][-1]
            self.assertEqual(batch['loras'][1]['target_character_ids'], ['C002'])
            self.assertEqual(batch['loras'][1]['character_id'], 'saved-b')
            self.assertEqual(batch['character_lora_passes'], [])
        self.assertEqual(self.loras, original)

    def test_local_repair_rejects_owner_outside_panel_before_starting_models(self):
        panel = {**self.panel, 'characters': [{'character_id': 'C001'}]}
        for owner in ('C002', 'saved-a', '', None, ['C001']):
            with self.subTest(owner=owner), self.assertRaisesRegex(ValueError, '当前画面'):
                lb.repair_owner(self.p, panel, owner)

    def test_repairing_other_person_next_keeps_original_role_models(self):
        from PIL import Image
        self.p['pages'] = {'P001': {'panels': [self.panel]}}
        source_bank = self.resolved()
        source_copy = copy.deepcopy(source_bank)
        batch = {'panel_id': self.panel['id'], 'panel_hash': e.digest(self.panel), 'checkpoint': 'test',
            'loras': [source_bank[0], source_bank[2]],
            'render_options': {'local_repair': {'owner': 'C002', 'source_loras': source_bank}},
            'images': [{'id': 'keqing-repaired', 'file': 'original.png'}]}
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp); (folder/'images').mkdir()
            Image.new('RGB', (100,100), 'white').save(folder/'images/original.png')
            app = Mock(); app.repo.get.return_value = self.p; app.repo.folder.return_value = folder
            with patch.object(drawing_tools, 'DATA', folder/'data'), \
                 patch.object(comfyui, 'drawing_state', return_value={'batches': [batch]}), \
                 patch.object(drawing_tools.creative, 'structured', side_effect=AssertionError('No automatic rewrite')) as rewrite, \
                 patch.object(comfyui, 'run_batch', return_value={'id': 'next'}) as run, \
                 patch.object(drawing_tools, 'review_batch') as review:
                drawing_tools.local_repair(app, 'project', {'base_hash': e.digest(self.p), 'image': 'keqing-repaired',
                    'owner': 'C001', 'rect': [.2,.2,.6,.6], 'instruction': '修复甲的袖口'}, Mock(), 'next-owner')
            passed = run.call_args.args[7]
            options = run.call_args.kwargs['render_options']
            self.assertEqual(options['local_repair']['instruction'], '修复甲的袖口')
            self.assertFalse(run.call_args.kwargs['review_required'])
            app.prepare_language.assert_not_called()
            rewrite.assert_not_called()
            review.assert_not_called()
            self.assertEqual(passed, source_copy)
            self.assertEqual(options['local_repair']['owner'], 'C001')
            self.assertEqual(options['local_repair']['source_loras'], source_copy)
            selected, passes = lb.plan(passed, self.p, self.panel, self.payload, owner='C001')
            self.assertEqual([l['file'] for l in selected], ['style','genshin/same.safetensors'])
            self.assertEqual(passes, [])
        self.assertEqual(source_bank, source_copy)

    def test_full_paths_and_partial_label_saves_preserve_other_folders(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(comfyui,"DATA",Path(tmp)), \
             patch.object(comfyui,"discover",return_value={"loras":[l["file"] for l in self.loras]}), \
             patch("comic_workflow.characters.CharacterLibrary.listing",return_value=self.p["brief"]["character_refs"]):
            for item in self.loras[1:]:
                comfyui.save_lora_labels([{**item,"name":"同名角色文件"}])
            labels=comfyui.lora_labels()
            self.assertEqual(len(labels),2)
            self.assertEqual(labels["genshin/same.safetensors"]["character_id"],"saved-a")
            self.assertEqual(labels["arknights/same.safetensors"]["character_id"],"saved-b")
            self.assertEqual(len(list((Path(tmp)/"lora-label-history").glob("*.json"))),1)

    def test_all_render_options_use_same_routing_before_submission(self):
        self.p["pages"]={"P001":{"panels":[self.panel]}}
        self.p["drawing_prompts"]={self.panel["id"]:{"panel_hash":e.digest(self.panel),
            "identity_hash":identity_hash(self.p,self.panel),"payload":self.payload}}
        options = [{},{"diagnostic":"small_batch"},{"diagnostic":"small_batch","direct_prompt":True},
                   {"local_repair":{"original":"original.png","mask":"mask.png","instruction":"fix"}}]
        cases = [(option, saved_separator, backend_separator) for option in options
                 for saved_separator, backend_separator in [("/", "/"), ("/", "\\"), ("\\", "/")]]
        for option, saved_separator, backend_separator in cases:
            with self.subTest(option=option, saved=saved_separator, backend=backend_separator),tempfile.TemporaryDirectory() as tmp:
                repo=Mock();repo.get.return_value=self.p;repo.folder.return_value=Path(tmp)
                selected = [{**l, "file": l["file"].replace("/", saved_separator)} for l in self.loras]
                original = copy.deepcopy(selected)
                graphs=[]
                def capture(path,body=None):
                    if path=="/prompt":graphs.append(body["prompt"])
                    raise ValueError("offline capture")
                with patch.object(comfyui,"config",return_value={**self.cfg,"checkpoint":"test","endpoint":"http://127.0.0.1:8188"}), \
                     patch.object(comfyui,"discover",return_value={"ready":True,"checkpoints":["test"],"loras":[l["file"].replace("/", backend_separator) for l in self.loras]}), \
                     patch.object(comfyui,"api",side_effect=capture),patch.object(comfyui,"lora_labels",return_value={}), \
                     patch.object(comfyui,"ComfyProgress"),patch("comic_workflow.framing.apply_camera",side_effect=lambda p,n,_:(p,n)):
                    with self.assertRaisesRegex(ValueError,"offline capture"):
                        comfyui.run_batch(repo,"test",self.panel["id"],Mock(),"test",1,loras=selected,
                                          render_options={"reference":False,"control":False,**option})
                self.assertEqual(len(graphs),1)
                self.assertEqual(graphs[0]["301"]["inputs"]["lora_name"],"genshin/same.safetensors".replace("/", backend_separator))
                self.assertEqual(graphs[0]["341"]["inputs"]["lora_name"],"arknights/same.safetensors".replace("/", backend_separator))
                batch = comfyui.drawing_state(repo, "test")["batches"][-1]
                self.assertEqual([l["file"] for l in batch["loras"]], [l["file"] for l in self.loras])
                self.assertEqual(selected, original)

    def test_native_private_loras_keep_pose_control_after_conditioning_combine(self):
        self.p['pages']={'P001':{'panels':[self.panel]}}
        self.p['drawing_prompts']={self.panel['id']:{'panel_hash':e.digest(self.panel),
            'identity_hash':identity_hash(self.p,self.panel),'payload':self.payload}}
        graphs=[]
        def capture(path,body=None):
            if path=='/prompt':graphs.append(body['prompt'])
            raise ValueError('offline capture')
        def control(graph,guide):
            self.assertEqual(guide,'explicit-pose.png')
            graph['203']={'class_type':'ControlNetApplyAdvanced','inputs':{
                'positive':graph['5']['inputs']['positive'],
                'negative':graph['5']['inputs']['negative']}}
            graph['5']['inputs'].update(positive=['203',0],negative=['203',1])
        with tempfile.TemporaryDirectory() as tmp:
            repo=Mock();repo.get.return_value=self.p;repo.folder.return_value=Path(tmp)
            with patch.object(comfyui,'config',return_value={**self.cfg,'regional_lora_hooks':True,'checkpoint':'test','endpoint':'http://127.0.0.1:8188'}), \
                 patch.object(comfyui,'discover',return_value={'ready':True,'checkpoints':['test'],'loras':[l['file'] for l in self.loras]}), \
                 patch.object(comfyui,'api',side_effect=capture),patch.object(comfyui,'lora_labels',return_value={}), \
                 patch.object(comfyui,'ComfyProgress'),patch('comic_workflow.framing.apply_camera',side_effect=lambda p,n,_:(p,n)), \
                 patch('comic_workflow.framing.guide_image',return_value='explicit-pose.png'), \
                 patch('comic_workflow.framing.apply_control',side_effect=control):
                with self.assertRaisesRegex(ValueError,'offline capture'):
                    comfyui.run_batch(repo,'test',self.panel['id'],Mock(),'test',1,loras=self.loras,
                                      render_options={'reference':False,'control':True})
            graph=graphs[0]
            self.assertEqual(graph['5']['inputs']['positive'],['203',0])
            self.assertEqual(graph['203']['inputs']['positive'],['297',0])
            self.assertEqual(sum(n['class_type']=='KSampler' for n in graph.values()),1)
            self.assertEqual(graph['300']['inputs']['lora_name'],'genshin/same.safetensors')
            self.assertEqual(graph['340']['inputs']['lora_name'],'arknights/same.safetensors')


if __name__ == "__main__":
    unittest.main()
