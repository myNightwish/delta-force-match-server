import tempfile
import unittest
from pathlib import Path
from pydantic import ValidationError
from app.api.routers.pilot import Preferences
from app.services.pilot import Pilot, assessment, compatible

BASE = dict(nickname='ui.player',game_id='private',region='NA',platform='PC',language='language.zh',map='map.zero_dam',difficulty='difficulty.normal',voice='voice.mic',slot='slot.next_hour',goal='goal.missions',task='task.investigate',armor=3,ammo=3,style=2,partner_style=4,confidential_kd=1.5,top_secret_kd=2,combat_score=50,strength_min=0,strength_max=200,discovery='browse',public=True)

class RecruitmentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.pilot=Pilot(Path(self.temp.name)/'pilot.db')
    def tearDown(self): self.temp.cleanup()
    def join(self, **kwargs):
        token=self.pilot.register()['token']
        return token,self.pilot.join(token,{**BASE,**kwargs})
    def test_score_is_computed_and_bilateral_conditions_allow_invite(self):
        a,sa=self.join(strength_score=999999)
        b,sb=self.join(style=4,partner_style=2)
        self.assertEqual(sa['ticket']['profile']['strength_score'],90)
        self.assertTrue(assessment(sa['ticket']['profile'],sb['ticket']['profile'])['eligible'])
        proposed=self.pilot.invite(a,sb['ticket']['id'])
        self.assertEqual(proposed['squad']['status'],'proposed')
        self.assertNotIn('game_id',proposed['squad']['members'][1]['profile'])
    def test_task_gear_style_and_reverse_strength_each_block(self):
        _,a=self.join()
        for change in [dict(task='task.place'),dict(ammo=4),dict(armor=4),dict(partner_style=1),dict(strength_min=100)]:
            _,b=self.join(style=4,**change)
            self.assertFalse(assessment(a['ticket']['profile'],b['ticket']['profile'])['eligible'])
            self.assertTrue(compatible(a['ticket']['profile'],b['ticket']['profile'],'A'))
    def test_knife_clears_irrelevant_equipment_and_task(self):
        _,a=self.join(goal='goal.budget_run')
        _,b=self.join(goal='goal.budget_run',style=4,partner_style=2,armor=5,ammo=1)
        p=a['ticket']['profile']
        self.assertIsNone(p['task']); self.assertIsNone(p['armor']); self.assertIsNone(p['ammo'])
        self.assertTrue(assessment(p,b['ticket']['profile'])['eligible'])
    def test_new_and_old_criteria_do_not_silently_match(self):
        _,a=self.join()
        legacy={**a['ticket']['profile'],'goal':'goal.legacy_tasks'}
        self.assertFalse(assessment(a['ticket']['profile'],legacy)['eligible'])
    def test_auto_uses_the_same_bilateral_rules(self):
        _,a=self.join(discovery='auto')
        _,wrong=self.join(discovery='auto',style=4,partner_style=2,task='task.place')
        self.assertIsNone(wrong['squad'])
        _,right=self.join(discovery='auto',style=4,partner_style=2)
        self.assertEqual(right['squad']['members'][0]['id'],right['ticket']['id'])
        self.assertIn(a['ticket']['id'],[m['id'] for m in right['squad']['members']])
    def test_demo_metadata_remains_excluded_from_metrics(self):
        _,a=self.join(is_demo=True,demo_key='fixture')
        self.assertTrue(a['ticket']['profile']['is_demo'])
        self.assertEqual(self.pilot.metrics()['groups']['manual']['requests'],0)
    def test_without_game_id_can_publish_match_confirm_and_chat(self):
        from fastapi.testclient import TestClient
        from app.main import app
        from app.api.routers import pilot
        from app.services.pilot_chat import PilotChat
        previous=pilot.service
        pilot.service=self.pilot
        try:
            client=TestClient(app)
            def publish(style,partner):
                credential=client.post('/api/pilot/participants').json()['token']
                headers={'Authorization':'Bearer '+credential}
                payload={k:v for k,v in BASE.items() if k!='game_id'}
                response=client.post('/api/pilot/join',headers=headers,json={**payload,'style':style,'partner_style':partner})
                self.assertEqual(response.status_code,200,response.text)
                self.assertEqual(response.json()['ticket']['profile']['game_id'],'')
                return credential,response.json()['ticket']['id']
            a,_=publish(2,4)
            b,bid=publish(4,2)
            sid=self.pilot.invite(a,bid)['squad']['id']
            self.pilot.action(a,'confirm',expected_squad_id=sid)
            self.pilot.action(b,'confirm',expected_squad_id=sid)
            chat=PilotChat(self.pilot)
            chat.send(a,sid,'你好，我们在这里交换游戏信息')
            self.assertEqual(chat.messages(b,sid)['items'][0]['content'],'你好，我们在这里交换游戏信息')
        finally:
            pilot.service=previous
    def test_different_game_clients_remain_incompatible(self):
        _,a=self.join()
        _,b=self.join(style=4,partner_style=2,platform='Mobile')
        self.assertFalse(assessment(a['ticket']['profile'],b['ticket']['profile'])['eligible'])
    def test_required_and_finite_inputs_and_range(self):
        for change in [dict(task=None),dict(armor=None),dict(style=6),dict(confidential_kd=float('nan')),dict(strength_min=201),dict(combat_score=None)]:
            with self.assertRaises(ValidationError): Preferences.model_validate({**BASE,**change})
