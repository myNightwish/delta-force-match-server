import copy
import tempfile
import unittest
from unittest.mock import patch
from fastapi import HTTPException
from fastapi.testclient import TestClient
from app.main import app
from app.api.routers import pilot
from app.services.pilot import Pilot
from test_recruitment_v4 import PLAN

class LocalizationApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.service=Pilot(self.tmp.name+'/locale.db')
        self.patch=patch.object(pilot,'service',self.service)
        self.patch.start()
        self.client=TestClient(app)
    def tearDown(self):
        self.client.close()
        self.patch.stop()
        self.tmp.cleanup()
    def test_code_is_additive_and_legacy_detail_is_preserved(self):
        response=self.client.get('/api/pilot/state')
        self.assertEqual(response.status_code,401)
        self.assertEqual(response.json()['detail'],'ui.an_anonymous_session_is_required')
        self.assertIn('error_code',response.json())
    def test_specific_business_error_code_is_independent_of_display_locale(self):
        token=self.service.register()['token']
        with patch.object(self.service,'state',side_effect=ValueError('ui.you_already_reserved_another_event_at_this_time')):
            responses=[self.client.get('/api/pilot/state',headers={'Authorization':'Bearer '+token,'Accept-Language':language}) for language in ['zh-CN','en']]
        self.assertEqual(responses[0].json(),responses[1].json())
        self.assertEqual(responses[0].json()['error_code'],'ui.you_already_reserved_another_event_at_this_time')
        self.assertEqual(responses[0].status_code,400)
    def test_rate_limit_code_retains_retry_header_and_legacy_nested_code(self):
        with patch.object(pilot,'guard',side_effect=HTTPException(429,{'code':'rate_limited','message':'ui.too_many_requests_please_try_again_later_633'},headers={'Retry-After':'60'})):
            token=self.service.register()['token']
            response=self.client.get('/api/pilot/state',headers={'Authorization':'Bearer '+token})
        self.assertEqual(response.status_code,429)
        self.assertEqual(response.headers['retry-after'],'60')
        self.assertEqual(response.json()['detail']['code'],'rate_limited')
        self.assertIn('error_code',response.json())
    def test_validation_has_code_and_keeps_legacy_array(self):
        response=self.client.post('/api/pilot/strength-preview',json={})
        self.assertEqual(response.status_code,422)
        self.assertEqual(response.json()['error_code'],'message.validation_failed')
        self.assertIsInstance(response.json()['detail'],list)
    def test_ui_language_never_changes_stored_profile_or_pair_eligibility(self):
        plan=copy.deepcopy(PLAN)
        plan['discovery']='browse'
        plan['nickname']='language.zh'
        tokens=[self.service.register()['token'] for _ in range(2)]
        for token,language in zip(tokens,['zh-CN','en']):
            response=self.client.post('/api/pilot/join',json=plan,headers={'Authorization':'Bearer '+token,'Accept-Language':language})
            self.assertEqual(response.status_code,200)
            profile=response.json()['ticket']['profile']
            self.assertEqual(profile['nickname'],'language.zh')
            self.assertEqual(profile['maps'],plan['maps'])
            self.assertEqual(profile['language'],plan['language'])
        from app.services.recruitment_rules import assessment_v4
        profiles=[self.service.state(token)['ticket']['profile'] for token in tokens]
        self.assertTrue(assessment_v4(*profiles)['eligible'])
