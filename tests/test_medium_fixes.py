import copy
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
from app.api.routers import pilot
from app.services.pilot import Pilot
from app.services.pilot_chat import PilotChat
from app.services.pilot_limits import PilotLimits, RateLimitError
from test_recruitment_v4 import PLAN

REVIEW=dict(played=True,again='yes',goal_met=True,budget_met=True,skill_met=True,reason='')
class MediumFixTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.p=Pilot(self.tmp.name+'/fixes.db');self.chat=PilotChat(self.p)
 def tearDown(self):self.tmp.cleanup()
 def pair(self):
  a=self.p.register()['token'];b=self.p.register()['token'];self.p.join(a,copy.deepcopy(PLAN));s=self.p.join(b,{**copy.deepcopy(PLAN),'nickname':'旧队友'})
  sid=self.p.invite(a,s['ticket']['id'])['squad']['id'];self.p.action(a,'confirm');self.p.action(b,'confirm');return a,b,sid
 def test_history_survives_next_recruitment_and_chat_remains_accessible(self):
  a,b,sid=self.pair();self.chat.send(b,sid,'交换的信息');self.p.action(a,'feedback',REVIEW);self.p.join(a,copy.deepcopy(PLAN))
  self.assertIsNone(self.p.state(a)['squad']);history=self.p.recent_squads(a)['items'];self.assertEqual(history[0]['id'],sid);self.assertEqual(history[0]['peer_nickname'],'旧队友')
  self.assertEqual(self.chat.messages(a,sid)['items'][0]['content'],'交换的信息');self.chat.send(a,sid,'稍后再约');self.assertEqual(len(self.chat.messages(b,sid)['items']),2)
 def test_history_private_and_cancelled_excluded(self):
  a,b,sid=self.pair();stranger=self.p.register()['token'];self.assertEqual(self.p.recent_squads(stranger)['items'],[])
  with self.assertRaises(PermissionError):self.chat.messages(stranger,sid)
  self.p.action(a,'cancel');self.assertEqual(self.p.recent_squads(a)['items'],[])
 def test_history_api_auth_and_pagination(self):
  a,b,sid=self.pair()
  with self.p.db() as db:
   original=db.execute('SELECT * FROM squads WHERE id=?',(sid,)).fetchone()
   for i in range(12):db.execute('INSERT INTO squads(arm,a,b,status,confirms,feedback,fee_cents,created,version,deadline,agreement_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)',tuple(original[k] for k in ('arm','a','b','status','confirms','feedback','fee_cents','created','version','deadline','agreement_json')))
  previous=pilot.service;pilot.service=self.p
  try:
   c=TestClient(app);headers={'Authorization':'Bearer '+a};self.assertEqual(c.get('/api/pilot/squads/recent').status_code,401)
   first=c.get('/api/pilot/squads/recent',headers=headers).json();second=c.get('/api/pilot/squads/recent?before='+str(first['next_before']),headers=headers).json()
   self.assertEqual(len(first['items']),10);self.assertEqual(len(second['items']),3);self.assertIsNone(second['next_before']);self.assertFalse({x['id'] for x in first['items']}&{x['id'] for x in second['items']});self.assertNotIn('token',str(first));self.assertNotIn('profile',str(first))
  finally:pilot.service=previous
 def test_message_ids_continue_after_all_messages_expire_and_restart(self):
  a,b,sid=self.pair();self.chat.send(a,sid,'old1');old=self.chat.send(b,sid,'old2')['id']
  with self.p.db() as db:db.execute('UPDATE pilot_messages SET created=?',((datetime.now(timezone.utc)-timedelta(days=8)).isoformat(),))
  new=PilotChat(Pilot(self.p.path)).send(a,sid,'new')['id'];self.assertGreater(new,old);self.assertEqual(self.chat.messages(b,sid,old)['items'][0]['id'],new)
 def test_legacy_table_migrates_before_retention_deletes_max_id(self):
  a,b,sid=self.pair()
  with self.p.db() as db:
   db.execute("CREATE TABLE pilot_messages(id INTEGER PRIMARY KEY,squad_id INTEGER NOT NULL,sender_ticket INTEGER NOT NULL,kind TEXT NOT NULL,content TEXT NOT NULL DEFAULT '',audio BLOB,mime TEXT,duration REAL,created TEXT NOT NULL)")
   sender=db.execute('SELECT a FROM squads WHERE id=?',(sid,)).fetchone()[0];db.execute("INSERT INTO pilot_messages(id,squad_id,sender_ticket,kind,content,created) VALUES(90,?,?,'text','expired',?)",(sid,sender,(datetime.now(timezone.utc)-timedelta(days=8)).isoformat()))
  new=self.chat.send(a,sid,'new')['id'];self.assertEqual(new,91);self.assertEqual(self.chat.messages(b,sid,90)['items'][0]['content'],'new')
 def test_audio_ids_and_content_preserved(self):
  a,b,sid=self.pair();voice=self.chat.send(a,sid,audio=b'\x1a\x45\xdf\xa3data',mime='audio/webm',duration=1);self.chat.send(b,sid,'new');self.assertEqual(self.chat.audio(b,sid,voice['id'])[0],b'\x1a\x45\xdf\xa3data')
 def test_rate_limit_persists_and_expires(self):
  policy={'register':{'ip':[(2,60)]}}
  with patch('app.services.pilot_limits.POLICIES',policy),patch('app.services.pilot_limits.time.time',return_value=1000):
   limiter=PilotLimits(self.p);limiter.check('register','ip');limiter.check('register','ip')
   with self.assertRaises(RateLimitError) as error:PilotLimits(Pilot(self.p.path)).check('register','ip')
   self.assertEqual(error.exception.retry_after,60)
  with patch('app.services.pilot_limits.POLICIES',policy),patch('app.services.pilot_limits.time.time',return_value=1061):PilotLimits(self.p).check('register','ip')
 def test_owner_and_address_limits_block_rotation(self):
  a=self.p.register()['token'];b=self.p.register()['token']
  with patch('app.services.pilot_limits.POLICIES',{'join':{'ip':[(2,60)],'owner':[(1,60)]}}):
   limiter=PilotLimits(self.p);limiter.check('join','one',a)
   with self.assertRaises(RateLimitError):limiter.check('join','two',a)
   limiter.check('join','one',b)
   with self.assertRaises(RateLimitError):limiter.check('join','one',self.p.register()['token'])
 def test_rate_checks_atomic_under_concurrency(self):
  with patch('app.services.pilot_limits.POLICIES',{'register':{'ip':[(3,60)]}}):
   def attempt(_):
    try:PilotLimits(self.p).check('register','same');return True
    except RateLimitError:return False
   with ThreadPoolExecutor(max_workers=8) as pool:self.assertEqual(sum(pool.map(attempt,range(8))),3)
 def test_http_429_retry_after_and_no_forwarded_header_bypass(self):
  previous=pilot.service;pilot.service=self.p
  try:
   c=TestClient(app)
   with patch('app.services.pilot_limits.POLICIES',{'register':{'ip':[(1,60)]}}):
    self.assertEqual(c.post('/api/pilot/participants').status_code,200);blocked=c.post('/api/pilot/participants',headers={'X-Forwarded-For':'8.8.8.8'});self.assertEqual(blocked.status_code,429);self.assertEqual(blocked.json()['detail']['code'],'rate_limited');self.assertGreater(int(blocked.headers['Retry-After']),0)
  finally:pilot.service=previous
 def test_failed_invites_count_but_cancellation_not_limited(self):
  a,b,sid=self.pair();previous=pilot.service;pilot.service=self.p
  try:
   c=TestClient(app);headers={'Authorization':'Bearer '+a}
   with patch('app.services.pilot_limits.POLICIES',{'invite':{'ip':[(1,60)],'owner':[(1,60)]}}):
    self.assertEqual(c.post('/api/pilot/invite',headers=headers,json={'candidate_id':99999}).status_code,400);self.assertEqual(c.post('/api/pilot/invite',headers=headers,json={'candidate_id':99999}).status_code,429);self.assertEqual(c.post('/api/pilot/cancel',headers=headers,json={'squad_id':sid}).status_code,200)
  finally:pilot.service=previous

 def test_concurrent_messages_have_unique_increasing_ids(self):
  a,b,sid=self.pair()
  with ThreadPoolExecutor(max_workers=8) as pool:
   ids=list(pool.map(lambda i:PilotChat(self.p).send(a,sid,str(i))['id'],range(8)))
  self.assertEqual(len(set(ids)),8)
  self.assertEqual([m['id'] for m in self.chat.messages(b,sid)['items']],sorted(ids))
 def test_unconfirmed_history_not_exposed(self):
  a=self.p.register()['token'];b=self.p.register()['token'];self.p.join(a,copy.deepcopy(PLAN));state=self.p.join(b,copy.deepcopy(PLAN));self.p.invite(a,state['ticket']['id'])
  self.assertEqual(self.p.recent_squads(a)['items'],[])
