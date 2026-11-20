import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from comic_workflow import engine as e
from comic_workflow.repository import Repository
from comic_workflow.project_translation import translate, text_leaves


class ProjectTranslationTests(unittest.TestCase):
    def test_translation_preserves_ids_source_and_continuity(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Repository(Path(temporary))
            pid = repo.create(None, True)
            p = repo.get(pid)
            p['script'] = '第一段。\n\n第二段。'
            p['script_source'] = {'original':p['script']}
            e.save(repo.folder(pid)/'project.json', p)
            app = SimpleNamespace(repo=repo, prepare_language=Mock(), model=Mock())
            def translated(prompt, context, *args, **kwargs):
                return {key:'English '+e.digest(value) for key,value in context['texts'].items()}, {}
            with patch('comic_workflow.project_translation.creative.structured', side_effect=translated):
                translate(app,pid,{'base_hash':e.digest(p)},lambda _:None,'test')
            q = repo.get(pid)
            e.validate_project(q,complete=True)
            self.assertEqual(list(q['pages']),list(p['pages']))
            for key in p['pages']:
                self.assertEqual([x['id'] for x in q['pages'][key]['panels']],
                                 [x['id'] for x in p['pages'][key]['panels']])
            self.assertEqual(q['script_source'],p['script_source'])
            self.assertEqual(q['script'].count('\n\n'),1)
            self.assertEqual(list(text_leaves(q['pages'])),[])
            self.assertEqual(e.read(repo.folder(pid)/'history'/f"v{p['version']:06}.json"),p)
            app.model.stop.assert_called_once()

