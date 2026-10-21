import copy,json,tempfile,unittest
from datetime import datetime,timezone,timedelta
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
from app.api.routers import pilot
from app.core.config import settings
from app.services.pilot import Pilot,assessment
from app.services.testing_tools import TestingTools
from app.services.phone_auth import PhoneError
from test_recruitment_v4 import PLAN

class TestingToolsTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.p=Pilot(self.tmp.name+'/tests.db');self.tools=TestingTools(self.p);self.owner=self.p.register()['token']
  self.code=patch.object(settings,'PILOT_TEST_ACCESS_CODE','only-for-test-123456');self.code.start();self.addCleanup(self.code.stop)
 def tearDown(self):self.tmp.cleanup()
 def access(self):return self.tools.unlock(self.owner,'only-for-test-123456')['access_token']
 def test_disabled_and_wrong_password(self):
  with patch.object(settings,'PILOT_TEST_ACCESS_CODE',''):
   with self.assertRaises(PhoneError):self.tools.unlock(self.owner,'any')
  with self.assertRaises(PhoneError):self.tools.unlock(self.owner,'wrong')
 def test_fixture_catalog_requires_owner_bound_authorization(self):
  grant=self.access();self.assertEqual(len(self.tools.scenarios(self.owner,grant)['scenarios']),10)
  other=self.p.register()['token']
  with self.assertRaises(PhoneError):self.tools.scenarios(other,grant)
  with self.assertRaises(PhoneError):self.tools.scenarios(self.owner,'fake')
 def test_expiry_and_lock(self):
  grant=self.access()
  with patch('app.services.testing_tools.time.time',return_value=9999999999):
   with self.assertRaises(PhoneError):self.tools.scenarios(self.owner,grant)
  self.tools.lock(self.owner,grant)
  with self.assertRaises(PhoneError):self.tools.scenarios(self.owner,grant)
 def test_import_validates_and_does_not_publish(self):
  grant=self.access();result=self.tools.import_profile(self.owner,grant,copy.deepcopy(PLAN));self.assertIsNone(result['token']);self.assertTrue(result['profile']['nickname'].startswith('【测试】'));self.assertIsNone(self.p.state(self.owner)['ticket'])
  state=self.p.join(self.owner,result['profile']);self.assertTrue(state['ticket']['profile']['is_demo'])
  # Reloading or editing the form cannot accidentally remove the server-side test flag
  self.p.action(self.owner,'cancel');state=self.p.join(self.owner,copy.deepcopy(PLAN));self.assertTrue(state['ticket']['profile']['is_demo'])
 def test_invalid_import_does_not_mark_or_create_identity(self):
  grant=self.access()
  for invalid in ({},dict(PLAN,maps=[]),dict(PLAN,manual_score=float('inf'),score_mode='manual')):
   with self.assertRaises(ValueError):self.tools.import_profile(self.owner,grant,invalid,True)
  with self.p.db() as db:
   self.assertEqual(db.execute('SELECT count(*) FROM participants').fetchone()[0],1);self.assertEqual(db.execute('SELECT count(*) FROM pilot_test_people').fetchone()[0],0)
 def test_active_real_player_cannot_be_silently_replaced(self):
  grant=self.access();self.p.join(self.owner,copy.deepcopy(PLAN))
  for new_identity in (False,True):
   with self.assertRaises(PhoneError):self.tools.import_profile(self.owner,grant,copy.deepcopy(PLAN),new_identity)
 def test_new_test_identity_has_independent_queue_and_rotated_access(self):
  first=self.tools.import_profile(self.owner,self.access(),copy.deepcopy(PLAN));self.p.join(self.owner,first['profile'])
  second=self.tools.import_profile(self.owner,first['access_token'],copy.deepcopy(PLAN),True)
  self.assertNotEqual(second['token'],self.owner);self.assertIsNone(self.p.state(second['token'])['ticket']);self.assertEqual(self.p.state(self.owner)['ticket']['status'],'waiting')
  self.tools.scenarios(second['token'],second['access_token'])
  with self.assertRaises(PhoneError):self.tools.scenarios(self.owner,second['access_token'])
 def test_every_fixture_is_valid_and_expected_pair_eligibility(self):
  scenarios=self.tools.scenarios(self.owner,self.access())['scenarios'];eligible={'compatible','task','knife','auto','private-auto'}
  from app.schemas.recruitment_v4 import RecruitmentV4
  for scene in scenarios:
   with self.subTest(scene=scene['id']):
    a,b=[RecruitmentV4.model_validate(p).profile() for p in scene['players']];self.assertEqual(assessment(a,b)['eligible'],scene['id'] in eligible)
 def test_http_gate_and_bad_password_rate_limit(self):
  previous=pilot.service;pilot.service=self.p
  try:
   c=TestClient(app);headers={'Authorization':'Bearer '+self.owner}
   self.assertEqual(c.post('/api/pilot/testing/scenarios',headers=headers,json={'access_token':'x'*32}).status_code,403)
   result=c.post('/api/pilot/testing/unlock',headers=headers,json={'code':'only-for-test-123456'});self.assertEqual(result.status_code,200);grant=result.json()['access_token']
   self.assertEqual(c.post('/api/pilot/testing/scenarios',headers=headers,json={'access_token':grant}).status_code,200)
   self.assertEqual(c.post('/api/pilot/testing/import',headers=headers,json={'access_token':grant,'profile':PLAN}).status_code,200)
   for i in range(4):self.assertEqual(c.post('/api/pilot/testing/unlock',headers=headers,json={'code':'wrong'}).status_code,403)
   self.assertEqual(c.post('/api/pilot/testing/unlock',headers=headers,json={'code':'wrong'}).status_code,429)
  finally:pilot.service=previous
 def test_demo_requests_are_excluded_from_real_metrics(self):
  result=self.tools.import_profile(self.owner,self.access(),copy.deepcopy(PLAN));self.p.join(self.owner,result['profile'])
  self.assertEqual(self.p.metrics()['groups']['manual']['requests'],0)

 def test_phone_account_requires_separate_test_identity(self):
  grant=self.access()
  with self.p.db() as db:
   db.execute('CREATE TABLE phone_accounts(owner TEXT)');db.execute('INSERT INTO phone_accounts VALUES(?)',(self.owner,))
  with self.assertRaises(PhoneError):self.tools.import_profile(self.owner,grant,copy.deepcopy(PLAN))
  result=self.tools.import_profile(self.owner,grant,copy.deepcopy(PLAN),True)
  self.assertNotEqual(result['token'],self.owner)
 def test_public_join_cannot_forge_demo_flag(self):
  previous=pilot.service;pilot.service=self.p
  try:
   response=TestClient(app).post('/api/pilot/join',headers={'Authorization':'Bearer '+self.owner},json={**copy.deepcopy(PLAN),'is_demo':True})
   self.assertEqual(response.status_code,200);self.assertFalse(response.json()['ticket']['profile'].get('is_demo',False));self.assertEqual(self.p.metrics()['groups']['manual']['requests'],1)
  finally:pilot.service=previous
