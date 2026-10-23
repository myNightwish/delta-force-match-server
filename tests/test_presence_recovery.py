import copy
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
from app.main import app
from app.api.routers import pilot
from app.services.pilot import Pilot, PRESENCE_SECONDS
from test_recruitment_v4 import PLAN

class PresenceRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.p=Pilot(self.tmp.name+'/audit.db')
    def tearDown(self):self.tmp.cleanup()
    def person(self,**changes):
        token=self.p.register()['token']
        state=self.p.join(token,{**copy.deepcopy(PLAN),**changes})
        return token,state['ticket']['id']
    def offline(self,token):
        with self.p.db() as db:
            db.execute('UPDATE participants SET last_seen=? WHERE token=?',((datetime.now(timezone.utc)-timedelta(seconds=PRESENCE_SECONDS+1)).isoformat(),token))
    def test_offline_hidden_from_public_and_recommendations(self):
        a,_=self.person();b,bid=self.person();self.offline(b)
        for recommended in (False,True):
            self.assertNotIn(bid,[x['id'] for x in self.p.candidates(a,recommended)['items']])
        self.p.state(b)
        self.assertIn(bid,[x['id'] for x in self.p.candidates(a,True)['items']])
    def test_stale_candidate_invitation_rejected(self):
        a,_=self.person();b,bid=self.person();self.offline(b)
        with self.assertRaisesRegex(ValueError,'ui.this_player_is_offline_refresh_the_candidates'):self.p.invite(a,bid)
        self.assertEqual(self.p.state(a)['ticket']['status'],'waiting')
    def test_auto_does_not_reserve_offline_player_then_resumes(self):
        a,_=self.person(discovery='auto');self.offline(a)
        b,_=self.person(discovery='auto')
        self.assertIsNone(self.p.state(b)['squad'])
        self.assertEqual(self.p.state(a)['squad']['status'],'proposed')
    def test_returning_refresh_preserves_ticket_and_queue_age(self):
        a,aid=self.person();created=self.p.state(a)['ticket']['created'];self.offline(a)
        state=self.p.state(a)
        self.assertEqual((state['ticket']['id'],state['ticket']['created']),(aid,created))
    def test_returning_does_not_revive_expired_recruitment(self):
        a,_=self.person()
        with self.p.db() as db:db.execute('UPDATE tickets SET created=? WHERE token=?',((datetime.now(timezone.utc)-timedelta(minutes=21)).isoformat(),a))
        self.offline(a)
        self.assertEqual(self.p.state(a)['ticket']['status'],'expired')
    def test_presence_does_not_cancel_confirmed_squad(self):
        a,_=self.person();b,bid=self.person();self.p.invite(a,bid);self.p.action(a,'confirm');self.p.action(b,'confirm');self.offline(b)
        self.assertEqual(self.p.state(a)['squad']['status'],'ready')
    def test_old_schema_migrates_without_marking_everyone_online(self):
        path=self.tmp.name+'/old.db'
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE participants(token TEXT PRIMARY KEY,arm TEXT NOT NULL,created TEXT NOT NULL)')
            db.execute('INSERT INTO participants VALUES(?,?,?)',('old','B',Pilot.now()))
        p=Pilot(path)
        with p.db() as db:self.assertFalse(p.online(db,'old'))
        p.state('old')
        with p.db() as db:self.assertTrue(p.online(db,'old'))
    def test_invalid_identity_has_explicit_recovery_code(self):
        previous=pilot.service;pilot.service=self.p
        try:
            client=TestClient(app)
            response=client.get('/api/pilot/state',headers={'Authorization':'Bearer orphaned'})
            self.assertEqual(response.status_code,401)
            self.assertEqual(response.json()['detail']['code'],'anonymous_identity_invalid')
            self.assertEqual(client.get('/api/pilot/state').json()['detail'],'ui.an_anonymous_session_is_required')
            # A malformed join is still a validation error, never an identity reset signal
            response=client.post('/api/pilot/join',json={},headers={'Authorization':'Bearer orphaned'})
            self.assertEqual(response.status_code,422)
        finally:pilot.service=previous
