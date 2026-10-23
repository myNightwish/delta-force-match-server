import copy
import unittest
from app.schemas.time_slots import TIME_SLOT_OPTIONS
from app.schemas.recruitment_v4 import RecruitmentV4
from app.schemas.pilot_preferences import Preferences
from app.services.phone_auth import clean_profile
from app.services.pilot import assessment
from test_recruitment_v4 import PLAN
from test_recruitment import BASE

class TimeSlotTests(unittest.TestCase):
    def test_all_offered_slots_are_accepted_by_recruitment_and_profiles(self):
        self.assertGreater(len(TIME_SLOT_OPTIONS),2)
        for slot in TIME_SLOT_OPTIONS:
            with self.subTest(slot=slot):
                self.assertEqual(RecruitmentV4.model_validate({**copy.deepcopy(PLAN),'slot':slot}).slot,slot)
                self.assertEqual(Preferences.model_validate({**copy.deepcopy(BASE),'slot':slot}).slot,slot)
                self.assertEqual(clean_profile({'slot':slot})['slot'],slot)
    def test_different_slots_still_cannot_match(self):
        profiles=[RecruitmentV4.model_validate({**copy.deepcopy(PLAN),'slot':slot}).profile() for slot in TIME_SLOT_OPTIONS]
        for i,a in enumerate(profiles):
            for j,b in enumerate(profiles):
                self.assertEqual(assessment(a,b)['eligible'],i==j)
