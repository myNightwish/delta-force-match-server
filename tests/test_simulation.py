import copy,json,tempfile,unittest
from datetime import datetime,timezone,timedelta
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
from app.api.routers import pilot
from app.core.config import settings
from app.services.pilot import Pilot
from app.services.pilot_chat import PilotChat
from app.services.simulation import Simulation
from app.services.testing_tools import TestingTools
from app.services.phone_auth import PhoneError
from test_recruitment_v4 import PLAN

class SimulationTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.p=Pilot(self.tmp.name+'/sim.db');self.sim=Simulation(self.p);self.owner=self.p.register()['token'];self.settings=patch.object(settings,'PILOT_TEST_ACCESS_CODE','simulation-test-code');self.settings.start();self.addCleanup(self.settings.stop);self.access=TestingTools(self.p).unlock(self.owner,'simulation-test-code')['access_token']
 def tearDown(self):self.tmp.cleanup()
 def start(self,behavior='accept',profile=None):return self.sim.start(self.owner,self.access,profile,behavior)
 def bot(self,run,eligible=True):
  with self.p.db() as db:
   row=db.execute('SELECT b.owner FROM pilot_sim_bots b WHERE run_id=? AND expected=? ORDER BY rowid LIMIT 1',(run['run']['id'],int(eligible))).fetchone();token=row['owner'];ticket=self.p._latest(db,token)
  return token,ticket['id']
 def test_start_publishes_self_and_eight_bots_without_manual_import(self):
  result=self.start();self.assertEqual(result['state']['ticket']['status'],'waiting');self.assertEqual(len(result['run']['bots']),8);self.assertEqual(sum(b['eligible'] for b in result['run']['bots']),2)
  self.assertEqual(self.p.candidates(result['token'],False)['total'],8);self.assertEqual(self.p.candidates(result['token'],True)['total'],2)
 def test_real_players_and_other_runs_cannot_see_or_invite_bots(self):
  one=self.start();real=self.p.register()['token'];self.p.join(real,copy.deepcopy(PLAN))
  self.assertEqual(self.p.candidates(real,False)['total'],0);bot,bid=self.bot(one)
  with self.assertRaisesRegex(ValueError,'ui.players_in_different_test_pools_cannot_team_up'):self.p.invite(real,bid)
  owner2=self.p.register()['token'];access2=TestingTools(self.p).unlock(owner2,'simulation-test-code')['access_token'];two=self.sim.start(owner2,access2)
  self.assertEqual(self.p.candidates(two['token'],False)['total'],8)
  with self.assertRaisesRegex(ValueError,'ui.players_in_different_test_pools_cannot_team_up'):self.p.invite(two['token'],bid)
 def test_bots_never_match_each_other(self):
  r=self.start();bot,bid=self.bot(r)
  with self.p.db() as db:other=db.execute('SELECT owner FROM pilot_sim_bots WHERE run_id=? AND owner<>? LIMIT 1',(r['run']['id'],bot)).fetchone()[0]
  with self.assertRaisesRegex(ValueError,'ui.players_in_different_test_pools_cannot_team_up'):self.p.invite(other,bid)
 def test_bot_automatically_confirms_then_sends_only_one_test_message(self):
  r=self.start();bot,bid=self.bot(r);sid=self.p.invite(r['token'],bid)['squad']['id'];self.p.action(r['token'],'confirm',expected_squad_id=sid)
  self.sim.tick();state=self.p.state(r['token']);self.assertEqual(state['squad']['status'],'ready')
  self.sim.tick();messages=PilotChat(self.p).messages(r['token'],sid)['items'];self.assertEqual(len(messages),1);self.assertIn('【测试消息】',messages[0]['content'])
 def test_reject_uses_normal_release_and_cooldown(self):
  r=self.start('reject');bot,bid=self.bot(r);sid=self.p.invite(r['token'],bid)['squad']['id'];self.sim.tick()
  self.assertEqual(self.p.state(r['token'])['ticket']['status'],'waiting')
  with self.p.db() as db:
   self.assertEqual(db.execute('SELECT status FROM squads WHERE id=?',(sid,)).fetchone()[0],'rejected');self.assertTrue(self.p._blocked(db,r['token'],bot))
 def test_timeout_keeps_bots_online_but_does_not_confirm(self):
  r=self.start('timeout');bot,bid=self.bot(r);sid=self.p.invite(r['token'],bid)['squad']['id'];self.sim.tick();self.assertFalse(any(m['confirmed'] for m in self.p.state(r['token'])['squad']['members']))
  with self.p.db() as db:db.execute('UPDATE squads SET deadline=? WHERE id=?',((datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(),sid))
  self.sim.tick();self.assertEqual(self.p.state(r['token'])['ticket']['status'],'waiting')
  with self.p.db() as db:self.assertEqual(db.execute('SELECT status FROM squads WHERE id=?',(sid,)).fetchone()[0],'timed_out')
 def test_tick_restores_bot_presence_and_survives_service_recreation(self):
  r=self.start();bot,bid=self.bot(r)
  with self.p.db() as db:db.execute('UPDATE participants SET last_seen=? WHERE token=?',('2000-01-01T00:00:00+00:00',bot))
  self.assertEqual(self.p.candidates(r['token'],False)['total'],7);Simulation(Pilot(self.p.path)).tick();self.assertEqual(self.p.candidates(r['token'],False)['total'],8)
 def test_stop_cancels_only_this_run_and_prevents_late_tick_revival(self):
  r=self.start();real=self.p.register()['token'];self.p.join(real,copy.deepcopy(PLAN));self.sim.stop(r['token'],r['access_token']);self.sim.tick()
  self.assertIsNone(self.sim.current(r['token'],r['access_token']));self.assertEqual(self.p.state(r['token'])['ticket']['status'],'cancelled');self.assertEqual(self.p.state(real)['ticket']['status'],'waiting')
  with self.p.db() as db:self.assertEqual(db.execute("SELECT count(*) FROM tickets t JOIN pilot_test_people p ON p.owner=t.token WHERE p.pool_id=? AND t.status IN ('waiting','matched')",(r['run']['id'],)).fetchone()[0],0)
 def test_expired_run_cleans_up_automatically(self):
  r=self.start()
  with self.p.db() as db:db.execute('UPDATE pilot_sim_runs SET expires=0 WHERE id=?',(r['run']['id'],))
  self.sim.tick();self.assertEqual(self.p.state(r['token'])['ticket']['status'],'cancelled');self.assertIsNone(self.sim.current(r['token'],r['access_token']))
 def test_real_active_request_cannot_be_abandoned(self):
  self.p.join(self.owner,copy.deepcopy(PLAN))
  with self.assertRaises(PhoneError):self.start()
 def test_restart_stops_old_run_and_creates_independent_pool(self):
  r=self.start();new=self.sim.start(r['token'],r['access_token'])
  self.assertNotEqual(new['run']['id'],r['run']['id']);self.assertEqual(self.p.state(r['token'])['ticket']['status'],'cancelled');self.assertEqual(self.p.candidates(new['token'],False)['total'],8)
 def test_knife_all_styles_zero_and_full_range_generate_valid_fixtures(self):
  for patch_data in [dict(goal='goal.budget_run'),dict(partner_style_min=1,partner_style_max=5,helmet=6,armor=6,ammo=6,partner_helmet_min=1,partner_armor_min=1,partner_ammo_min=1),dict(combat_score=0,strength_range_mode='custom',strength_min=0,strength_max=1300000000)]:
   with self.subTest(patch=patch_data):
    plans=self.sim.peers(Simulation.normalize({**copy.deepcopy(PLAN),**patch_data}));self.assertEqual(len(plans),8);self.assertEqual(sum(p[1] for p in plans),2)
 def test_auto_mode_matches_self_to_bot_without_bot_bot_pairing(self):
  r=self.start(profile={**copy.deepcopy(PLAN),'discovery':'auto'});self.assertEqual(r['state']['squad']['status'],'proposed')
  with self.p.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM squads').fetchone()[0],1)
 def test_authorization_required_to_inspect_or_stop_other_run(self):
  r=self.start();stranger=self.p.register()['token']
  with self.assertRaises(PhoneError):self.sim.current(stranger,r['access_token'])
  with self.assertRaises(PhoneError):self.sim.stop(stranger,r['access_token'])
 def test_http_create_status_stop_and_forged_pool_ignored(self):
  previous=pilot.service;pilot.service=self.p
  try:
   c=TestClient(app);h={'Authorization':'Bearer '+self.owner}
   self.assertEqual(c.post('/api/pilot/testing/simulation/start',headers=h,json={'access_token':'x'*32}).status_code,403)
   response=c.post('/api/pilot/testing/simulation/start',headers=h,json={'access_token':self.access});self.assertEqual(response.status_code,200,response.text);r=response.json();actor={'Authorization':'Bearer '+r['token']}
   self.assertEqual(c.post('/api/pilot/testing/simulation/current',headers=actor,json={'access_token':r['access_token']}).json()['id'],r['run']['id'])
   self.assertEqual(c.post('/api/pilot/testing/simulation/stop',headers=actor,json={'access_token':r['access_token']}).status_code,200)
  finally:pilot.service=previous

 def test_invalid_restart_preserves_existing_run(self):
  r=self.start()
  with self.assertRaises(ValueError):self.sim.start(r['token'],r['access_token'],{})
  self.assertEqual(self.sim.current(r['token'],r['access_token'])['id'],r['run']['id'])
 def test_generated_profiles_cannot_forge_pool_membership_via_join(self):
  r=self.start();real=self.p.register()['token'];state=self.p.join(real,{**copy.deepcopy(PLAN),'pool_id':r['run']['id'],'is_bot':True})
  self.assertEqual(self.p.candidates(real,False)['total'],0)
  with self.p.db() as db:self.assertFalse(db.execute('SELECT 1 FROM pilot_test_people WHERE owner=?',(real,)).fetchone())
