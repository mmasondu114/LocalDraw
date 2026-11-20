import copy
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from PIL import Image
from comic_workflow import anima_consistency as ac, prompt_models, comfyui, quick_sample
from comic_workflow.repository import Repository


class ConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name, value in [('ROOT', self.root), ('DATA', self.root/'data')]:
            p = patch.object(ac, name, value); p.start(); self.addCleanup(p.stop)
        file = self.root/'models/loras'/ac.LORA
        file.parent.mkdir(parents=True); file.write_bytes(b'test')
        self.source = self.root/'doctor.png'
        Image.new('RGB', (32, 32), 'red').save(self.source)
        self.library = SimpleNamespace(image_path=lambda _: self.source)
        self.project = {'brief': {'character_refs': [{'id':'card', 'name':'Doctor', 'revision':1,
            'profile':{}, 'image_id':'source'}]}, 'canon': {'characters':[{'id':'C1','name':'Doctor'}]}}
        self.panel = {'characters':[{'character_id':'C1'}]}
        self.cfg = {'consistency_lora':True, 'width':768, 'height':1024, **prompt_models.defaults(ac.CHECKPOINT)}
        self.files = {ac.LORA: ac.LORA.replace('/', '\\')}

    def prepare(self, **kw):
        args = dict(project=self.project, panel=self.panel, checkpoint=ac.CHECKPOINT,
                    cfg=self.cfg, options={}, lora_files=self.files, library=self.library)
        args.update(kw)
        return ac.prepare(**args)

    def test_original_reference_and_existing_loras_reach_sampler_without_text_changes(self):
        record = self.prepare()
        graph = prompt_models.graph(ac.CHECKPOINT, 'exact prompt', 'exact negative', 42, self.cfg,
                                   [{'file':'anime/character.safetensors','weight':0.6}])
        ac.apply(graph, record, self.cfg)
        self.assertEqual(graph['consistency-lora']['inputs']['model'], ['20',0])
        self.assertEqual(graph['consistency-lora']['inputs']['lora_name'], self.files[ac.LORA])
        self.assertEqual(graph['5']['inputs']['model'], ['consistency-reference',0])
        self.assertEqual(graph['consistency-reference']['inputs']['ref_latents.ref_latent_1'], ['consistency-latent',0])
        self.assertEqual(graph['2']['inputs']['text'], 'exact prompt')
        self.assertEqual(graph['3']['inputs']['text'], 'exact negative')
        self.assertEqual(graph['5']['inputs']['seed'], 42)
        self.assertEqual((ac.DATA/'comfy-input'/record['reference']).read_bytes(), self.source.read_bytes())

    def test_explicit_off_other_models_and_diagnostics_skip(self):
        for kwargs in [dict(options={'consistency_lora':False}), dict(checkpoint='sdxl.safetensors'),
                       dict(options={'external_prompt':True}), dict(options={'diagnostic':'small_batch'})]:
            with self.subTest(kwargs=kwargs): self.assertFalse(self.prepare(**kwargs)['enabled'])
        p = copy.deepcopy(self.project)
        p['canon']['characters'].append({'id':'C2','name':'Other'})
        multi = self.prepare(project=p, panel={'characters':[{'character_id':'C1'},{'character_id':'C2'}]})
        self.assertTrue(multi['enabled'])
        self.assertNotIn('reference', multi)
        p['brief']['character_refs'][0].pop('image_id')
        missing = self.prepare(project=p)
        self.assertTrue(missing['enabled'])
        self.assertNotIn('reference', missing)
        self.assertTrue(self.prepare(project={**self.project,'quick_sample':True},options={'diagnostic':'small_batch'})['enabled'])

    def test_imported_explicit_consistency_loads_lora_without_implicit_image(self):
        self.source.unlink()
        record = self.prepare(options={'external_prompt':True, 'consistency_lora':True})
        graph = prompt_models.graph(ac.CHECKPOINT, 'literal prompt', 'negative', 7, self.cfg)
        ac.apply(graph, record, self.cfg)
        self.assertTrue(record['enabled'])
        self.assertEqual(graph['5']['inputs']['model'], ['consistency-lora',0])
        self.assertNotIn('reference', record)
        self.assertEqual(graph['2']['inputs']['text'], 'literal prompt')
        self.assertFalse(any(n['class_type'] in ('LoadImage','VAEEncode','ApplyCosmosReferenceLatent') for n in graph.values()))

    def test_no_character_still_loads_lora_without_reference_nodes(self):
        record = self.prepare(panel={'characters':[]})
        self.assertTrue(record['enabled'])
        self.assertEqual(record['reference_mode'],'none')
        graph = prompt_models.graph(ac.CHECKPOINT,'scene only','n',5,self.cfg)
        ac.apply(graph,record,self.cfg)
        self.assertEqual(graph['5']['inputs']['model'],['consistency-lora',0])
        self.assertEqual(graph['consistency-lora']['inputs']['strength_model'],0.8)
        self.assertFalse(any(n['class_type'] in ('LoadImage','VAEEncode','ApplyCosmosReferenceLatent') for n in graph.values()))

    def test_prompt_reference_opt_out_never_reads_image_or_adds_reference_nodes(self):
        self.source.unlink()  # Unchecked references must not even read the file.
        for global_choice, character_choice in [(False, False), (False, True),
                                                 (None, True), (True, False), (True, None)]:
            with self.subTest(global_choice=global_choice, character_choice=character_choice):
                project = {**self.project, 'quick_sample': True, 'prompt_input': {
                    'use_reference': global_choice,
                    'characters': [{'name': 'Doctor', 'use_reference': character_choice}]}}
                record = self.prepare(project=project, options={'diagnostic': 'small_batch'})
                graph = prompt_models.graph(ac.CHECKPOINT, 'exact prompt', 'negative', 42, self.cfg)
                ac.apply(graph, record, self.cfg)
                self.assertTrue(record['enabled'])
                self.assertEqual(record['reference_mode'], 'none')
                self.assertNotIn('reference', record)
                self.assertEqual(graph['5']['inputs']['model'], ['consistency-lora', 0])
                self.assertFalse(any(n['class_type'] in ('LoadImage', 'VAEEncode', 'ApplyCosmosReferenceLatent')
                                     for n in graph.values()))
                self.assertEqual(graph['2']['inputs']['text'], 'exact prompt')
        self.assertFalse((ac.DATA/'comfy-input'/'consistency').exists())

    def test_prompt_reference_requires_both_explicit_selections(self):
        project = {**self.project, 'quick_sample': True, 'prompt_input': {
            'use_reference': True, 'characters': [{'name': 'Doctor', 'use_reference': True}]}}
        record = self.prepare(project=project, options={'diagnostic': 'small_batch'})
        self.assertEqual(record['reference_mode'], 'fixed_character_original')
        self.assertEqual((ac.DATA/'comfy-input'/record['reference']).read_bytes(), self.source.read_bytes())

    def test_imported_reference_is_frozen_and_preserves_aspect_ratio(self):
        from comic_workflow import engine as e
        repo = Repository(self.root/'projects')
        self.addCleanup(repo._temporary.cleanup)
        pid = repo.create(None, True)
        folder = repo.folder(pid)/'images'
        folder.mkdir(exist_ok=True)
        source = folder/'reference.png'
        Image.new('RGB',(512,1024),'red').save(source)
        e.save(folder/'index.json',{'batches':[{'images':[{'id':'ref','file':'reference.png'}]}]})
        frozen = ac.freeze_reference(repo,pid,'ref')
        source.unlink()  # Later changes to the candidate do not alter a queued job.
        record = self.prepare(options={'external_prompt':True,'consistency_reference':frozen})
        graph = prompt_models.graph(ac.CHECKPOINT,'literal prompt','negative',7,self.cfg)
        ac.apply(graph,record,self.cfg)
        self.assertEqual(graph['2']['inputs']['text'],'literal prompt')
        self.assertEqual(graph['5']['inputs']['model'],['consistency-reference',0])
        self.assertEqual(graph['consistency-scale']['inputs']['width'],512)
        self.assertEqual(graph['consistency-scale']['inputs']['height'],1024)
        self.assertEqual(graph['consistency-scale']['inputs']['crop'],'disabled')
        self.assertEqual(graph['consistency-pad']['inputs']['left'],128)
        self.assertEqual(graph['consistency-pad']['inputs']['right'],128)
        self.assertEqual(graph['consistency-latent']['inputs']['pixels'],['consistency-pad',0])
        (ac.DATA/'comfy-input'/frozen['reference']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'已变化'):
            self.prepare(options={'external_prompt':True,'consistency_reference':frozen})

    def test_missing_assets_fail_instead_of_claiming_consistency(self):
        with self.assertRaisesRegex(ValueError,'文件未就绪'): self.prepare(lora_files={})
        self.source.unlink()
        with self.assertRaisesRegex(ValueError,'参考图不存在'): self.prepare()

    def test_explicit_character_reference_in_group_is_frozen_and_absent_cast_is_rejected(self):
        project = copy.deepcopy(self.project)
        project['canon']['characters'].append({'id': 'C2', 'name': 'Other'})
        panel = {'characters': [{'character_id': 'C1'}, {'character_id': 'C2'}]}
        prefs = {'reference_character_id': 'C1', 'consistency_lora': True}
        selection = ac.selected_character_reference(project, panel, ac.CHECKPOINT, prefs, library=self.library)
        frozen = ac.freeze_character_reference(selection)
        self.source.unlink()
        record = self.prepare(project=project, panel=panel,
            options={'external_prompt': True, 'consistency_reference': frozen})
        graph = prompt_models.graph(ac.CHECKPOINT, 'Two colleagues in an office.', '', 3, self.cfg)
        ac.apply(graph, record, self.cfg)
        self.assertEqual(record['character_id'], 'C1')
        self.assertEqual(record['reference_fit'], 'contain_pad')
        self.assertEqual(graph['5']['inputs']['model'], ['consistency-reference', 0])
        self.assertEqual(graph['2']['inputs']['text'], 'Two colleagues in an office.')
        absent = {'characters': [{'character_id': 'C2'}]}
        self.assertIsNone(ac.selected_character_reference(project, absent, ac.CHECKPOINT, prefs, library=self.library))
        with self.assertRaisesRegex(ValueError, '未出镜'):
            self.prepare(project=project, panel=absent,
                options={'external_prompt': True, 'consistency_reference': frozen})

    def test_selected_character_reference_fails_for_disabled_or_incompatible_model(self):
        prefs = {'reference_character_id': 'C1', 'consistency_lora': True}
        with self.assertRaisesRegex(ValueError, 'Delta v6'):
            ac.selected_character_reference(self.project, self.panel, 'other', prefs, library=self.library)
        with self.assertRaisesRegex(ValueError, '开启'):
            ac.selected_character_reference(self.project, self.panel, ac.CHECKPOINT,
                {**prefs, 'consistency_lora': False}, library=self.library)

    def test_manual_edit_lora_is_not_loaded_twice(self):
        record = self.prepare()
        graph = prompt_models.graph(ac.CHECKPOINT,'p','n',2,self.cfg,
                                   [{'file':self.files[ac.LORA],'weight':0.55}])
        ac.apply(graph,record,self.cfg)
        self.assertNotIn('consistency-lora',graph)
        self.assertEqual(record['weight'],0.55)
        self.assertEqual(graph['consistency-reference']['inputs']['model'],['20',0])

    def test_prompt_session_saves_default_and_explicit_off(self):
        repo = Repository(self.root/'projects')
        self.addCleanup(repo._temporary.cleanup)
        with patch.object(comfyui,'config',return_value=self.cfg), patch.object(comfyui,'discover',return_value={'checkpoints':[ac.CHECKPOINT]}):
            for supplied, expected in [({},True),({'consistency_lora':False},False)]:
                pid = quick_sample.create(repo,{'text':'mountains','checkpoint':ac.CHECKPOINT,**supplied})['project']
                self.assertIs(repo.get(pid)['prompt_input']['consistency_lora'], expected)
                repo.release_transient(pid)

    def test_normal_batch_submits_reference_and_records_actual_loading(self):
        from tests.test_comfyui import DrawingTests
        from comic_workflow import engine as e
        from comic_workflow.character_binding import identity_hash
        fixture = DrawingTests(); fixture.setUp()
        self.addCleanup(fixture.doCleanups); self.addCleanup(fixture.tmp.cleanup)
        p = fixture.repo.get(fixture.pid)
        panel = p['pages']['P001']['panels'][0]
        panel['characters'] = panel['characters'][:1]
        char = next(c for c in p['canon']['characters'] if c['id'] == panel['characters'][0]['character_id'])
        p['brief']['character_refs'] = [{'id':'card','name':char['name'],'revision':1,'profile':{},'image_id':'source'}]
        prompt = p['drawing_prompts'][fixture.target]
        prompt.update(panel_hash=e.digest(panel),identity_hash=identity_hash(p,panel))
        e.save(fixture.repo.folder(fixture.pid)/'project.json',p)
        fixture.prompts=[]
        def api(path, body=None, timeout=10):
            if path == '/object_info/ApplyCosmosReferenceLatent': return {'ApplyCosmosReferenceLatent':{}}
            return fixture.mock_api(path,body,timeout)
        raw=io.BytesIO(); Image.new('RGB',(32,32),'white').save(raw,format='PNG')
        class Response:
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self,*args): return raw.getvalue()
        class Opener:
            def open(self,*args,**kwargs): return Response()
        with patch.object(comfyui,'discover',return_value={'ready':True,'checkpoints':[ac.CHECKPOINT],'lora_files':self.files}), \
             patch.object(comfyui,'api',api), \
             patch('comic_workflow.characters.CharacterLibrary',return_value=self.library), \
             patch('comic_workflow.telemetry.websocket.create_connection',side_effect=ConnectionError('test')), \
             patch('urllib.request.build_opener',return_value=Opener()):
            result=comfyui.run_batch(fixture.repo,fixture.pid,fixture.target,lambda _:None,ac.CHECKPOINT,count=1)
        self.assertEqual(result['status'],'done')
        self.assertEqual(result['consistency']['loading'],'automatic')
        self.assertEqual(result['consistency']['reference_mode'],'fixed_character_original')
        self.assertEqual(fixture.prompts[0]['5']['inputs']['model'],['consistency-reference',0])
        self.assertNotIn('100',fixture.prompts[0])  # No SDXL IPAdapter attached.

        # Reproduce the prompt-page request: a selected character still has an
        # image in its frozen card, but both reference checkboxes are off.
        p.update(quick_sample=True, prompt_input={'use_reference': False,
            'characters': [{'name': char['name'], 'use_reference': False}]})
        e.save(fixture.repo.folder(fixture.pid)/'project.json', p)
        self.source.unlink()
        with patch.object(comfyui,'discover',return_value={'ready':True,'checkpoints':[ac.CHECKPOINT],'lora_files':self.files}), \
             patch.object(comfyui,'api',fixture.mock_api), \
             patch('comic_workflow.characters.CharacterLibrary',return_value=self.library), \
             patch('comic_workflow.telemetry.websocket.create_connection',side_effect=ConnectionError('test')), \
             patch('urllib.request.build_opener',return_value=Opener()):
            result=comfyui.run_batch(fixture.repo,fixture.pid,fixture.target,lambda _:None,ac.CHECKPOINT,count=1,
                render_options={'diagnostic':'small_batch','direct_prompt':True,
                                'consistency_lora':True,'reference':False,'control':False})
        self.assertEqual(result['status'], 'done')
        self.assertEqual(result['consistency']['reference_mode'], 'none')
        self.assertEqual(result['references'], [])
        self.assertEqual(fixture.prompts[-1]['5']['inputs']['model'], ['consistency-lora',0])
        self.assertFalse(any(n['class_type'] in ('LoadImage','VAEEncode','ApplyCosmosReferenceLatent')
                             for n in fixture.prompts[-1].values()))


if __name__ == '__main__': unittest.main()
