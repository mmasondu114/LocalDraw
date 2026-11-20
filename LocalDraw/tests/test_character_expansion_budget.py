"""Exercise the reservation boundary shared by all download worker threads."""
import importlib.util
import sys
import tempfile
import unittest
import io
from pathlib import Path
from unittest.mock import patch
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import download_character_expansion as d


class BudgetTests(unittest.TestCase):
    def test_network_recovery_only_retries_reserved_network_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            state={'reserved_bytes':d.LIMIT-100,'limit_bytes':d.LIMIT,'files':{
                'network':{'status':'failed','last_error':'URLError'},
                'structure':{'status':'failed','last_error':'ValueError'},
                'range':{'status':'failed','last_error':'HTTPError HTTP 416'},
                'complete':{'status':'verified','last_error':'URLError'}}}
            d.e.save(root/'status.json',state)
            with patch.object(d,'OUT',root), patch.object(d,'STATE',{}), \
                 patch.object(d.sys,'argv',['worker','--retry-network-only']), \
                 patch.object(d,'run_rows') as run, patch.object(d,'reserve') as reserve:
                d.main()
                self.assertEqual(run.call_args.args[0],[state['files']['network']])
                reserve.assert_not_called()
                result=d.e.read(root/'status.json')
                self.assertEqual(result['reserved_bytes'],state['reserved_bytes'])
                self.assertEqual(result['network_recovery']['count'],1)

    def test_band_search_rejects_shared_surname(self):
        for title in ['Hinata Wakaba / Rival Schools', 'Misumi Tozawa - Sword Art Online', 'Makoto Misumi', 'typhoeus']:
            self.assertFalse(d.band_matches({'title':title}, 'Ave Mujica'))
        for title in ['[Anima] Mutsumi Wakaba 若葉睦 | BanG Dream! Ave Mujica', 'Misumi Uika', 'Ave Mujiac X Killer Tune']:
            self.assertTrue(d.band_matches({'title':title}, 'Ave Mujica'))
        self.assertFalse(d.band_matches({'title':'Rupa unrelated character'}, 'Girls Band Cry'))
        self.assertTrue(d.band_matches({'title':'Rupa', 'triggers':['rupa (girls_band_cry)']}, 'Girls Band Cry'))

    def test_nullable_platform_popularity(self):
        def model(identifier, stats, version_stats):
            return dict(id=identifier, name='Test character', type='LORA', tags=['character'], stats=stats,
                modelVersions=[dict(id=identifier, name='v1', baseModel='Anima', stats=version_stats,
                    files=[dict(type='Model', name='test.safetensors', sizeKB=10,
                        hashes={'SHA256':'a'*64}, downloadUrl='https://example.com/model')])])
        rows = d.candidates([model(1, {'downloadCount':None}, {'downloadCount':None}),
                             model(2, {'downloadCount':20}, {'downloadCount':10}),
                             model(3, None, None)])
        self.assertEqual([r['model_id'] for r in rows], [2,1,3])
        self.assertEqual(rows[1]['popularity'], 0)
        self.assertEqual(rows[2]['version_popularity'], 0)

    def test_oversize_response_never_writes_past_reservation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            response = io.BytesIO(b'x'*101)
            response.status = 200
            response.headers = {}
            opener = Mock()
            opener.open.return_value = response
            row = dict(relative_path='test.safetensors', bytes=100,
                       url='https://example.com/test', name='oversize')
            with patch.object(d, 'LIB', root), patch.object(d, 'save_state'), \
                 patch.object(d.urllib.request, 'build_opener', return_value=opener):
                d.download(row)
            self.assertEqual(row['status'], 'failed')
            self.assertFalse((root/'test.safetensors').exists())
            self.assertLessEqual((root/'test.safetensors.partial').stat().st_size, 100)

    def test_limit_and_dedup(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(d, 'OUT', Path(directory)), patch.object(d, 'LIB', Path(directory)), \
                 patch.object(d, 'STATE', {'reserved_bytes': d.LIMIT-100, 'files': {}}), \
                 patch.object(d, 'KNOWN', {}):
                c = dict(sha256='a'*64, bytes=101, base_model='Anima', version_id=1)
                self.assertIsNone(d.reserve(c, 'test', 'too large'))
                c['bytes'] = 100
                self.assertIsNotNone(d.reserve(c, 'test', 'exact fit'))
                self.assertEqual(d.STATE['reserved_bytes'], d.LIMIT)
                self.assertIsNone(d.reserve(c, 'test', 'duplicate'))
                self.assertEqual(len(d.STATE['files']), 1)

    def test_installed_hash_does_not_use_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'existing.safetensors').write_bytes(b'x')
            with patch.object(d, 'OUT', root), patch.object(d, 'LIB', root), \
                 patch.object(d, 'STATE', {'reserved_bytes': 0, 'files': {}}), \
                 patch.object(d, 'KNOWN', {'a'*64:'existing.safetensors'}):
                self.assertIsNone(d.reserve(dict(sha256='a'*64, bytes=1), 'test', 'duplicate'))
                self.assertEqual(d.STATE['reserved_bytes'], 0)


if __name__ == '__main__': unittest.main()
