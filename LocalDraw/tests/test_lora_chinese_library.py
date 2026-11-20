import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import sync_lora_chinese_library as sync
from comic_workflow import engine as e
from comic_workflow.characters import CharacterLibrary
from comic_workflow import lora_binding


class ChineseLibraryTests(unittest.TestCase):
    def test_migration_preserves_weights_triggers_aliases_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); lib=root/'models/loras'; out=root/'data/lora-cn-library'
            config=root/'config/lora-chinese-identities.json'; state=root/'data/character-expansion-20261031/status.json'
            old='anime/热门角色/Nina-v1.safetensors'; style='sdxl/原神/手部修复_original.safetensors'
            for relative in (old,style):
                p=lib/relative;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(relative.encode())
            digest=hashlib.sha256((lib/old).read_bytes()).hexdigest()
            e.save(config,{'models':{'9':{'name':'井芹仁菜','kind':'character','basis':'test'}},'versions':{},'names':{}})
            e.save(state,{'files':{'one':{'relative_path':old,'name':'Nina','title':'Nina','model_id':9,'version_id':100}}})
            e.save(root/'data/lora-labels.json',{old:{'trigger_words':'iseri nina, red shirt','weight':.7}})
            library=CharacterLibrary(root/'data/characters')
            with patch.multiple(sync,ROOT=root,LIB=lib,OUT=out,STATE=state,CONFIG=config), \
                 patch.object(sync,'CharacterLibrary',return_value=library):
                plan=sync.plan();self.assertEqual(plan['pending'],[])
                result=sync.apply_plan(plan)
                self.assertEqual(result['bound_files'],2)
                labels=e.read(root/'data/lora-labels.json'); aliases=e.read(root/'data/lora-curation/rename-result.json')['mapping']
                new=aliases[old]
                self.assertEqual(hashlib.sha256((lib/new).read_bytes()).hexdigest(),digest)
                self.assertEqual(labels[new]['trigger_words'],'iseri nina, red shirt')
                self.assertEqual(labels[new]['character_id'],library.get('井芹仁菜')['id'])
                self.assertEqual(labels[aliases[style]]['scope'],'global')
                self.assertEqual(e.read(state)['files']['one']['relative_path'],new)
                self.assertTrue(all(r['old']==r['new'] for r in sync.plan()['files']))
                second=sync.apply_plan(sync.plan())
                self.assertEqual(second['created_cards'],[])
                self.assertEqual(second['renamed_files'],0)

    def test_resource_owner_does_not_require_person_in_panel(self):
        item={'character_id':'style-card','character':'手部修复','scope':'global'}
        self.assertIsNone(lora_binding.targets(item,{},{}))
        self.assertEqual(lora_binding.binding(item)['character_id'],'style-card')

    def test_pending_label_and_interrupted_state_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); lib=root/'models/loras'; out=root/'data/lora-cn-library'
            config=root/'config/lora-chinese-identities.json'; state=root/'data/status.json'
            old='anime/test/Nina.safetensors'; new='anime/test/井芹仁菜_版本100.safetensors'
            pending='anime/test/Unknown.safetensors'
            for relative in (new,pending):
                path=lib/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'preserved')
            e.save(config,{'models':{},'versions':{},'names':{}})
            e.save(root/'data/lora-curation/rename-result.json',{'mapping':{old:new}})
            e.save(root/'data/lora-labels.json',{
                new:{'name':'井芹仁菜','chinese_identity_verified':True},
                pending:{'name':'Unknown'}})
            e.save(state,{'files':{
                'one':{'relative_path':old,'name':'Nina','model_id':9,'version_id':100,'group':'热门角色'},
                'two':{'relative_path':pending,'name':'Unknown','model_id':10,'version_id':101,'group':'热门角色'}}})
            library=CharacterLibrary(root/'data/characters')
            with patch.multiple(sync,ROOT=root,LIB=lib,OUT=out,STATE=state,CONFIG=config), patch.object(sync,'CharacterLibrary',return_value=library):
                report=sync.apply_plan(sync.plan())
                self.assertEqual(len(report['pending']),1)
                rows=e.read(state)['files']
                self.assertEqual(rows['one']['relative_path'],new)
                self.assertEqual(rows['one']['character_id'],library.get('井芹仁菜')['id'])
                self.assertNotIn('character_id',rows['two'])
                self.assertEqual((lib/pending).read_bytes(),b'preserved')

    def test_character_old_name_alias_survives(self):
        with tempfile.TemporaryDirectory() as tmp:
            library=CharacterLibrary(tmp)
            card=library.save('机械师',sync.profile('机械师','character'),{'mode':'text','aliases':['Mechanist']})
            self.assertEqual(library.get('Mechanist')['id'],card['id'])
            self.assertEqual(library.resolve('/Mechanist ')[0]['id'],card['id'])

    def test_collection_version_identity_wins(self):
        spec=sync.resolve(Path('anything.safetensors'),{}, {'model_id':9,'version_id':10},
            {'models':{'9':{'name':'角色合集','kind':'collection'}},'versions':{'10':{'name':'知更鸟','kind':'character'}}})
        self.assertEqual(spec['name'],'知更鸟')

    def test_config_and_verified_label_use_canonical_identity(self):
        cfg={'versions':{'10':{'name':'若娜瓦','kind':'character'}},
             'names':{'若娜瓦':'罗诺瓦'}}
        self.assertEqual(sync.resolve(Path('x.safetensors'),{},
            {'model_id':9,'version_id':10},cfg)['name'],'罗诺瓦')
        self.assertEqual(sync.resolve(Path('x.safetensors'),
            {'name':'若娜瓦','chinese_identity_verified':True},None,cfg)['name'],'罗诺瓦')


if __name__=='__main__':unittest.main()
