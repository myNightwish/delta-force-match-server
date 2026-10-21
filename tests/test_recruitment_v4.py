import copy
import json
import tempfile
import unittest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from app.main import app
from app.api.routers import pilot
from app.schemas.recruitment_v4 import RecruitmentV4
from app.services.pilot import Pilot, assessment
from app.services.pilot_chat import PilotChat
from test_recruitment import BASE as OLD

PLAN=dict(schema_version=4,nickname='向导测试',region='NA',platform='PC',language='中文',voice='开麦',slot='现在起一小时',goal='搜打撤',tasks=[],maps=[dict(map='零号大坝',difficulty='普通')],helmet=4,armor=4,ammo=4,style=3,partner_style_min=1,partner_style_max=5,partner_helmet_min=2,partner_armor_min=2,partner_ammo_min=2,score_mode='auto',confidential_kd=2,top_secret_kd=2,combat_score=50,strength_range_mode='auto',discovery='browse',public=True)

class V4Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.p=Pilot(self.tmp.name+'/v4.db')
 def tearDown(self):self.tmp.cleanup()
 def join(self,**patch):
  t=self.p.register()['token'];s=self.p.join(t,{**copy.deepcopy(PLAN),**patch});return t,s
 def test_different_gear_and_styles_satisfy_bilateral_requirements(self):
  a,sa=self.join(style=2,partner_style_min=4,partner_style_max=5)
  b,sb=self.join(helmet=3,armor=3,ammo=3,partner_helmet_min=3,partner_armor_min=3,partner_ammo_min=3,style=4,partner_style_min=1,partner_style_max=3)
  self.assertTrue(assessment(sa['ticket']['profile'],sb['ticket']['profile'])['eligible'])
  self.assertEqual(self.p.invite(a,sb['ticket']['id'])['squad']['status'],'proposed')
 def test_reverse_style_and_gear_block(self):
  a,sa=self.join(style=2,helmet=2,partner_helmet_min=1)
  _,sb=self.join(helmet=5,partner_helmet_min=3,partner_style_min=3)
  check=assessment(sa['ticket']['profile'],sb['ticket']['profile'])
  self.assertFalse(check['eligible'])
  failed={c['label'] for c in check['checks'] if not c['ok']}
  self.assertIn('我的装备达到对方门槛',failed);self.assertIn('我的打法符合对方期望',failed)
 def test_auto_range_recomputed_not_trusted(self):
  _,s=self.join(strength_min=999,strength_max=999,strength_score=999)
  p=s['ticket']['profile'];self.assertEqual((p['strength_score'],p['strength_min'],p['strength_max']),(100,70,130))
  _,b=self.join(score_mode='manual',manual_score=70)
  self.assertFalse(assessment(p,b['ticket']['profile'])['eligible'])
  _,c=self.join(score_mode='manual',manual_score=130)
  self.assertTrue(assessment(p,c['ticket']['profile'])['eligible'])
 def test_custom_range_and_zero(self):
  _,a=self.join(strength_range_mode='custom',strength_min=0,strength_max=200)
  _,b=self.join(score_mode='manual',manual_score=50,strength_range_mode='custom',strength_min=90,strength_max=110)
  self.assertTrue(assessment(a['ticket']['profile'],b['ticket']['profile'])['eligible'])
  _,z=self.join(score_mode='manual',manual_score=0)
  self.assertEqual(z['ticket']['profile']['strength_min'],0)
  self.assertEqual(z['ticket']['profile']['strength_max'],0)
 def test_map_and_task_intersections_are_frozen_in_agreement(self):
  a,sa=self.join(goal='33任务',tasks=['调查','击杀干员'],maps=[dict(map='零号大坝',difficulty='普通'),dict(map='巴克什',difficulty='绝密')])
  b,sb=self.join(goal='33任务',tasks=['放置','击杀干员'],maps=[dict(map='零号大坝',difficulty='机密'),dict(map='巴克什',difficulty='绝密')])
  s=self.p.invite(a,sb['ticket']['id']);ag=s['squad']['agreement']
  self.assertEqual((ag['map'],ag['difficulty']),('巴克什','绝密'));self.assertEqual(ag['tasks'],['击杀干员'])
  self.assertEqual(ag,Pilot(self.p.path).state(b)['squad']['agreement'])
  self.p.action(a,'confirm');self.p.action(b,'confirm')
  chat=PilotChat(self.p);chat.send(a,s['squad']['id'],'去巴克什绝密')
  self.assertEqual(chat.messages(b,s['squad']['id'])['items'][0]['content'],'去巴克什绝密')
 def test_same_map_different_mode_or_disjoint_tasks_do_not_match(self):
  _,a=self.join(goal='33任务',tasks=['调查'])
  _,b=self.join(goal='33任务',tasks=['放置'],maps=[dict(map='零号大坝',difficulty='机密')])
  self.assertFalse(assessment(a['ticket']['profile'],b['ticket']['profile'])['eligible'])
 def test_knife_ignores_both_equipment_requirements(self):
  a,sa=self.join(goal='卡战备跑刀',helmet=None,armor=None,ammo=None,partner_helmet_min=None,partner_armor_min=None,partner_ammo_min=None)
  _,sb=self.join(goal='卡战备跑刀')
  self.assertTrue(assessment(sa['ticket']['profile'],sb['ticket']['profile'])['eligible'])
  self.assertIsNone(sb['ticket']['profile']['helmet']);self.assertIsNone(sb['ticket']['profile']['partner_helmet_min'])
 def test_auto_uses_new_rules_across_shared_pool(self):
  a,sa=self.join(discovery='auto',style=2,partner_style_min=4,partner_style_max=5)
  b,sb=self.join(discovery='auto',style=4,partner_style_min=1,partner_style_max=3)
  self.assertIsNotNone(sb['squad']);self.assertEqual(sb['squad']['agreement']['map'],'零号大坝')
 def test_old_tickets_preserved_but_not_silently_mixed(self):
  _,a=self.join()
  t=self.p.register()['token'];b=self.p.join(t,OLD)
  self.assertFalse(assessment(a['ticket']['profile'],b['ticket']['profile'])['eligible'])
  with self.p.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM tickets').fetchone()[0],2)
 def test_schema_rejects_invalid_and_missing_values(self):
  cases=[dict(maps=[]),dict(maps=[dict(map='巴克什',difficulty='普通')]),dict(maps=PLAN['maps']*2),dict(goal='33任务',tasks=[]),dict(tasks=['invalid']),dict(helmet=7),dict(helmet=None),dict(partner_helmet_min=5),dict(partner_style_min=5,partner_style_max=2),dict(manual_score=-1,score_mode='manual'),dict(score_mode='manual',manual_score=float('inf')),dict(strength_range_mode='custom',strength_min=100,strength_max=90)]
  for patch in cases:
   with self.subTest(patch=patch),self.assertRaises(ValidationError):RecruitmentV4.model_validate({**copy.deepcopy(PLAN),**patch})
 def test_http_schema_cannot_fall_back_to_legacy_validation(self):
  previous=pilot.service;pilot.service=self.p
  try:
   c=TestClient(app);t=c.post('/api/pilot/participants').json()['token'];h={'Authorization':'Bearer '+t}
   invalid={**PLAN,**{k:v for k,v in OLD.items() if k not in PLAN},'helmet':None}
   self.assertEqual(c.post('/api/pilot/join',headers=h,json=invalid).status_code,422)
   response=c.post('/api/pilot/join',headers=h,json=PLAN)
   self.assertEqual(response.status_code,200,response.text);self.assertEqual(response.json()['ticket']['profile']['schema_version'],4)
  finally:pilot.service=previous
