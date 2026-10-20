import tempfile
import unittest
from fastapi.testclient import TestClient
from app.main import app
from app.api.routers import pilot
from app.services.pilot import Pilot
from test_pilot import P

class DiscoveryApiTest(unittest.TestCase):
    def test_candidate_invite_reject_contract(self):
        with tempfile.TemporaryDirectory() as d:
            previous=pilot.service;pilot.service=Pilot(d+'/api.db')
            try:
                c=TestClient(app)
                def person():
                    t=c.post('/api/pilot/participants').json()['token']
                    h={'Authorization':'Bearer '+t}
                    r=c.post('/api/pilot/join',headers=h,json={**P,'public':True,'discovery':'browse'})
                    self.assertEqual(r.status_code,200)
                    return h,r.json()['ticket']['id']
                a,_=person();b,bid=person()
                response=c.get('/api/pilot/candidates',headers=a).json()
                self.assertEqual(response['total'],1)
                self.assertNotIn('game_id',response['items'][0]['profile'])
                s=c.post('/api/pilot/invite',headers=a,json={'candidate_id':bid})
                self.assertEqual(s.status_code,200)
                sid=s.json()['squad']['id']
                self.assertEqual(c.post('/api/pilot/confirm',headers=a,json={'squad_id':sid+1}).status_code,400)
                self.assertEqual(c.post('/api/pilot/reject',headers=b,json={'squad_id':sid,'reason':'skill'}).status_code,200)
                self.assertEqual(c.get('/api/pilot/candidates',headers=a).json()['total'],0)
                self.assertEqual(c.get('/api/pilot/state',headers=a).json()['ticket']['status'],'waiting')
                self.assertEqual(c.get('/api/pilot/metrics').json()['groups']['manual']['decline_reasons']['skill'],1)
            finally:pilot.service=previous
