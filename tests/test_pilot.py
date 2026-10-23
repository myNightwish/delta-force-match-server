import os
import tempfile
import unittest
from unittest.mock import patch
from app.core.config import settings
from app.services.pilot import Pilot, compatible
from app.api.routers.pilot import Preferences

P=dict(nickname='test',game_id='secret-id',region='NA',platform='PC',language='language.zh',map='map.zero_dam',difficulty='difficulty.normal',voice='voice.mic',slot='slot.next_hour',goal='goal.legacy_tasks',budget_min=20,budget_max=50,rank=4,skill=3,gap=1)
R=dict(played=True,again='yes',goal_met=True,budget_met=True,skill_met=True,reason='')

class PilotTest(unittest.TestCase):
    def setUp(self):
        self.experiment=patch.object(settings,'PILOT_EXPERIMENT_MODE',True);self.experiment.start();self.addCleanup(self.experiment.stop)
        self.tmp=tempfile.TemporaryDirectory();self.p=Pilot(self.tmp.name+'/pilot.db')
    def tearDown(self): self.tmp.cleanup()
    def person(self,arm):
        t=self.p.register()['token']
        with self.p.db() as db: db.execute('UPDATE participants SET arm=? WHERE token=?',(arm,t))
        return t
    def pair(self,arm='B'):
        a,b=self.person(arm),self.person(arm)
        self.p.join(a,P);self.p.join(b,P)
        return a,b
    def test_three_conditions_and_mutual_gap(self):
        self.assertTrue(compatible(P,{**P,'goal':'goal.legacy_aggressive'},'B'))
        self.assertTrue(compatible(P,{**P,'budget_min':51,'budget_max':60},'B'))
        self.assertTrue(compatible(P,{**P,'skill':4,'gap':0},'B'))
        self.assertFalse(compatible(P,{**P,'budget_min':50,'budget_max':60},'B'))
    def test_control_only_rank_after_hard_conditions(self):
        a,b=self.person('A'),self.person('A');self.p.join(a,P)
        state=self.p.join(b,{**P,'goal':'goal.legacy_aggressive','budget_min':80,'budget_max':90,'skill':5})
        self.assertIsNotNone(state['squad']);self.assertEqual(len(state['squad']['agreement']['warnings']),3)
    def test_hard_conditions_never_relaxed(self):
        for arm in ['A','B']:
            self.assertTrue(compatible(P,{**P,'region':'EU'},arm))
    def test_no_cross_arm_matching(self):
        a,b=self.person('A'),self.person('B');self.p.join(a,P)
        self.assertIsNone(self.p.join(b,P)['squad'])
    def test_blocked_request_and_no_fake_match(self):
        a=self.person('B');self.assertIsNone(self.p.join(a,P)['squad'])
        with self.assertRaises(ValueError): self.p.join(a,P)
    def test_confirmation_privacy_and_feedback(self):
        a,b=self.pair()
        s=self.p.state(a);other=next(m for m in s['squad']['members'] if not m['is_me']);self.assertNotIn('game_id',other['profile'])
        with self.assertRaises(ValueError): self.p.action(a,'feedback',R)
        self.p.action(a,'confirm');s=self.p.action(b,'confirm')
        self.assertEqual(s['squad']['status'],'ready')
        self.assertEqual(s['squad']['members'][0]['profile']['game_id'],'secret-id')
        self.p.action(a,'feedback',R)
        with self.assertRaises(ValueError): self.p.action(a,'feedback',R)
        with self.assertRaises(ValueError): self.p.action(b,'cancel')
        self.p.action(b,'feedback',{**R,'again':'no'})
        self.assertEqual(self.p.state(a)['squad']['status'],'done')
        m=self.p.metrics()['groups']['B'];self.assertEqual(m['again_rate'],50)
        # Repeat sessions do not inflate primary denominator.
        self.p.join(a,P);self.p.join(b,P);self.p.action(a,'confirm');self.p.action(b,'confirm');self.p.action(a,'feedback',R)
        self.assertEqual(self.p.metrics()['groups']['B']['played_first_responses'],2)
    def test_unplayed_not_counted_as_played(self):
        a,b=self.pair();self.p.action(a,'confirm');self.p.action(b,'confirm');self.p.action(a,'feedback',{**R,'played':False})
        self.assertIsNone(self.p.metrics()['groups']['B']['again_rate'])
    def test_cancel_propagates_and_allows_requeue(self):
        a,b=self.pair();self.p.action(a,'cancel');self.assertEqual(self.p.state(b)['ticket']['status'],'waiting')
        self.p.action(b,'cancel')
        self.assertIsNone(self.p.join(b,P)['squad'])
    def test_fee_snapshot(self):
        a,b=self.pair();fee=self.p.state(a)['squad']['fee_cents']
        old=os.environ.get('PILOT_FEE_CENTS');os.environ['PILOT_FEE_CENTS']='100'
        try:
            self.assertEqual(self.p.config()['fee_cents'],100)
            self.assertEqual(self.p.state(a)['squad']['fee_cents'],fee)
        finally:
            if old is None: os.environ.pop('PILOT_FEE_CENTS')
            else: os.environ['PILOT_FEE_CENTS']=old
    def test_schema_rejects_invalid_budget(self):
        with self.assertRaises(ValueError): Preferences(**{**P,'budget_min':70})
    def test_stale_queue_not_matched(self):
        a,b=self.person('B'),self.person('B');self.p.join(a,P)
        with self.p.db() as db: db.execute("UPDATE tickets SET created='2000-01-01T00:00:00+00:00' WHERE token=?",(a,))
        self.assertIsNone(self.p.join(b,P)['squad'])
        self.assertEqual(self.p.state(a)['ticket']['status'],'expired')

    def test_invalid_identity(self):
        with self.assertRaises(ValueError):self.p.state('unknown')

if __name__=='__main__': unittest.main()
