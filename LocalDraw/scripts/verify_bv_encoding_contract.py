"""Check text-slot isolation and mismatched prompt lengths with dummy tensors.

No AI model runs here. Actual CUDA renders are saved by verify_bv_regional_gpu.
"""
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
import torch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime/custom_nodes/bv_nodepack/py'))
from util.regional import sdxl_attention as backend


class DummyEncoder:
    def __init__(self,scope=0):
        self.scope=scope

    def tokenize(self,text):
        count=2 if 'long description' in text else 1
        return {'l':[[text]*77 for _ in range(count)],'g':[[text]*77 for _ in range(count)]}

    def encode_from_tokens_scheduled(self,tokens):
        count=max(len(chunks) for chunks in tokens.values())*77
        values=torch.full((1,count,3),float(self.scope))
        return [[values,{'pooled_output':torch.full((1,3),float(self.scope)),'hooks':'slot-only'}]]


def document():
    identifier=lambda text:str(uuid.uuid5(uuid.NAMESPACE_URL,'localdraw-clip-contract-'+text))
    return {'schema':'bv.regional','version':2,'document_id':identifier('document'),'title':'encoding check',
        'canvas':{'width':64,'height':48},'negative_mode':'auto','overlap':{'mode':'joint'},
        'prompts':{'global':{'positive_source':'shared scene','negative_source':'low quality'},
                   'background':{'positive_source':'','negative_source':''}},
        'regions':[{'id':identifier(str(i)),'name':str(i),'enabled':True,'usage':'generation','strength':1.0,'priority':i,
                    'prompts':{'positive_source':'long description '+str(i),'negative_source':'low quality'},
                    'mask':{'feather':0},'geometry':[{'id':identifier('geometry-'+str(i)),'type':'rect','operation':'add',
                        'x':(i-1)/2,'y':0,'width':.5,'height':1}]} for i in (1,2)]}


class EncodingContract(unittest.TestCase):
    def test_each_identity_gets_its_own_text_weights_without_leaking_model_hooks(self):
        def scoped_encoder(clip,hooks):return DummyEncoder(hooks) if hooks is not None else clip
        doc=document();hooks={r['id']:i for i,r in enumerate(doc['regions'],1)}
        with patch.object(backend,'clip_with_hooks',side_effect=scoped_encoder):
            pos,neg,slots,_=backend.compile_sdxl_attention(doc,DummyEncoder(),hooks)
        self.assertEqual([s.name for s in slots],['global','1','2'])
        for scope,slot in enumerate(slots):
            self.assertTrue(torch.all(slot.positive==scope))
            self.assertTrue(torch.all(slot.negative==scope))
        self.assertNotIn('hooks',pos[0][1]);self.assertNotIn('hooks',neg[0][1])

    def test_unequal_prompt_lengths_keep_the_same_attention_slot_layout(self):
        pos,neg,slots,_=backend.compile_sdxl_attention(document(),DummyEncoder())
        self.assertEqual(pos[0][0].shape,neg[0][0].shape)
        self.assertEqual([s.positive.shape[1] for s in slots],[77,154,154])
        self.assertEqual([s.negative.shape[1] for s in slots],[77,154,154])
        bias=backend.build_cross_attention_bias(slots,12,pos[0][0].shape[1],64/48,1.,torch.device('cpu'),torch.float32)
        self.assertEqual(bias.shape,(1,12,385))

    def test_no_hooks_retains_base_encoding(self):
        pos,neg,_,_=backend.compile_sdxl_attention(document(),DummyEncoder(9))
        self.assertTrue(torch.all(pos[0][0]==9));self.assertTrue(torch.all(neg[0][0]==9))


if __name__=='__main__':unittest.main()
