import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
from app.api.routers import pilot
from app.core.config import settings
from app.services.pilot import Pilot

class DeploymentTest(unittest.TestCase):
    def test_production_cors_and_legacy_api(self):
        c=TestClient(app)
        origin='https://river-delta-web.onrender.com'
        r=c.options('/api/pilot/join',headers={'Origin':origin,'Access-Control-Request-Method':'POST','Access-Control-Request-Headers':'authorization,content-type'})
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.headers['access-control-allow-origin'],origin)
        bad=c.options('/api/pilot/join',headers={'Origin':'https://not-allowed.example','Access-Control-Request-Method':'POST'})
        self.assertEqual(bad.status_code,400)
        self.assertEqual(c.post('/api/pay/notify',json={}).status_code,404)
        self.assertEqual(c.post('/api/auth/code',json={}).status_code,404)
        self.assertIn('version',c.get('/health').json())
    def test_production_metrics_requires_private_token(self):
        with tempfile.TemporaryDirectory() as d, patch.object(settings,'APP_ENV','production'), patch.object(settings,'PILOT_METRICS_TOKEN','test-private-token'):
            previous=pilot.service;pilot.service=Pilot(d+'/metrics.db')
            try:
                c=TestClient(app)
                self.assertEqual(c.get('/api/pilot/metrics').status_code,403)
                self.assertEqual(c.get('/api/pilot/metrics',headers={'Authorization':'Bearer wrong'}).status_code,403)
                self.assertEqual(c.get('/api/pilot/metrics',headers={'Authorization':'Bearer test-private-token'}).status_code,200)
            finally:pilot.service=previous
