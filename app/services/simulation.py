"""Isolated, expiring simulated peers using the normal matching and chat services."""
import copy
import json
import logging
import secrets
import time
from pydantic import ValidationError
from app.schemas.recruitment_v4 import RecruitmentV4
from app.core.config import settings
from app.services.pilot import assessment
from app.services.pilot_chat import PilotChat
from app.services.recruitment_rules import MAP_MODES
from app.services.testing_tools import TestingTools
from app.services.phone_auth import PhoneError

log=logging.getLogger(__name__)
BEHAVIORS={'accept','reject','timeout'}

class Simulation:
    def __init__(self,pilot):self.pilot=pilot;self.tools=TestingTools(pilot)
    @staticmethod
    def schema(db):
        db.execute('CREATE TABLE IF NOT EXISTS pilot_sim_runs(id TEXT PRIMARY KEY,controller TEXT NOT NULL,actor TEXT NOT NULL,status TEXT NOT NULL,behavior TEXT NOT NULL,expires REAL NOT NULL,created TEXT NOT NULL)')
        db.execute('CREATE INDEX IF NOT EXISTS pilot_sim_actor ON pilot_sim_runs(actor,status)')
        db.execute('CREATE TABLE IF NOT EXISTS pilot_sim_bots(owner TEXT PRIMARY KEY,run_id TEXT NOT NULL,label TEXT NOT NULL,expected INTEGER NOT NULL,greeted_squad INTEGER)')
    def _run(self,db,owner):
        return db.execute('SELECT * FROM pilot_sim_runs WHERE actor=? OR controller=? ORDER BY rowid DESC LIMIT 1',(owner,owner)).fetchone()
    @staticmethod
    def normalize(profile):
        if len(json.dumps(profile,ensure_ascii=False).encode())>16384:raise ValueError('条件内容过大')
        p=copy.deepcopy(profile)
        for key in ('manual_score','confidential_kd','top_secret_kd','combat_score','strength_min','strength_max'):
            if p.get(key)=='':p[key]=None
        try:return RecruitmentV4.model_validate(p).model_dump()
        except ValidationError as e:raise ValueError('当前条件未填写完整：'+str(e.errors()[0]['msg']))
    @staticmethod
    def peers(profile):
        me=RecruitmentV4.model_validate(profile).profile();base=copy.deepcopy(profile)
        score=min(1e9,max(me['strength_min'],min(me['strength_score'],me['strength_max'])))
        base.update(score_mode='manual',manual_score=score,strength_range_mode='custom',strength_min=me['strength_score'],strength_max=me['strength_score'],style=max(me['partner_style_min'],min(me['style'],me['partner_style_max'])),partner_style_min=me['style'],partner_style_max=me['style'],public=True)
        for k in ('helmet','armor','ammo'):
            if me['goal']!='卡战备跑刀':base['partner_'+k+'_min']=me[k]
        plans=[]
        def add(label,expected,**changes):
            p={**copy.deepcopy(base),**changes,'nickname':'【测试】'+label}
            if p['goal']!='卡战备跑刀':
                for k in ('helmet','armor','ammo'):
                    if p.get(k) is None:p[k]=4
                    if p.get('partner_'+k+'_min') is None:p['partner_'+k+'_min']=1
            p=RecruitmentV4.model_validate(p).model_dump()
            plans.append((label,expected,p))
        add('兼容队友一',True);add('兼容队友二',True)
        add('目标不同',False,goal='猛攻' if me['goal']!='猛攻' else '搜打撤',tasks=[])
        pair=next({'map':m,'difficulty':d} for m,ds in MAP_MODES.items() for d in ds if {'map':m,'difficulty':d} not in me['maps'])
        add('战场不同',False,maps=[pair])
        # If my range accepts every possible score, make the peer reject my score instead
        if me['strength_max']<1e9:add('实力不符',False,manual_score=min(1e9,me['strength_max']+1))
        elif me['strength_min']>0:add('实力不符',False,manual_score=max(0,me['strength_min']-1))
        else:
            other=0 if me['strength_score'] else 1;add('实力不符',False,strength_min=other,strength_max=other)
        style=1 if me['style']!=1 else 5
        add('反向打法不符',False,partner_style_min=style,partner_style_max=style)
        gear_changes=None
        if me['goal']!='卡战备跑刀':
            for k in ('helmet','armor','ammo'):
                if me['partner_'+k+'_min']>1:
                    value=me['partner_'+k+'_min']-1;gear_changes={k:value,'partner_'+k+'_min':value};break
                if me[k]<6:
                    gear_changes={k:6,'partner_'+k+'_min':me[k]+1};break
        if gear_changes:add('装备不符',False,**gear_changes)
        else:add('语音方式不同',False,voice='听指挥与标记' if me['voice']=='开麦' else '开麦')
        add('区服不同',False,region='EU' if me['region']!='EU' else 'NA')
        # Refuse a broken generated fixture instead of showing a misleading expectation
        for label,expected,p in plans:
            if assessment(me,RecruitmentV4.model_validate(p).profile())['eligible']!=expected:raise ValueError('无法根据当前条件构建场景：'+label)
        return plans
    def start(self,owner,access,profile=None,behavior='accept'):
        if behavior not in BEHAVIORS:raise ValueError('请选择模拟队友响应方式')
        with self.pilot.db() as db:
            self.schema(db);self.tools.authorize(db,owner,access)
            old=self._run(db,owner)
            controller=old['controller'] if old and old['actor']==owner else owner
            active=self.pilot._latest(db,owner)
            is_test=db.execute('SELECT 1 FROM pilot_test_people WHERE owner=?',(owner,)).fetchone()
            if active and active['status'] in ('waiting','matched') and not is_test:raise PhoneError(409,'请先结束真实招募，再开始模拟测试')
            count=db.execute("SELECT count(*) FROM pilot_sim_runs WHERE status='active' AND expires>?",(time.time(),)).fetchone()[0]
            if count>=5 and (not old or old['status']!='active'):raise PhoneError(429,'当前测试轮次较多，请稍后重试')
        if profile is None:profile=self.tools.scenarios(owner,access)['scenarios'][0]['players'][0]
        clean=self.normalize(profile);plans=self.peers(clean)
        if old and old['status']=='active':self._stop(old['id'])
        actor=self.pilot.register()['token'];run_id=secrets.token_urlsafe(18)
        clean['nickname']='【测试】我';clean.pop('game_id',None)
        with self.pilot.db() as db:
            self.schema(db)
            if db.execute("SELECT count(*) FROM pilot_sim_runs WHERE status='active' AND expires>?",(time.time(),)).fetchone()[0]>=5:raise PhoneError(429,'当前测试轮次较多，请稍后重试')
            db.execute("INSERT INTO pilot_sim_runs VALUES(?,?,?,'active',?,?,?)",(run_id,controller,actor,behavior,time.time()+900,self.pilot.now()))
            db.execute('INSERT INTO pilot_test_people(owner,pool_id,is_bot) VALUES(?,?,0)',(actor,run_id))
            new_access=self.tools.grant(db,actor)
        try:
            self.pilot.join(actor,clean)
            for label,expected,plan in plans:
                bot=self.pilot.register()['token']
                with self.pilot.db() as db:
                    db.execute('INSERT INTO pilot_test_people(owner,pool_id,is_bot) VALUES(?,?,1)',(bot,run_id))
                    db.execute('INSERT INTO pilot_sim_bots(owner,run_id,label,expected) VALUES(?,?,?,?)',(bot,run_id,label,int(expected)))
                self.pilot.join(bot,plan)
        except Exception:
            self._stop(run_id);raise
        return {'token':actor,'access_token':new_access,'profile':clean,'state':self.pilot.state(actor),'run':self.current(actor,new_access)}
    def _stop(self,run_id):
        with self.pilot.db() as db:
            self.schema(db)
            if not db.execute("SELECT 1 FROM pilot_sim_runs WHERE id=? AND status='active'",(run_id,)).fetchone():return
            owners=[r['owner'] for r in db.execute('SELECT owner FROM pilot_test_people WHERE pool_id=?',(run_id,))]
            for owner in owners:
                ticket=self.pilot._latest(db,owner)
                squad=self.pilot._squad(db,ticket)
                if squad and squad['status'] in ('proposed','ready'):self.pilot._release(db,squad,'cancelled')
                db.execute("UPDATE tickets SET status='cancelled' WHERE token=? AND status IN ('waiting','matched')",(owner,))
            db.execute("UPDATE pilot_sim_runs SET status='stopped' WHERE id=?",(run_id,))
    def stop(self,owner,access):
        with self.pilot.db() as db:
            self.schema(db);self.tools.authorize(db,owner,access);run=self._run(db,owner)
        if run:self._stop(run['id'])
        return {'ok':True}
    def current(self,owner,access):
        with self.pilot.db() as db:
            self.schema(db);self.tools.authorize(db,owner,access);run=self._run(db,owner)
            if not run or run['status']!='active':return None
            if run['expires']<=time.time():expired=run['id']
            else:
                actor=self.pilot._latest(db,run['actor']);me=json.loads(actor['profile']);bots=[]
                for row in db.execute('SELECT * FROM pilot_sim_bots WHERE run_id=? ORDER BY rowid',(run['id'],)):
                    ticket=self.pilot._latest(db,row['owner']);p=json.loads(ticket['profile']);check=assessment(me,p)
                    bots.append({'nickname':p['nickname'],'label':row['label'],'expected_eligible':bool(row['expected']),'eligible':check['eligible'],'status':ticket['status'],'checks':check['checks'],'hard_conflicts':check['hard_conflicts']})
                return {'id':run['id'],'behavior':run['behavior'],'remaining_seconds':max(0,int(run['expires']-time.time())),'bots':bots}
        self._stop(expired);return None
    def tick(self):
        with self.pilot.db() as db:
            self.schema(db);runs=db.execute("SELECT * FROM pilot_sim_runs WHERE status='active'").fetchall()
        for run in runs:
            if run['expires']<=time.time() or len(settings.PILOT_TEST_ACCESS_CODE)<12:
                self._stop(run['id']);continue
            with self.pilot.db() as db:
                bots=db.execute('SELECT * FROM pilot_sim_bots WHERE run_id=?',(run['id'],)).fetchall()
            for bot in bots:
                try:
                    # A stopped run must never be revived by a late background tick
                    with self.pilot.db() as db:
                        if not db.execute("SELECT 1 FROM pilot_sim_runs WHERE id=? AND status='active'",(run['id'],)).fetchone():break
                        db.execute('UPDATE participants SET last_seen=? WHERE token=?',(self.pilot.now(),bot['owner']))
                    state=self.pilot.state(bot['owner']);squad=state['squad']
                    if squad and squad['status']=='proposed':
                        if run['behavior']=='accept':state=self.pilot.action(bot['owner'],'confirm',expected_squad_id=squad['id'])
                        elif run['behavior']=='reject':state=self.pilot.action(bot['owner'],'reject',{'reason':'other'},squad['id'])
                    squad=state['squad']
                    if squad and squad['status']=='ready' and bot['greeted_squad']!=squad['id']:
                        PilotChat(self.pilot).send(bot['owner'],squad['id'],'【测试消息】我已确认，可以在这里交换游戏信息')
                        with self.pilot.db() as db:db.execute('UPDATE pilot_sim_bots SET greeted_squad=? WHERE owner=?',(squad['id'],bot['owner']))
                except (ValueError,PermissionError):
                    # A simultaneous cancel/cleanup or an expired reservation is normal
                    continue
                except Exception:log.exception('模拟队友处理失败')
