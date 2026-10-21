import copy
import tempfile
import time
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
from app.api.routers import pilot
from app.services.pilot import Pilot
from app.services.phone_auth import PhoneAuth,PhoneError,clean_profile
from app.core.config import settings
from test_recruitment_v4 import PLAN

class Provider:
 def __init__(self):self.sent=[];self.approved=False
 def request(self,phone):self.sent.append(phone)
 def check(self,phone,code):return self.approved

class PhoneTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.p=Pilot(self.temp.name+'/phone.db');self.provider=Provider();self.auth=PhoneAuth(self.p,self.provider)
  self.enabled=patch.object(PhoneAuth,'available',return_value=True);self.enabled.start()
  self.secret=patch.object(settings,'PHONE_AUTH_SECRET','unit-test-secret-with-32-characters');self.secret.start()
  self.a=self.p.register()['token'];self.b=self.p.register()['token'];self.number='+85291234567'
 def tearDown(self):self.enabled.stop();self.secret.stop();self.temp.cleanup()
 def login(self,guest=None,number=None):
  guest=guest or self.a;number=number or self.number
  self.provider.approved=True
  challenge=self.auth.start(guest,number,'test-ip')
  return self.auth.verify(guest,challenge['challenge_id'],number,'123456')
 def expire_cooldown(self):
  with self.p.db() as db:db.execute('UPDATE phone_challenges SET created=?',(time.time()-65,))
 def test_disabled_never_sends_or_uses_legacy_dev_code(self):
  with patch.object(PhoneAuth,'available',return_value=False):
   self.assertFalse(self.auth.me(self.a)['authenticated'])
   with self.assertRaises(PhoneError) as ctx:self.auth.start(self.a,self.number,'ip')
   self.assertEqual(ctx.exception.status,503)
   with self.assertRaises(PhoneError):self.auth.verify(self.a,'anything',self.number,settings.DEV_SMS_CODE)
   self.assertEqual(self.provider.sent,[])
 def test_first_login_keeps_guest_ticket_and_creates_private_session(self):
  self.p.join(self.a,copy.deepcopy(PLAN));result=self.login();self.assertTrue(result['authenticated']);self.assertTrue(result['token'].startswith('ph_'))
  self.assertEqual(self.auth.resolve(result['token']),self.a)
  self.assertNotIn(self.number,str(result));self.assertEqual(self.auth.me(self.a),{'authenticated':False,'available':True})
  with self.p.db() as db:
   row=db.execute('SELECT * FROM phone_sessions').fetchone();self.assertNotEqual(row['digest'],result['token'])
   self.assertNotIn(self.number,str(dict(db.execute('SELECT * FROM phone_accounts').fetchone())))
 def test_profile_sync_between_independent_sessions(self):
  first=self.login();self.auth.save_profile(first['token'],{'nickname':'已保存','platform':'Mobile','helmet':4,'confidential_kd':1.5,'goal':'not-personal','token':'never-store'})
  self.expire_cooldown();second=self.login(self.b)
  self.assertNotEqual(first['token'],second['token']);self.assertEqual(self.auth.resolve(second['token']),self.a)
  self.assertEqual(second['profile'],{'nickname':'已保存','platform':'Mobile','helmet':4,'confidential_kd':1.5})
  with self.assertRaises(PhoneError):self.auth.save_profile(self.a,{'nickname':'no-session'})
 def test_challenge_bound_to_number_and_browser_and_consumed_once(self):
  challenge=self.auth.start(self.a,self.number,'ip')['challenge_id'];self.provider.approved=True
  for guest,number in [(self.b,self.number),(self.a,'+85299998888')]:
   with self.assertRaises(PhoneError):self.auth.verify(guest,challenge,number,'123456')
  self.auth.verify(self.a,challenge,self.number,'123456')
  with self.assertRaises(PhoneError):self.auth.verify(self.a,challenge,self.number,'123456')
 def test_wrong_codes_count_and_expiration_and_rate_limit(self):
  challenge=self.auth.start(self.a,self.number,'ip')['challenge_id']
  with self.assertRaises(PhoneError) as ctx:self.auth.start(self.a,self.number,'ip')
  self.assertEqual(ctx.exception.status,429)
  for _ in range(5):
   with self.assertRaises(PhoneError):self.auth.verify(self.a,challenge,self.number,'123456')
  with self.assertRaises(PhoneError) as ctx:self.auth.verify(self.a,challenge,self.number,'123456')
  self.assertEqual(ctx.exception.status,429)
  with self.p.db() as db:db.execute('UPDATE phone_challenges SET expires=0')
  with self.assertRaises(PhoneError):self.auth.verify(self.a,challenge,self.number,'123456')
 def test_does_not_switch_away_from_active_guest_queue(self):
  self.login();self.expire_cooldown();self.p.join(self.b,copy.deepcopy(PLAN))
  with self.assertRaises(PhoneError) as ctx:self.login(self.b)
  self.assertEqual(ctx.exception.status,409);self.assertEqual(self.p.state(self.b)['ticket']['status'],'waiting')
 def test_session_expiry_and_logout_only_revokes_one_device(self):
  first=self.login();self.expire_cooldown();second=self.login(self.b)
  new_guest=self.auth.logout(second['token'])['token'];self.assertNotEqual(new_guest,self.b)
  with self.assertRaises(PhoneError):self.auth.resolve(second['token'])
  self.assertEqual(self.auth.resolve(first['token']),self.a)
  with self.p.db() as db:db.execute('UPDATE phone_sessions SET expires=0')
  with self.assertRaises(PhoneError):self.auth.resolve(first['token'])
 def test_profile_validation(self):
  for value in [{'helmet':7},{'manual_score':float('inf')},{'style':2.5},{'nickname':'a'*25},{'platform':'other'}]:
   with self.assertRaises(PhoneError):clean_profile(value)
  self.assertEqual(clean_profile({'nickname':'  hi  ','helmet':None}),{'nickname':'hi','helmet':None})
 def test_http_login_session_can_access_matching_without_changing_guest_api(self):
  previous=pilot.service;pilot.service=self.p
  try:
   c=TestClient(app);h={'Authorization':'Bearer '+self.a}
   with patch('app.services.phone_auth.TwilioVerify.request',return_value={'status':'pending'}),patch('app.services.phone_auth.TwilioVerify.check',return_value=True):
    sent=c.post('/api/pilot/auth/phone/request',headers=h,json={'phone':self.number});self.assertEqual(sent.status_code,200)
    response=c.post('/api/pilot/auth/phone/verify',headers=h,json={'phone':self.number,'code':'123456','challenge_id':sent.json()['challenge_id']})
   self.assertEqual(response.status_code,200,response.text)
   session={'Authorization':'Bearer '+response.json()['token']}
   self.assertEqual(c.post('/api/pilot/join',headers=session,json=PLAN).status_code,200)
   self.assertEqual(c.get('/api/pilot/state',headers=h).json()['ticket']['id'],c.get('/api/pilot/state',headers=session).json()['ticket']['id'])
   self.assertEqual(c.post('/api/pilot/auth/profile',headers=session,json={'profile':{'nickname':'跨设备'}}).status_code,200)
   self.assertEqual(c.get('/api/pilot/auth/me',headers=session).json()['profile']['nickname'],'跨设备')
   self.assertEqual(c.post('/api/pilot/auth/profile',headers=h,json={'profile':{}}).status_code,401)
  finally:pilot.service=previous

class AdapterTests(unittest.TestCase):
 def test_configuration_stays_disabled_without_real_settings(self):
  with patch.object(settings,'PHONE_AUTH_ENABLED',True),patch.object(settings,'PHONE_AUTH_SECRET',''),patch.object(settings,'TWILIO_ACCOUNT_SID',''):
   self.assertFalse(PhoneAuth.available())
 def test_verify_adapter_posts_form_and_only_accepts_approved(self):
  from app.services.phone_auth import TwilioVerify
  import httpx
  with patch.object(settings,'TWILIO_VERIFY_SERVICE_SID','VA'+'a'*32),patch('app.services.phone_auth.httpx.post',return_value=httpx.Response(200,json={'status':'approved'})) as request:
   self.assertTrue(TwilioVerify().check('+85291234567','123456'))
   self.assertTrue(request.call_args.args[0].endswith('/VerificationCheck'))
   self.assertEqual(request.call_args.kwargs['data'],{'To':'+85291234567','Code':'123456'})
  with patch('app.services.phone_auth.httpx.post',return_value=httpx.Response(404,json={})):
   self.assertFalse(TwilioVerify().check('+85291234567','123456'))
 def test_provider_error_is_not_success(self):
  from app.services.phone_auth import TwilioVerify
  import httpx
  with patch('app.services.phone_auth.httpx.post',return_value=httpx.Response(401,json={'secret':'must not leak'})):
   with self.assertRaises(PhoneError) as ctx:TwilioVerify().request('+85291234567')
   self.assertEqual(ctx.exception.status,503);self.assertNotIn('secret',str(ctx.exception))
