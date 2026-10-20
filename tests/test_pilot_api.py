import tempfile
import unittest
from unittest.mock import patch
from app.core.config import settings
from fastapi.testclient import TestClient
from app.main import app
from app.api.routers import pilot
from app.services.pilot import Pilot
from test_pilot import P,R

class ApiTest(unittest.TestCase):
    def test_real_http_contract(self):
        with tempfile.TemporaryDirectory() as d, patch.object(settings,'PILOT_EXPERIMENT_MODE',True):
            previous=pilot.service;pilot.service=Pilot(d+'/http.db')
            try:
                client=TestClient(app)
                tokens=[client.post('/api/pilot/participants').json()['token'] for _ in range(4)]
                by_arm={}
                for t in tokens:
                    h={'Authorization':'Bearer '+t};s=client.get('/api/pilot/state',headers=h).json()
                    by_arm.setdefault(s['arm'],[]).append(h)
                self.assertEqual(sorted(map(len,by_arm.values())),[2,2])
                a,b=by_arm['B']
                self.assertEqual(client.post('/api/pilot/join',headers=a,json=P).status_code,200)
                self.assertIsNotNone(client.post('/api/pilot/join',headers=b,json=P).json()['squad'])
                self.assertEqual(client.post('/api/pilot/feedback',headers=a,json=R).status_code,400)
                client.post('/api/pilot/confirm',headers=a,json={})
                self.assertEqual(client.post('/api/pilot/confirm',headers=b,json={}).json()['squad']['status'],'ready')
                self.assertEqual(client.post('/api/pilot/feedback',headers=a,json=R).status_code,200)
                self.assertEqual(client.post('/api/pilot/feedback',headers=b,json={**R,'again':'no'}).status_code,200)
                self.assertEqual(client.get('/api/pilot/metrics').json()['groups']['B']['again_rate'],50)
                self.assertEqual(client.get('/api/pilot/state').status_code,401)
                self.assertEqual(client.post('/api/pilot/join',headers=a,json={**P,'budget_min':100}).status_code,422)
            finally:pilot.service=previous
