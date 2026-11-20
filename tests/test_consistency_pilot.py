"""Fail-closed production gates; these tests never generate or visually approve images."""
import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import consistency_pilot as pilot


class PilotGates(unittest.TestCase):
    def setUp(self):
        self.manifest=[{'image_id':str(i),'frontend_original_verified':True} for i in range(4)]
        self.review={'group_verdict':'pass','all_candidates_viewed':True,'records':[
            {'image_id':str(i),'visual_verdict':'pass','issues':[],
             'inspection':'Test fixture, not a real visual review.','evidence':{'script':'fixture'}} for i in range(4)]}

    def test_group_flag_does_not_replace_four_reviews(self):
        self.review.pop('records')
        with self.assertRaises(ValueError): pilot.validate_visual_pass(self.review,self.manifest)

    def test_review_of_different_or_duplicate_image_rejected(self):
        for wrong in ['other','0']:
            review=copy.deepcopy(self.review);review['records'][3]['image_id']=wrong
            with self.assertRaises(ValueError): pilot.validate_visual_pass(review,self.manifest)

    def test_uncertain_or_uninspected_candidate_blocks_group(self):
        for field,value in [('visual_verdict','uncertain'),('inspection',''),('evidence',{})]:
            review=copy.deepcopy(self.review);review['records'][0][field]=value
            with self.assertRaises(ValueError): pilot.validate_visual_pass(review,self.manifest)

    def test_missing_frontend_original_blocks_release(self):
        self.manifest[2]['frontend_original_verified']=False
        with self.assertRaises(ValueError): pilot.validate_visual_pass(self.review,self.manifest)

    def test_complete_fixture_passes_structural_gate_only(self):
        pilot.validate_visual_pass(self.review,self.manifest)

    def test_unknown_submission_never_retries(self):
        with patch.object(pilot,'load',return_value={'pending_submission':{'bundle':'unknown'}}), \
             patch.object(pilot,'check_pack',return_value={}), \
             patch.object(pilot.h,'idle'),patch.object(pilot.h,'act') as action:
            with self.assertRaises(ValueError): pilot.submit()
            action.assert_not_called()

    def test_calibration_cannot_repeat_as_unbounded_reroll(self):
        with patch.object(pilot,'load',return_value={'jobs':[{},{}]}), \
             patch.object(pilot,'check_pack',return_value={}), \
             patch.object(pilot.h,'idle'),patch.object(pilot.h,'act') as action:
            with self.assertRaises(ValueError): pilot.calibrate()
            action.assert_not_called()


if __name__=='__main__': unittest.main()
