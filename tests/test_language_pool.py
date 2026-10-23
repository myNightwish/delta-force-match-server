import copy,json,tempfile,unittest
from fastapi.testclient import TestClient
from unittest.mock import patch
from app.main import app
from app.api.routers import pilot
from app.services.pilot import Pilot,assessment
from app.services.growth import Growth
from app.schemas.recruitment_v4 import RecruitmentV4
from test_recruitment_v4 import PLAN

class LanguagePoolTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.p=Pilot(self.tmp.name+'/pools.db')
 def tearDown(self):self.tmp.cleanup()
 def person(self,language='zh',**changes):
  token=self.p.register()['token'];profile={**copy.deepcopy(PLAN),'listing_language':language,**changes};state=self.p.join(token,profile);return token,state
 def test_same_pool_pairs_and_cross_pool_is_a_hard_conflict(self):
  a=RecruitmentV4.model_validate({**PLAN,'listing_language':'en'}).profile();b=RecruitmentV4.model_validate({**PLAN,'listing_language':'zh'}).profile()
  self.assertTrue(assessment(a,a)['eligible'])
  result=assessment(a,b);self.assertFalse(result['eligible']);self.assertIn('conflict.listing_language',result['hard_conflicts'])
  self.assertEqual(a['language'],'language.en');self.assertEqual(b['language'],'language.zh')
 def test_automatic_matching_never_crosses_listing_language(self):
  en,_=self.person('en',discovery='auto');zh,_=self.person('zh',discovery='auto')
  self.assertIsNone(self.p.state(en)['squad']);self.assertIsNone(self.p.state(zh)['squad'])
  peer,state=self.person('en',discovery='auto')
  self.assertIsNotNone(state['squad']);self.assertEqual(state['squad']['id'],self.p.state(en)['squad']['id']);self.assertIsNone(self.p.state(zh)['squad'])
 def test_hall_pool_is_owned_by_active_listing_not_ui_query(self):
  owner,_=self.person('en');en,_=self.person('en');zh,_=self.person('zh')
  results=self.p.candidates(owner,False,0,'','zh')
  self.assertEqual(results['total'],1);self.assertTrue(all(c['profile']['listing_language']=='en' for c in results['items']))
 def test_visitors_can_browse_only_the_selected_language_pool(self):
  self.person('en');self.person('zh');visitor=self.p.register()['token']
  for language in ['zh','en']:
   results=self.p.candidates(visitor,False,0,'',language)
   self.assertEqual(results['total'],1);self.assertEqual(results['items'][0]['profile']['listing_language'],language)
 def test_direct_id_invitation_cannot_bypass_language_pool(self):
  owner,_=self.person('en');peer,state=self.person('zh')
  with self.assertRaises(ValueError):self.p.invite(owner,state['ticket']['id'])
  self.assertEqual(self.p.state(owner)['ticket']['status'],'waiting');self.assertEqual(self.p.state(peer)['ticket']['status'],'waiting')
 def test_shared_link_cannot_bypass_language_pool(self):
  owner,_=self.person('en');peer,_=self.person('zh');growth=Growth(self.p);code=growth.create_share(peer)['code']
  result=growth.shared(code,owner);self.assertFalse(result['can_invite']);self.assertIn('conflict.listing_language',result['assessment']['hard_conflicts'])
  with self.assertRaises(ValueError):growth.invite_share(owner,code)
 def test_changing_ui_language_does_not_rewrite_listing(self):
  owner,state=self.person('en');original=state['ticket']['profile']
  with patch.object(pilot,'service',self.p):
   client=TestClient(app)
   for ui in ['zh-CN','en']:
    response=client.get('/api/pilot/state',headers={'Authorization':'Bearer '+owner,'Accept-Language':ui})
    self.assertEqual(response.status_code,200);self.assertEqual(response.json()['ticket']['profile'],original)
 def test_contract_requires_language_and_rejects_translated_enum_labels(self):
  from pydantic import ValidationError
  invalid=copy.deepcopy(PLAN);invalid.pop('listing_language')
  with self.assertRaises(ValidationError):RecruitmentV4.model_validate(invalid)
  invalid={**PLAN,'goal':'Loot & extract'}
  with self.assertRaises(ValidationError):RecruitmentV4.model_validate(invalid)
  _,state=self.person('en')
  with self.p.db() as db:
   stored=json.loads(db.execute('SELECT profile FROM tickets WHERE id=?',(state['ticket']['id'],)).fetchone()[0])
  self.assertEqual(stored['listing_language'],'en');self.assertEqual(stored['goal'],'goal.loot_extract');self.assertEqual(stored['maps'][0]['map'],'map.zero_dam')

 def test_communication_language_is_derived_and_cannot_be_overridden(self):
  profile=RecruitmentV4.model_validate({**PLAN,'listing_language':'en','language':'language.zh'}).profile()
  self.assertEqual(profile['language'],'language.en')
  raw={**PLAN,'listing_language':'zh'};raw.pop('language',None)
  self.assertEqual(RecruitmentV4.model_validate(raw).profile()['language'],'language.zh')

 def test_current_recruitment_accepts_only_pc(self):
  from pydantic import ValidationError
  self.assertEqual(RecruitmentV4.model_validate(PLAN).platform,'PC')
  for platform in ['Console','Mobile']:
   with self.assertRaises(ValidationError):RecruitmentV4.model_validate({**PLAN,'platform':platform})
