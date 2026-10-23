"""Independent rule oracle and simultaneous reservation regression tests."""
import copy
import json
import random
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from app.services.pilot import Pilot, assessment
from app.services.recruitment_rules import agreement_v4, MAP_MODES
from app.schemas.recruitment_v4 import RecruitmentV4
from test_recruitment_v4 import PLAN


def expected(a,b):
    if any(a[k]!=b[k] for k in ('region','platform','listing_language','voice','slot','goal')):return False
    if not {(m['map'],m['difficulty']) for m in a['maps']} & {(m['map'],m['difficulty']) for m in b['maps']}:return False
    if a['goal']=='goal.missions' and not set(a['tasks']) & set(b['tasks']):return False
    for own,peer in ((a,b),(b,a)):
        if not own['strength_min']<=peer['strength_score']<=own['strength_max']:return False
        if not own['partner_style_min']<=peer['style']<=own['partner_style_max']:return False
        if own['goal']!='goal.budget_run' and any(peer[k]<own['partner_'+k+'_min'] for k in ('helmet','armor','ammo')):return False
    return True

class MatchingReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.p=Pilot(self.tmp.name+'/matching.db')
    def tearDown(self):self.tmp.cleanup()
    def player(self,**patch):
        token=self.p.register()['token'];state=self.p.join(token,{**copy.deepcopy(PLAN),**patch})
        return token,state['ticket']['id']
    def test_seeded_rule_oracle_and_symmetry_for_5000_pairs(self):
        rng=random.Random(20261023);accepted=0
        pairs=[{'map':m,'difficulty':d} for m,ds in MAP_MODES.items() for d in ds]
        for i in range(5000):
            raw=copy.deepcopy(PLAN);raw.update(goal=rng.choice(['goal.missions','goal.loot_extract','goal.aggressive','goal.budget_run']),tasks=rng.sample(['task.investigate','task.place','task.eliminate_operators'],rng.randint(1,3)),maps=rng.sample(pairs,rng.randint(1,2)))
            # Select distinct maps, keeping the fixture valid independently of the evaluator
            raw['maps']=list({m['map']:m for m in raw['maps']}.values())
            a=RecruitmentV4.model_validate(raw).profile();b=copy.deepcopy(a)
            for _ in range(rng.randint(0,4)):
                key=rng.choice(['region','platform','listing_language','voice','slot','goal','maps','tasks','strength_score','strength_min','strength_max','style','partner_style_min','partner_style_max','helmet','partner_helmet_min'])
                options={'region':['NA','EU'],'platform':['PC','Mobile'],'listing_language':['zh','en'],'voice':['voice.mic','voice.listen'],'slot':['slot.next_hour','slot.today_evening'],'goal':['goal.missions','goal.loot_extract','goal.aggressive','goal.budget_run']}
                if key in options:b[key]=rng.choice(options[key])
                elif key=='maps':b[key]=rng.sample(pairs,rng.randint(1,3))
                elif key=='tasks':b[key]=rng.sample(['task.investigate','task.place','task.eliminate_operators'],rng.randint(0,3))
                elif key.startswith('strength'):b[key]=rng.choice([0,69.99,70,100,130,130.01,1000000000])
                else:b[key]=rng.randint(1,5)
            # Null gear is meaningful only to knife; non-knife comparisons need numeric gear
            for plan in (a,b):
                if plan['goal']!='goal.budget_run':
                    for k in ('helmet','armor','ammo'):
                        if plan[k] is None:plan[k]=4
                        if plan['partner_'+k+'_min'] is None:plan['partner_'+k+'_min']=2
            truth=expected(a,b);accepted+=truth
            self.assertEqual(assessment(a,b)['eligible'],truth,f'pair {i}')
            self.assertEqual(assessment(b,a)['eligible'],truth,f'reversed pair {i}')
            if truth:
                agreement=agreement_v4(a,b)
                self.assertEqual(agreement,agreement_v4(b,a))
                self.assertEqual(agreement,agreement_v4({**a,'maps':list(reversed(a['maps'])),'tasks':list(reversed(a['tasks']))},b))
        self.assertGreater(accepted,500);self.assertLess(accepted,4500)
    def test_inclusive_score_boundaries_and_zero(self):
        a=RecruitmentV4.model_validate(PLAN).profile()
        for value,ok in [(69.99,False),(70,True),(130,True),(130.01,False)]:
            b={**copy.deepcopy(a),'strength_score':value,'strength_min':0,'strength_max':1000000000}
            self.assertEqual(assessment(a,b)['eligible'],ok)
        z=RecruitmentV4.model_validate({**PLAN,'combat_score':0}).profile()
        self.assertTrue(assessment(z,z)['eligible']);self.assertFalse(assessment(z,a)['eligible'])
    def test_sixteen_concurrent_invites_have_exactly_one_winner(self):
        target,tid=self.player();actors=[self.player()[0] for _ in range(16)];gate=threading.Barrier(16)
        def apply(owner):
            gate.wait()
            try:self.p.invite(owner,tid);return True
            except ValueError:return False
        with ThreadPoolExecutor(16) as pool:results=list(pool.map(apply,actors))
        self.assertEqual(sum(results),1)
        with self.p.db() as db:
            squads=db.execute("SELECT a,b FROM squads WHERE status='proposed'").fetchall()
            self.assertEqual(len(squads),1)
            self.assertEqual(db.execute("SELECT count(*) FROM tickets WHERE status='matched'").fetchone()[0],2)
    def test_twenty_four_simultaneous_auto_joins_form_disjoint_pairs(self):
        actors=[self.p.register()['token'] for _ in range(24)];gate=threading.Barrier(24)
        def join(owner):gate.wait();return self.p.join(owner,{**copy.deepcopy(PLAN),'discovery':'auto'})
        with ThreadPoolExecutor(24) as pool:list(pool.map(join,actors))
        with self.p.db() as db:
            squads=db.execute("SELECT a,b FROM squads WHERE status='proposed'").fetchall()
            self.assertEqual(len(squads),12)
            self.assertEqual(len({tid for row in squads for tid in row}),24)
            self.assertEqual(db.execute("SELECT count(*) FROM tickets WHERE status='waiting'").fetchone()[0],0)
    def test_invite_and_cancel_race_leaves_no_half_reservation(self):
        for _ in range(10):
            a,aid=self.player();b,bid=self.player();gate=threading.Barrier(2)
            def invite():
                gate.wait()
                try:self.p.invite(a,bid)
                except ValueError:pass
            def cancel():gate.wait();self.p.action(b,'cancel')
            with ThreadPoolExecutor(2) as pool:
                futures=[pool.submit(invite),pool.submit(cancel)]
                for f in futures:f.result()
            with self.p.db() as db:
                self.assertEqual(db.execute('SELECT status FROM tickets WHERE id=?',(bid,)).fetchone()[0],'cancelled')
                self.assertEqual(db.execute('SELECT status FROM tickets WHERE id=?',(aid,)).fetchone()[0],'waiting')
                self.assertEqual(db.execute("SELECT count(*) FROM squads WHERE status IN ('proposed','ready') AND (a IN (?,?) OR b IN (?,?))",(aid,bid,aid,bid)).fetchone()[0],0)
    def test_simultaneous_confirmation_is_idempotent(self):
        a,aid=self.player();b,bid=self.player();sid=self.p.invite(a,bid)['squad']['id'];gate=threading.Barrier(16)
        def confirm(i):gate.wait();return self.p.action(a if i%2 else b,'confirm',expected_squad_id=sid)
        with ThreadPoolExecutor(16) as pool:list(pool.map(confirm,range(16)))
        with self.p.db() as db:
            squad=db.execute('SELECT * FROM squads WHERE id=?',(sid,)).fetchone()
            self.assertEqual(squad['status'],'ready');self.assertEqual(set(json.loads(squad['confirms'])),{str(aid),str(bid)})
            self.assertEqual(db.execute("SELECT count(*) FROM pilot_events WHERE squad_id=? AND kind='confirmed'",(sid,)).fetchone()[0],2)

    def test_batch_availability_preserves_pool_online_and_exclusion_policy(self):
        owner,_=self.player();peers=[self.player() for _ in range(7)]
        with self.p.db() as db:
            db.execute("INSERT INTO pilot_test_people(owner,pool_id,is_bot) VALUES(?,'p',0)",(owner,))
            for i,(peer,_) in enumerate(peers):
                if i<5:db.execute('INSERT INTO pilot_test_people(owner,pool_id,is_bot) VALUES(?,?,?)',(peer,'p' if i!=3 else 'q',int(i==2)))
            db.execute("UPDATE participants SET last_seen='2000-01-01T00:00:00+00:00' WHERE token=?",(peers[1][0],))
            self.p._exclude(db,owner,peers[4][0])
            for bot in (0,1):
                db.execute('UPDATE pilot_test_people SET is_bot=? WHERE owner=?',(bot,owner))
                expected={row['id'] for row in db.execute("SELECT * FROM tickets WHERE status='waiting' AND token<>?",(owner,)).fetchall() if self.p.same_pool(db,owner,row['token']) and self.p.online(db,row['token']) and not self.p._blocked(db,owner,row['token'])}
                actual={row['id'] for row in self.p._available_candidates(db,owner)}
                self.assertEqual(actual,expected)
                self.assertIn(peers[0][1],actual)
                self.assertEqual(peers[2][1] in actual,not bot)
    def test_candidate_sql_count_does_not_grow_per_peer(self):
        owner,_=self.player();original=self.p.db;counts=[]
        @contextmanager
        def traced():
            with original() as db:
                db.set_trace_callback(counts.append);yield db
        sizes=[]
        for target in (10,50):
            while len(sizes)<target:sizes.append(self.player())
            self.p.db=traced;counts.clear()
            try:
                result=self.p.candidates(owner)
                self.assertEqual(result['total'],target)
                query_count=len(counts)
            finally:self.p.db=original
            if target==10:first=query_count
            else:self.assertLessEqual(query_count,first+2)
