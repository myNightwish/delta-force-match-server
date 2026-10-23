import copy
import unittest
from pydantic import ValidationError
from app.schemas.recruitment_v4 import RecruitmentV4
from app.services.strength_range import tolerance_bounds
from test_recruitment_v4 import PLAN

class StrengthToleranceTests(unittest.TestCase):
    def profile(self,**patch):return RecruitmentV4.model_validate({**copy.deepcopy(PLAN),**patch}).profile()
    def test_default_and_selected_tolerance_recomputed_by_server(self):
        self.assertEqual((self.profile()['strength_min'],self.profile()['strength_max']),(70,130))
        plan=self.profile(strength_range_mode='percent',strength_tolerance_percent=45,strength_min=999,strength_max=999,strength_score=999)
        self.assertEqual((plan['strength_score'],plan['strength_min'],plan['strength_max']),(100,55,145))
    def test_zero_exact_match_and_maximum_supported_percentage(self):
        plan=self.profile(strength_range_mode='percent',strength_tolerance_percent=0)
        self.assertEqual((plan['strength_min'],plan['strength_max']),(100,100))
        plan=self.profile(strength_range_mode='percent',strength_tolerance_percent=100,confidential_kd=1000,top_secret_kd=1000,combat_score=1000000)
        self.assertEqual((plan['strength_min'],plan['strength_max']),(0,2000000000))
        plan=self.profile(strength_range_mode='percent',strength_tolerance_percent=100,combat_score=0)
        self.assertEqual((plan['strength_min'],plan['strength_max']),(0,0))
    def test_invalid_percentages_rejected(self):
        for value in (-1,101,2.5,None,True):
            with self.subTest(value=value),self.assertRaises(ValidationError):self.profile(strength_range_mode='percent',strength_tolerance_percent=value)
    def test_legacy_explicit_custom_bounds_preserved(self):
        plan=self.profile(strength_range_mode='custom',strength_min=12,strength_max=45)
        self.assertEqual((plan['strength_min'],plan['strength_max']),(12,45))
    def test_decimal_rounding_matches_slider_preview(self):
        self.assertEqual(tolerance_bounds(1441.6,30),(1009.12,1874.08))
        self.assertEqual(tolerance_bounds(1441.6,40),(864.96,2018.24))
        self.assertEqual(tolerance_bounds(0.05,30),(0.04,0.07))
