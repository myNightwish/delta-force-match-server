import copy
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone,timedelta
from unittest.mock import patch
from fastapi.testclient import TestClient
from pydantic import ValidationError
from app.main import app
from app.api.routers import pilot
from app.services.pilot import Pilot,RecruitmentConflictError
from app.services.growth import Growth
from test_recruitment_v4 import PLAN

class RecruitmentReplaceTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.p=Pilot(self.tmp.name+'/replace.db')
    def tearDown(self):self.tmp.cleanup()
    def person(self):
        token=self.p.register()['token'];state=self.p.join(token,copy.deepcopy(PLAN));return token,state['ticket']['id']
    def changed(self):return {**copy.deepcopy(PLAN),'goal':'猛攻','combat_score':75,'strength_range_mode':'percent','strength_tolerance_percent':40}
    def test_replace_ends_old_ticket_and_resets_queue_age_with_server_score(self):
        owner,old=self.person();earlier=(datetime.now(timezone.utc)-timedelta(minutes=10)).isoformat()
        with self.p.db() as db:db.execute('UPDATE tickets SET created=? WHERE id=?',(earlier,old))
        result=self.p.replace(owner,old,{**self.changed(),'strength_score':999})
        self.assertNotEqual(result['ticket']['id'],old);self.assertEqual(result['ticket']['status'],'waiting')
        self.assertGreater(result['ticket']['created'],earlier)
        self.assertEqual((result['ticket']['profile']['strength_score'],result['ticket']['profile']['strength_min'],result['ticket']['profile']['strength_max']),(150,90,210))
        with self.p.db() as db:self.assertEqual(db.execute('SELECT status FROM tickets WHERE id=?',(old,)).fetchone()[0],'replaced')
    def test_invalid_conditions_preserve_existing_ticket(self):
        owner,old=self.person()
        with self.assertRaises(ValidationError):self.p.replace(owner,old,{**self.changed(),'combat_score':None})
        self.assertEqual(self.p.state(owner)['ticket']['id'],old)
        self.assertEqual(self.p.state(owner)['ticket']['status'],'waiting')
    def test_storage_failure_rolls_back_old_new_and_events(self):
        owner,old=self.person();original=self.p._event
        def fail(db,ticket,squad,kind,reason=None):
            if kind=='joined':raise RuntimeError('injected write failure')
            return original(db,ticket,squad,kind,reason)
        with patch.object(self.p,'_event',side_effect=fail),self.assertRaises(RuntimeError):self.p.replace(owner,old,self.changed())
        with self.p.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM tickets WHERE token=?',(owner,)).fetchone()[0],1)
            self.assertEqual(db.execute('SELECT status FROM tickets WHERE id=?',(old,)).fetchone()[0],'waiting')
            self.assertEqual(db.execute("SELECT count(*) FROM pilot_events WHERE kind='replaced'").fetchone()[0],0)
    def test_stale_id_cannot_replace_new_recruitment(self):
        owner,old=self.person();new=self.p.replace(owner,old,self.changed())['ticket']['id']
        with self.assertRaises(RecruitmentConflictError):self.p.replace(owner,old,copy.deepcopy(PLAN))
        self.assertEqual(self.p.state(owner)['ticket']['id'],new)
    def test_cannot_replace_someone_elses_ticket(self):
        a,aid=self.person();b,bid=self.person()
        with self.assertRaises(RecruitmentConflictError):self.p.replace(a,bid,self.changed())
        self.assertEqual(self.p.state(a)['ticket']['id'],aid);self.assertEqual(self.p.state(b)['ticket']['id'],bid)
    def test_proposed_and_ready_squads_cannot_be_replaced(self):
        a,aid=self.person();b,bid=self.person();sid=self.p.invite(a,bid)['squad']['id']
        with self.assertRaises(RecruitmentConflictError):self.p.replace(a,aid,self.changed())
        self.p.action(a,'confirm',expected_squad_id=sid);self.p.action(b,'confirm',expected_squad_id=sid)
        with self.assertRaises(RecruitmentConflictError):self.p.replace(a,aid,self.changed())
        self.assertEqual(self.p.state(a)['squad']['status'],'ready')
    def test_concurrent_replacements_only_one_succeeds(self):
        owner,old=self.person();gate=threading.Barrier(2)
        def replace(_):
            gate.wait()
            try:self.p.replace(owner,old,self.changed());return True
            except RecruitmentConflictError:return False
        with ThreadPoolExecutor(2) as pool:self.assertEqual(sum(pool.map(replace,range(2))),1)
        with self.p.db() as db:self.assertEqual(db.execute("SELECT count(*) FROM tickets WHERE token=? AND status IN ('waiting','matched')",(owner,)).fetchone()[0],1)
    def test_invite_replace_race_never_abandons_reserved_teammate(self):
        for _ in range(8):
            owner,old=self.person();peer,pid=self.person();gate=threading.Barrier(2)
            def replace():
                gate.wait()
                try:return self.p.replace(owner,old,self.changed())
                except RecruitmentConflictError:return None
            def invite():
                gate.wait()
                try:return self.p.invite(peer,old)
                except ValueError:return None
            with ThreadPoolExecutor(2) as pool:
                futures=[pool.submit(replace),pool.submit(invite)];results=[f.result() for f in futures]
            self.assertEqual(sum(value is not None for value in results),1)
            state=self.p.state(owner)
            if state['ticket']['id']==old:self.assertEqual(state['squad']['status'],'proposed')
            else:self.assertEqual(state['ticket']['status'],'waiting');self.assertIsNone(self.p.state(peer)['squad'])
    def test_original_share_link_is_invalidated_only_on_success(self):
        owner,old=self.person();growth=Growth(self.p);code=growth.create_share(owner)['code']
        with self.assertRaises(ValidationError):self.p.replace(owner,old,{**PLAN,'maps':[]})
        self.assertEqual(growth.shared(code)['profile']['goal'],PLAN['goal'])
        self.p.replace(owner,old,self.changed())
        with self.assertRaises(LookupError):growth.shared(code)
    def test_http_validation_conflict_and_auth(self):
        owner,old=self.person();before=pilot.service;pilot.service=self.p
        try:
            c=TestClient(app);headers={'Authorization':'Bearer '+owner};data={'expected_ticket_id':old,'profile':self.changed()}
            self.assertEqual(c.post('/api/pilot/replace',json=data).status_code,401)
            self.assertEqual(c.post('/api/pilot/replace',headers=headers,json={**data,'profile':{**self.changed(),'maps':[]}}).status_code,422)
            self.assertEqual(self.p.state(owner)['ticket']['id'],old)
            result=c.post('/api/pilot/replace',headers=headers,json=data);self.assertEqual(result.status_code,200,result.text)
            stale=c.post('/api/pilot/replace',headers=headers,json=data);self.assertEqual(stale.status_code,409);self.assertEqual(stale.json()['detail']['code'],'recruitment_changed')
        finally:pilot.service=before
