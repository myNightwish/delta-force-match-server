import copy
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
from app.api.routers import pilot
from app.core.config import settings
from app.services.pilot import Pilot
from app.services.growth import Growth,ActivityIn
from test_recruitment_v4 import PLAN
from pydantic import ValidationError

REVIEW=dict(played=True,again='yes',goal_met=True,budget_met=True,skill_met=True,reason='')
class GrowthTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.p=Pilot(self.tmp.name+'/g.db');self.g=Growth(self.p)
  self.old=pilot.service;pilot.service=self.p;self.c=TestClient(app)
 def tearDown(self):pilot.service=self.old;self.tmp.cleanup()
 def person(self,join=True,**values):
  t=self.p.register()['token'];self.g.visit(t,'direct')
  if join:self.p.join(t,{**copy.deepcopy(PLAN),**values})
  return t
 def h(self,t):return {'Authorization':'Bearer '+t}
 def activity(self,**patches):
  return ActivityIn.model_validate(dict(title='周末组队',starts_at=datetime.now(timezone.utc)+timedelta(hours=2),region='SEA',platform='PC',language='中文',goal='搜打撤',capacity=2,**patches))
 def test_public_share_allowlist_and_stable_code(self):
  a=self.person();code=self.g.create_share(a)['code'];self.assertEqual(code,self.g.create_share(a)['code'])
  result=self.g.shared(code);self.assertNotIn(a,json.dumps(result));self.assertNotIn('confidential_kd',result['profile']);self.assertNotIn('game_id',result['profile']);self.assertNotIn('id',result)
  self.assertEqual(result['profile']['nickname'],PLAN['nickname']);self.assertFalse(result['can_invite'])
 def test_share_routes_auth_and_foreign_owner_cannot_modify(self):
  a=self.person();b=self.person();code=self.g.create_share(a)['code'];self.assertEqual(self.c.get('/api/pilot/shares/'+code).status_code,200)
  self.assertEqual(self.c.post('/api/pilot/shares').status_code,401)
  self.assertNotEqual(self.g.create_share(b)['code'],code)
  self.assertEqual(self.c.get('/api/pilot/shares/'+"x' OR 1=1").status_code,404)
 def test_private_and_demo_cannot_share(self):
  private=self.person(public=False)
  with self.assertRaises(ValueError):self.g.create_share(private)
  demo=self.person()
  with self.p.db() as db:db.execute('INSERT INTO pilot_test_people(owner) VALUES(?)',(demo,))
  with self.assertRaises(ValueError):self.g.create_share(demo)
 def test_cancelled_expired_matched_link_withdraws_profile(self):
  a=self.person();code=self.g.create_share(a)['code'];self.p.action(a,'cancel')
  r=self.c.get('/api/pilot/shares/'+code);self.assertEqual(r.status_code,404);self.assertNotIn('profile',r.json())
  a=self.person();code=self.g.create_share(a)['code']
  with self.p.db() as db:db.execute('UPDATE tickets SET created=? WHERE token=?',((datetime.now(timezone.utc)-timedelta(minutes=21)).isoformat(),a))
  self.assertEqual(self.c.get('/api/pilot/shares/'+code).status_code,404)
 def test_shared_invitation_runs_bilateral_rules_and_normal_confirmations(self):
  a=self.person();code=self.g.create_share(a)['code'];b=self.person(style=1,partner_style_min=1,partner_style_max=5)
  # Alter A requirement through a fresh request; the old link stays revoked.
  self.p.action(a,'cancel');self.p.join(a,{**copy.deepcopy(PLAN),'partner_style_min':3});code=self.g.create_share(a)['code']
  self.assertFalse(self.g.shared(code,b)['can_invite'])
  with self.assertRaises(ValueError):self.g.invite_share(b,code)
  self.p.action(b,'cancel');self.p.join(b,copy.deepcopy(PLAN))
  squad=self.g.invite_share(b,code)['squad'];self.assertEqual(squad['status'],'proposed')
  self.assertEqual(self.c.get('/api/pilot/shares/'+code).status_code,404)
  self.p.action(a,'confirm');self.assertEqual(self.p.action(b,'confirm')['squad']['status'],'ready')
 def test_offline_share_read_does_not_revive_owner(self):
  a=self.person();code=self.g.create_share(a)['code'];b=self.person()
  with self.p.db() as db:db.execute('UPDATE participants SET last_seen=? WHERE token=?',((datetime.now(timezone.utc)-timedelta(minutes=2)).isoformat(),a))
  self.assertFalse(self.g.shared(code,b)['online'])
  with self.assertRaises(ValueError):self.g.invite_share(b,code)
 def test_simulator_cannot_invite_from_real_share(self):
  a=self.person();code=self.g.create_share(a)['code'];b=self.person()
  with self.p.db() as db:db.execute('INSERT INTO pilot_test_people VALUES(?,?,?)',(b,'isolated',0))
  self.assertFalse(self.g.shared(code,b)['can_invite'])
  with self.assertRaises(ValueError):self.g.invite_share(b,code)
 def test_activity_bounds_and_timezone(self):
  for patches in [dict(starts_at=datetime.now()+timedelta(hours=2)),dict(starts_at=datetime.now(timezone.utc)-timedelta(hours=2)),dict(starts_at=datetime.now(timezone.utc)+timedelta(days=40)),dict(capacity=1),dict(duration_minutes=999),dict(title=' '),dict(region='bad')]:
   base=self.activity().model_dump();base.update(patches)
   with self.subTest(patches=patches),self.assertRaises(ValidationError):ActivityIn.model_validate(base)
 def test_reservations_idempotent_private_and_cancel_releases(self):
  aid=self.g.create_activity(self.activity())['id'];a=self.person(False);b=self.person(False);c=self.person(False)
  self.g.reserve_activity(a,aid);self.g.reserve_activity(a,aid);self.g.reserve_activity(b,aid)
  with self.assertRaises(ValueError):self.g.reserve_activity(c,aid)
  self.assertTrue(self.g.activities(a)['items'][0]['mine']);self.assertNotIn(a,json.dumps(self.g.activities(c)))
  self.g.reserve_activity(a,aid,True);self.g.reserve_activity(c,aid)
  with self.p.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM tickets').fetchone()[0],0)
 def test_capacity_under_concurrency(self):
  aid=self.g.create_activity(self.activity())['id'];owners=[self.person(False) for _ in range(10)]
  def reserve(t):
   try:self.g.reserve_activity(t,aid);return True
   except ValueError:return False
  with ThreadPoolExecutor(max_workers=5) as pool:self.assertEqual(sum(pool.map(reserve,owners)),2)
 def test_activity_overlap_cancel_and_persistence(self):
  aid=self.g.create_activity(self.activity())['id'];bid=self.g.create_activity(self.activity())['id'];a=self.person(False)
  self.g.reserve_activity(a,aid)
  with self.assertRaises(ValueError):self.g.reserve_activity(a,bid)
  self.assertTrue(Growth(Pilot(self.p.path)).activities(a)['items'][0]['mine'])
  self.g.close_activity(aid);self.assertEqual(self.g.activities(a)['items'][0]['status'],'cancelled')
  self.g.reserve_activity(a,bid)
  with self.assertRaises(ValueError):self.g.reserve_activity(self.person(False),aid)
 def test_activity_foreign_cancel_only_changes_own_reservation(self):
  aid=self.g.create_activity(self.activity())['id'];a=self.person(False);b=self.person(False);self.g.reserve_activity(a,aid)
  self.g.reserve_activity(b,aid,True);self.assertTrue(self.g.activities(a)['items'][0]['mine'])
 def test_operator_fails_closed_in_development_too(self):
  for key in ['', 'short', 'z'*40]:
   with patch.object(settings,'PILOT_METRICS_TOKEN','' if key=='' else 'a'*40):
    self.assertEqual(self.c.get('/api/pilot/operations/metrics',headers=self.h(key)).status_code,403)
    self.assertEqual(self.c.post('/api/pilot/operations/activities',headers=self.h(key),json=self.activity().model_dump(mode='json')).status_code,403)
  with patch.object(settings,'PILOT_METRICS_TOKEN','a'*40):
   self.assertEqual(self.c.get('/api/pilot/operations/metrics',headers=self.h('a'*40)).status_code,200)
   response=self.c.post('/api/pilot/operations/activities',headers=self.h('a'*40),json=self.activity().model_dump(mode='json'));self.assertEqual(response.status_code,200)
   self.assertEqual(self.c.post('/api/pilot/operations/activities/'+str(response.json()['id'])+'/close',headers=self.h('a'*40)).status_code,200)
 def test_metrics_denominators_feedback_and_repeat_publication(self):
  a=self.person();b=self.person();empty=self.person(False)
  with self.p.db() as db:tid=db.execute('SELECT id FROM tickets WHERE token=?',(b,)).fetchone()[0]
  sid=self.p.invite(a,tid)['squad']['id'];self.p.action(a,'confirm');self.p.action(b,'confirm')
  self.p.action(a,'feedback',REVIEW);self.p.action(b,'feedback',{**REVIEW,'played':False,'again':'no'})
  self.p.join(a,copy.deepcopy(PLAN));m=self.g.metrics()
  self.assertEqual((m['visitors'],m['publishers'],m['requests'],m['confirmed_requests']),(3,2,3,2))
  self.assertEqual((m['feedback_requests'],m['played_requests'],m['reported_play_rate']),(2,1,50))
  self.assertEqual((m['repeat_publishers'],m['repeat_publication_rate']),(1,50));self.assertEqual(m['willing_again_rate'],100)
 def test_visits_deduplicate_and_demo_and_legacy_excluded(self):
  legacy=self.p.register()['token'];self.p.join(legacy,copy.deepcopy(PLAN));self.g.visit(legacy,'direct')
  a=self.person();self.g.visit(a,'share');demo=self.person()
  with self.p.db() as db:db.execute('INSERT INTO pilot_test_people(owner) VALUES(?)',(demo,))
  m=self.g.metrics();self.assertEqual((m['visitors'],m['publishers'],m['requests']),(2,1,1))
 def test_empty_metrics_null_not_fake_zero(self):
  m=self.g.metrics();self.assertIsNone(m['publication_rate']);self.assertIsNone(m['reported_play_rate']);self.assertIsNone(m['median_confirm_seconds'])
 def test_cancel_after_confirm_still_counts_confirmation(self):
  a=self.person();b=self.person()
  with self.p.db() as db:tid=db.execute('SELECT id FROM tickets WHERE token=?',(b,)).fetchone()[0]
  self.p.invite(a,tid);self.p.action(a,'confirm');self.p.action(b,'confirm');self.p.action(a,'cancel')
  self.assertEqual(self.g.metrics()['confirmed_requests'],2)
 def test_request_size_limits_and_security_headers(self):
  r=self.c.post('/api/pilot/participants',content='x'*70000);self.assertEqual(r.status_code,413)
  def chunks():yield b'x'*40000;yield b'x'*40000
  r=self.c.post('/api/pilot/participants',content=chunks());self.assertEqual(r.status_code,413)
  r=self.c.get('/health');self.assertEqual(r.headers['x-content-type-options'],'nosniff');self.assertEqual(r.headers['cache-control'],'no-store')
 def test_new_endpoints_rate_limit(self):
  with patch('app.services.pilot_limits.POLICIES',{'public_read':{'ip':[(1,60)]}}):
   self.assertEqual(self.c.get('/api/pilot/shares/'+'x'*32).status_code,404)
   r=self.c.get('/api/pilot/shares/'+'x'*32);self.assertEqual(r.status_code,429);self.assertIn('retry-after',r.headers)

 def test_identity_marked_demo_after_reserving_can_release_and_does_not_consume_real_capacity(self):
  aid=self.g.create_activity(self.activity())['id'];a=self.person(False);self.g.reserve_activity(a,aid)
  with self.p.db() as db:db.execute('INSERT INTO pilot_test_people(owner) VALUES(?)',(a,))
  self.g.reserve_activity(self.person(False),aid);self.g.reserve_activity(self.person(False),aid)
  self.assertEqual(self.g.activities(a)['items'][0]['reserved'],2)
  self.g.reserve_activity(a,aid,True);self.assertFalse(self.g.activities(a)['items'][0]['mine'])
