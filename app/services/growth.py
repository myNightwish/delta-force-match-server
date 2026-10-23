"""Public recruitment links, opt-in scheduled sessions and private activation metrics."""
import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from statistics import median
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Literal
from app.services.pilot import assessment

class ActivityIn(BaseModel):
    title: str = Field(min_length=2,max_length=48)
    starts_at: datetime
    duration_minutes: int = Field(default=120,ge=30,le=240)
    capacity: int = Field(default=30,ge=2,le=200)
    region: Literal['NA','EU','SEA','OCE','ME']
    platform: Literal['PC','Console','Mobile']
    language: Literal['language.zh','language.en','language.ja']
    goal: Literal['goal.missions','goal.budget_run','goal.loot_extract','goal.aggressive']

    @field_validator('title')
    @classmethod
    def clean_title(cls,v):
        v=v.strip()
        if len(v)<2:raise ValueError('ui.enter_an_event_name')
        return v

    @model_validator(mode='after')
    def valid_time(self):
        now=datetime.now(timezone.utc)
        if self.starts_at.tzinfo is None:raise ValueError('ui.event_times_must_include_a_time_zone')
        self.starts_at=self.starts_at.astimezone(timezone.utc)
        if not now+timedelta(minutes=5)<=self.starts_at<=now+timedelta(days=30):
            raise ValueError('ui.choose_a_start_time_between_5_minutes_and_30')
        return self

class Growth:
    def __init__(self,pilot):
        self.p=pilot
        with pilot.db() as db:
            self.schema(db)

    @staticmethod
    def schema(db):
        for sql in [
            'CREATE TABLE IF NOT EXISTS growth_visits(owner TEXT PRIMARY KEY,first_at TEXT NOT NULL,last_at TEXT NOT NULL,source TEXT NOT NULL)',
            'CREATE TABLE IF NOT EXISTS recruitment_shares(code TEXT PRIMARY KEY,ticket_id INTEGER UNIQUE NOT NULL,created TEXT NOT NULL)',
            'CREATE TABLE IF NOT EXISTS growth_activities(id INTEGER PRIMARY KEY,title TEXT NOT NULL,starts_at TEXT NOT NULL,ends_at TEXT NOT NULL,capacity INTEGER NOT NULL,segment TEXT NOT NULL,status TEXT NOT NULL,created TEXT NOT NULL)',
            'CREATE TABLE IF NOT EXISTS growth_reservations(activity_id INTEGER NOT NULL,owner TEXT NOT NULL,status TEXT NOT NULL,created TEXT NOT NULL,PRIMARY KEY(activity_id,owner))',
            'CREATE INDEX IF NOT EXISTS growth_activity_time ON growth_activities(status,ends_at)',
            'CREATE INDEX IF NOT EXISTS growth_res_owner ON growth_reservations(owner,status)',
        ]:db.execute(sql)

    def _demo(self,db,owner):
        return db.execute('SELECT 1 FROM pilot_test_people WHERE owner=?',(owner,)).fetchone() is not None

    def visit(self,owner,source):
        with self.p.db() as db:
            self.p.participant(db,owner)
            if not self._demo(db,owner):
                db.execute('INSERT INTO growth_visits VALUES(?,?,?,?) ON CONFLICT(owner) DO UPDATE SET last_at=excluded.last_at',(owner,self.p.now(),self.p.now(),source))
        return {'ok':True}

    def create_share(self,owner):
        with self.p.db() as db:
            self.p.participant(db,owner,touch=True);self.p._expire(db)
            row=self.p._latest(db,owner)
            if not row or row['status']!='waiting' or json.loads(row['profile']).get('schema_version')!=4 or not json.loads(row['profile']).get('public') or self._demo(db,owner):
                raise ValueError('ui.publish_a_public_listing_first_test_identities_cannot_share')
            code=secrets.token_urlsafe(24)
            db.execute('INSERT INTO recruitment_shares VALUES(?,?,?) ON CONFLICT(ticket_id) DO NOTHING',(code,row['id'],self.p.now()))
            code=db.execute('SELECT code FROM recruitment_shares WHERE ticket_id=?',(row['id'],)).fetchone()[0]
            return {'code':code}

    def _shared(self,db,code):
        if not re.fullmatch(r'[A-Za-z0-9_-]{32}',code):raise LookupError('ui.this_listing_link_does_not_exist_or_has_expired')
        row=db.execute('SELECT t.* FROM recruitment_shares s JOIN tickets t ON t.id=s.ticket_id WHERE s.code=?',(code,)).fetchone()
        if not row or self._demo(db,row['token']):raise LookupError('ui.this_listing_link_does_not_exist_or_has_expired')
        p=json.loads(row['profile'])
        if not p.get('public') or row['status']!='waiting':raise LookupError('ui.this_listing_has_ended_or_its_slot_is_reserved')
        return row,p

    def shared(self,code,owner=None):
        with self.p.db() as db:
            self.p._expire(db);row,p=self._shared(db,code)
            mine=None
            if owner:
                self.p.participant(db,owner);mine=self.p._latest(db,owner)
            # Public allowlist: no bearer, phone, raw KD, private feedback or contact information.
            keys=('schema_version','listing_language','nickname','region','platform','language','voice','slot','goal','tasks','maps','map','difficulty','helmet','armor','ammo','style','strength_score','strength_min','strength_max','partner_style_min','partner_style_max','partner_helmet_min','partner_armor_min','partner_ammo_min')
            check=assessment(json.loads(mine['profile']),p) if mine else None
            online=self.p.online(db,row['token'])
            return {'profile':{k:p[k] for k in keys if k in p},'online':online,'is_me':row['token']==owner,'assessment':check,
                    'can_invite':bool(mine and mine['status']=='waiting' and row['token']!=owner and online and check['eligible'] and self.p.same_pool(db,owner,row['token']) and not self.p._blocked(db,owner,row['token'])),
                    'expires_at':(datetime.fromisoformat(row['created'])+timedelta(minutes=20)).isoformat()}

    def invite_share(self,owner,code):
        with self.p.db() as db:
            self.p.participant(db,owner,touch=True);self.p._expire(db)
            row,_=self._shared(db,code);mine=self.p._latest(db,owner)
            if not mine:raise ValueError('ui.publish_your_conditions_first_664')
            self.p._reserve(db,mine,row,'manual')
            return self.p._state(db,owner,auto=False)

    def create_activity(self,data):
        starts=data.starts_at;ends=starts+timedelta(minutes=data.duration_minutes)
        with self.p.db() as db:
            if db.execute("SELECT count(*) FROM growth_activities WHERE status='open' AND ends_at>?",(self.p.now(),)).fetchone()[0]>=20:
                raise ValueError('ui.at_most_20_events_can_be_open_at_once')
            cur=db.execute('INSERT INTO growth_activities(title,starts_at,ends_at,capacity,segment,status,created) VALUES(?,?,?,?,?,\'open\',?)',(data.title,starts.isoformat(),ends.isoformat(),data.capacity,json.dumps({k:getattr(data,k) for k in ('region','platform','language','goal')},ensure_ascii=False),self.p.now()))
            return {'id':cur.lastrowid}

    def activities(self,owner):
        with self.p.db() as db:
            self.p.participant(db,owner)
            rows=db.execute("SELECT a.*,(SELECT count(*) FROM growth_reservations r WHERE r.activity_id=a.id AND r.status='reserved' AND NOT EXISTS(SELECT 1 FROM pilot_test_people p WHERE p.owner=r.owner)) AS reserved,(SELECT status FROM growth_reservations r WHERE r.activity_id=a.id AND r.owner=?) AS mine FROM growth_activities a WHERE (a.status='open' OR EXISTS(SELECT 1 FROM growth_reservations r WHERE r.activity_id=a.id AND r.owner=? AND r.status='reserved')) AND a.ends_at>? ORDER BY a.starts_at,a.id LIMIT 20",(owner,owner,self.p.now())).fetchall()
            return {'items':[dict(status=r['status'],id=r['id'],title=r['title'],starts_at=r['starts_at'],ends_at=r['ends_at'],capacity=r['capacity'],reserved=r['reserved'],mine=r['mine']=='reserved',segment=json.loads(r['segment'])) for r in rows],'server_now':self.p.now()}

    def reserve_activity(self,owner,activity_id,cancel=False):
        with self.p.db() as db:
            self.p.participant(db,owner)
            row=db.execute('SELECT * FROM growth_activities WHERE id=?',(activity_id,)).fetchone()
            if not row:raise LookupError('ui.event_not_found')
            if cancel:
                db.execute("UPDATE growth_reservations SET status='cancelled' WHERE activity_id=? AND owner=?",(activity_id,owner))
                return {'ok':True}
            if self._demo(db,owner):raise ValueError('ui.test_identities_cannot_reserve_events')
            if row['status']!='open' or row['ends_at']<=self.p.now():raise ValueError('ui.this_event_has_ended_or_been_cancelled')
            existing=db.execute('SELECT status FROM growth_reservations WHERE activity_id=? AND owner=?',(activity_id,owner)).fetchone()
            if existing and existing['status']=='reserved':return {'ok':True}
            if db.execute("SELECT count(*) FROM growth_reservations r WHERE activity_id=? AND status='reserved' AND NOT EXISTS(SELECT 1 FROM pilot_test_people p WHERE p.owner=r.owner)",(activity_id,)).fetchone()[0]>=row['capacity']:raise ValueError('ui.no_reservation_slots_remain')
            overlap=db.execute("SELECT 1 FROM growth_reservations r JOIN growth_activities a ON a.id=r.activity_id WHERE r.owner=? AND r.status='reserved' AND a.status='open' AND a.starts_at<? AND a.ends_at>?",(owner,row['ends_at'],row['starts_at'])).fetchone()
            if overlap:raise ValueError('ui.you_already_reserved_another_event_at_this_time')
            db.execute("INSERT INTO growth_reservations VALUES(?,?,'reserved',?) ON CONFLICT(activity_id,owner) DO UPDATE SET status='reserved',created=excluded.created",(activity_id,owner,self.p.now()))
            return {'ok':True}

    def close_activity(self,activity_id):
        with self.p.db() as db:
            if not db.execute('SELECT 1 FROM growth_activities WHERE id=?',(activity_id,)).fetchone():raise LookupError('ui.event_not_found')
            db.execute("UPDATE growth_activities SET status='cancelled' WHERE id=?",(activity_id,))
        return {'ok':True}

    def metrics(self):
        with self.p.db() as db:
            demos={r[0] for r in db.execute('SELECT owner FROM pilot_test_people')}
            tickets={r['id']:r for r in db.execute('SELECT * FROM tickets') if r['token'] not in demos and not json.loads(r['profile']).get('is_demo')}
            visits={r['owner']:r for r in db.execute('SELECT * FROM growth_visits') if r['owner'] not in demos}
            # Only publication after first tracked visit counts towards this funnel.
            cohort={tid:t for tid,t in tickets.items() if t['token'] in visits and t['created']>=visits[t['token']]['first_at']}
            published={t['token'] for t in cohort.values()}
            confirmations={}
            for e in db.execute("SELECT squad_id,MAX(created) AS at FROM pilot_events WHERE kind='confirmed' GROUP BY squad_id HAVING COUNT(DISTINCT ticket_id)=2"):
                confirmations[e['squad_id']]=e['at']
            confirmed=set();wait_by_ticket={};review_by_ticket={}
            for squad in db.execute('SELECT * FROM squads ORDER BY id'):
                if squad['a'] not in tickets or squad['b'] not in tickets:continue
                if squad['id'] in confirmations:
                    for tid in (squad['a'],squad['b']):
                        if tid in cohort:
                            confirmed.add(tid);wait_by_ticket.setdefault(tid,max(0,(datetime.fromisoformat(confirmations[squad['id']])-datetime.fromisoformat(tickets[tid]['created'])).total_seconds()))
                    for tid,r in json.loads(squad['feedback']).items():
                        if int(tid) in cohort:review_by_ticket.setdefault(int(tid),r)
            waits=list(wait_by_ticket.values());reviews=list(review_by_ticket.items())
            played={tid for tid,r in reviews if r['played']};willing={tid for tid,r in reviews if r['played'] and r['again']=='yes'}
            counts={o:sum(t['token']==o for t in cohort.values()) for o in published}
            repeat=sum(n>=2 for n in counts.values())
            pct=lambda n,d:round(n/d*100,1) if d else None
            within=sum(v<=300 for v in waits)
            return {'since':min((v['first_at'] for v in visits.values()),default=None),'visitors':len(visits),'publishers':len(published),'publication_rate':pct(len(published),len(visits)),'requests':len(cohort),'confirmed_requests':len(confirmed),'confirmation_rate':pct(len(confirmed),len(cohort)),'confirmed_within_5min':within,'within_5min_rate':pct(within,len(cohort)),'median_confirm_seconds':round(median(waits),1) if waits else None,'feedback_requests':len(reviews),'played_requests':len(played),'reported_play_rate':pct(len(played),len(reviews)),'feedback_coverage':pct(len(reviews),len(confirmed)),'willing_again_requests':len(willing),'willing_again_rate':pct(len(willing),len(played)),'repeat_publishers':repeat,'repeat_publication_rate':pct(repeat,len(published)),'notice':'metrics.countingNotice'}
