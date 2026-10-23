"""Two-person matching: private plans, opt-in public recruitment and atomic reservations."""
import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta

from app.core.config import settings

VERSION = 'two-person-recruitment-v4'
PRESENCE_SECONDS = 75

class RecruitmentConflictError(ValueError):
    pass

class InvalidIdentityError(ValueError):
    pass

HARD_FIELDS = [('region', '区服'), ('platform', '平台'), ('language', '语言'),
               ('map', '地图'), ('difficulty', '难度'), ('voice', '语音方式'), ('slot', '开局时段')]
REASONS = {'goal': '目标不同', 'budget': '装备预算不合适', 'skill': '实力预期不符',
           'communication': '沟通方式不合适', 'time': '时间安排变化', 'other': '其他原因'}


def assessment(a, b):
    if a.get('schema_version') == 4 or b.get('schema_version') == 4:
        if a.get('schema_version') != b.get('schema_version'):
            return {'eligible':False,'hard_conflicts':['招募条件版本不同，请更新本局条件'],'checks':[],'skill_gap':None}
        from app.services.recruitment_rules import assessment_v4
        return assessment_v4(a,b)
    hard = [label + '不一致' for field, label in HARD_FIELDS if a[field] != b[field]]
    gap = abs(a['skill'] - b['skill'])
    checks = [
        {'label': '本局目标一致', 'ok': a['goal'] == b['goal']},
        {'label': '装备预算有交集', 'ok': max(a['budget_min'], b['budget_min']) <= min(a['budget_max'], b['budget_max'])},
        {'label': '对方实力在我接受范围内', 'ok': gap <= a['gap']},
        {'label': '我的实力在对方接受范围内', 'ok': gap <= b['gap']},
    ]
    if a['goal'] in ('33任务','跑刀','猛攻') or b['goal'] in ('33任务','跑刀','猛攻'):
        checks[2:4] = [{'label':'对方实力符合我的范围','ok': b.get('strength_score') is not None and a.get('strength_min') is not None and a.get('strength_max') is not None and a['strength_min'] <= b['strength_score'] <= a['strength_max']},
                       {'label':'我的实力符合对方范围','ok': a.get('strength_score') is not None and b.get('strength_min') is not None and b.get('strength_max') is not None and b['strength_min'] <= a['strength_score'] <= b['strength_max']}]
        checks[1] = {'label': '装备等级一致', 'ok': a['goal'] == b['goal'] and (a['goal'] == '跑刀' or all(a.get(k) is not None and a.get(k) == b.get(k) for k in ('armor','ammo')))}
        if a['goal'] == b['goal'] == '33任务':
            checks.append({'label':'任务类型一致','ok': bool(a.get('task')) and a.get('task') == b.get('task')})
        checks.extend([{'label':'对方打法符合我的期望','ok': a.get('partner_style') is not None and a.get('partner_style') == b.get('style')},
                       {'label':'我的打法符合对方期望','ok': b.get('partner_style') is not None and b.get('partner_style') == a.get('style')}])
    return {'eligible': not hard and all(c['ok'] for c in checks), 'hard_conflicts': hard,
            'checks': checks, 'skill_gap': gap, 'data_source': '玩家自报，未核验'}


def compatible(a, b, arm):
    check = assessment(a, b)
    reasons = list(check['hard_conflicts'])
    if a.get('schema_version') == 4 or b.get('schema_version') == 4:
        return reasons + [c['label']+'不满足' for c in check['checks'] if not c['ok']]
    if a['goal'] in ('33任务','跑刀','猛攻') or b['goal'] in ('33任务','跑刀','猛攻'):
        return reasons + [c['label'] + '不满足' for c in check['checks'] if not c['ok']]
    if arm != 'A':
        if a['goal'] != b['goal']:
            reasons.append('本局目标不同')
        if not check['checks'][1]['ok']:
            reasons.append('装备预算没有交集')
        if not all(c['ok'] for c in check['checks'][2:]):
            reasons.append('超过双方接受的实力差距')
    return reasons


class Pilot:
    def __init__(self, path=None):
        self.path = str(path or os.getenv('PILOT_DB', settings.PILOT_DB))
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS participants(token TEXT PRIMARY KEY, arm TEXT NOT NULL, created TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS tickets(id INTEGER PRIMARY KEY, token TEXT NOT NULL, arm TEXT NOT NULL, profile TEXT NOT NULL, status TEXT NOT NULL, created TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS squads(id INTEGER PRIMARY KEY, arm TEXT NOT NULL, a INTEGER NOT NULL, b INTEGER NOT NULL, status TEXT NOT NULL, confirms TEXT NOT NULL, feedback TEXT NOT NULL, fee_cents INTEGER NOT NULL, created TEXT NOT NULL, version TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS pilot_events(id INTEGER PRIMARY KEY, ticket_id INTEGER, squad_id INTEGER, kind TEXT NOT NULL, reason TEXT, created TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS pilot_exclusions(a TEXT NOT NULL, b TEXT NOT NULL, until_at TEXT NOT NULL, PRIMARY KEY(a,b));
                CREATE INDEX IF NOT EXISTS pilot_waiting ON tickets(status,arm);
                CREATE INDEX IF NOT EXISTS pilot_token ON tickets(token,id);
                CREATE INDEX IF NOT EXISTS pilot_squad_a ON squads(a);
                CREATE INDEX IF NOT EXISTS pilot_squad_b ON squads(b);
            ''')
            participant_columns = {r['name'] for r in db.execute('PRAGMA table_info(participants)')}
            if 'last_seen' not in participant_columns:
                db.execute('ALTER TABLE participants ADD COLUMN last_seen TEXT')
            columns = {r['name'] for r in db.execute('PRAGMA table_info(squads)')}
            if 'deadline' not in columns:
                db.execute('ALTER TABLE squads ADD COLUMN deadline TEXT')
            if 'agreement_json' not in columns:
                db.execute('ALTER TABLE squads ADD COLUMN agreement_json TEXT')
            db.execute('CREATE TABLE IF NOT EXISTS pilot_test_people(owner TEXT PRIMARY KEY)')
            test_columns={r['name'] for r in db.execute('PRAGMA table_info(pilot_test_people)')}
            if 'pool_id' not in test_columns:db.execute('ALTER TABLE pilot_test_people ADD COLUMN pool_id TEXT')
            if 'is_bot' not in test_columns:db.execute('ALTER TABLE pilot_test_people ADD COLUMN is_bot INTEGER NOT NULL DEFAULT 0')
            # Keep historical records; old unconfirmed reservations also receive a deadline.
            db.execute("UPDATE squads SET deadline=created WHERE status='proposed' AND deadline IS NULL")

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def now():
        return datetime.now(timezone.utc).isoformat()

    def config(self):
        return {'currency': 'USD', 'fee_cents': int(os.getenv('PILOT_FEE_CENTS', str(settings.PILOT_FEE_CENTS))),
                'charge_enabled': False, 'free_pilot': settings.PILOT_FREE_MODE,
                'experiment_enabled': settings.PILOT_EXPERIMENT_MODE, 'confirm_seconds': 120, 'queue_minutes': 20, 'version': VERSION}

    def register(self):
        token = secrets.token_urlsafe(32)
        with self.db() as db:
            n = db.execute('SELECT COUNT(*) FROM participants').fetchone()[0]
            if n % 2:
                last = db.execute('SELECT arm FROM participants ORDER BY rowid DESC LIMIT 1').fetchone()[0]
                arm = 'B' if last == 'A' else 'A'
            else:
                arm = secrets.choice(['A', 'B'])
            db.execute('INSERT INTO participants(token,arm,created,last_seen) VALUES(?,?,?,?)', (token, arm, self.now(), self.now()))
        return {'token': token}

    def participant(self, db, token, touch=False):
        p = db.execute('SELECT * FROM participants WHERE token=?', (token,)).fetchone()
        if not p:
            raise InvalidIdentityError('体验身份已失效')
        if touch:
            db.execute('UPDATE participants SET last_seen=? WHERE token=?', (self.now(), token))
        return p

    @staticmethod
    def presence_cutoff():
        return (datetime.now(timezone.utc) - timedelta(seconds=PRESENCE_SECONDS)).isoformat()

    def online(self, db, token):
        return db.execute('SELECT 1 FROM participants WHERE token=? AND last_seen>?', (token, self.presence_cutoff())).fetchone() is not None

    def same_pool(self, db, a, b):
        rows=db.execute('SELECT owner,pool_id,is_bot FROM pilot_test_people WHERE owner IN (?,?)',(a,b)).fetchall()
        people={row['owner']:row for row in rows}
        pa,pb=people.get(a),people.get(b)
        return (pa['pool_id'] if pa else None)==(pb['pool_id'] if pb else None) and not (pa and pb and pa['is_bot'] and pb['is_bot'])

    def _available_candidates(self, db, token, arm=None):
        # Filter presence, isolated test pools and cooldowns in one query.
        # Keep _reserve's independent checks inside the same write transaction.
        query="""SELECT t.* FROM tickets t
            JOIN participants p ON p.token=t.token
            LEFT JOIN pilot_test_people peer ON peer.owner=t.token
            LEFT JOIN pilot_test_people me ON me.owner=?
            WHERE t.status='waiting' AND t.token<>? AND p.last_seen>?
              AND peer.pool_id IS me.pool_id
              AND NOT (COALESCE(peer.is_bot,0)<>0 AND COALESCE(me.is_bot,0)<>0)
              AND NOT EXISTS (SELECT 1 FROM pilot_exclusions e
                WHERE e.a=CASE WHEN ?<t.token THEN ? ELSE t.token END
                  AND e.b=CASE WHEN ?<t.token THEN t.token ELSE ? END
                  AND e.until_at>?)"""
        params=[token,token,self.presence_cutoff(),token,token,token,token,self.now()]
        if arm is not None:
            query+=' AND t.arm=?';params.append(arm)
        return db.execute(query+' ORDER BY t.created,t.id',params).fetchall()

    def _latest(self, db, token):
        return db.execute('SELECT * FROM tickets WHERE token=? ORDER BY id DESC LIMIT 1', (token,)).fetchone()

    @staticmethod
    def _squad(db, ticket):
        return db.execute('SELECT * FROM squads WHERE a=? OR b=? ORDER BY id DESC LIMIT 1',
                          (ticket['id'], ticket['id'])).fetchone() if ticket else None

    def _event(self, db, ticket, squad, kind, reason=None):
        db.execute('INSERT INTO pilot_events(ticket_id,squad_id,kind,reason,created) VALUES(?,?,?,?,?)',
                   (ticket, squad, kind, reason, self.now()))

    def _exclude(self, db, a, b):
        a, b = sorted([a, b])
        until = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
        db.execute('INSERT INTO pilot_exclusions VALUES(?,?,?) ON CONFLICT(a,b) DO UPDATE SET until_at=excluded.until_at', (a, b, until))

    def _blocked(self, db, a, b):
        a, b = sorted([a, b])
        return db.execute('SELECT 1 FROM pilot_exclusions WHERE a=? AND b=? AND until_at>?',
                          (a, b, self.now())).fetchone() is not None

    def _release(self, db, squad, status, actor=None, reason=None):
        db.execute('UPDATE squads SET status=? WHERE id=?', (status, squad['id']))
        members = [db.execute('SELECT * FROM tickets WHERE id=?', (tid,)).fetchone()
                   for tid in (squad['a'], squad['b'])]
        self._exclude(db, members[0]['token'], members[1]['token'])
        for m in members:
            # Reuse the original ticket/queue time. Do not revive a completed request.
            db.execute("UPDATE tickets SET status='waiting' WHERE id=? AND status='matched'", (m['id'],))
            self._event(db, m['id'], squad['id'], status, reason if actor == m['id'] else None)

    def _expire(self, db):
        for s in db.execute("SELECT * FROM squads WHERE status='proposed' AND deadline<=?", (self.now(),)).fetchall():
            self._release(db, s, 'timed_out')
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
        db.execute("UPDATE tickets SET status='expired' WHERE status='waiting' AND created<?", (cutoff,))
        db.execute('DELETE FROM pilot_exclusions WHERE until_at<=?', (self.now(),))

    def _reserve(self, db, a, b, source):
        if a['status'] != 'waiting' or b['status'] != 'waiting':
            raise ValueError('该玩家已被其他组局保留，请刷新候选')
        if not self.same_pool(db,a['token'],b['token']):
            raise ValueError('测试候选池不同，不能组队')
        if a['token'] == b['token']:
            raise ValueError('不能与自己组队')
        if self._blocked(db, a['token'], b['token']):
            raise ValueError('近期已跳过该候选，请选择其他玩家')
        if not self.online(db, a['token']) or not self.online(db, b['token']):
            raise ValueError('该玩家暂时不在线，请刷新候选')
        pa, pb = json.loads(a['profile']), json.loads(b['profile'])
        strategy = a['arm'] if source == 'auto' and self.config()['experiment_enabled'] else 'B'
        conflicts = compatible(pa, pb, strategy)
        if conflicts:
            raise ValueError('无法组局：' + '；'.join(conflicts))
        if source == 'auto' and self.config()['experiment_enabled'] and a['arm'] != b['arm']:
            raise ValueError('自动匹配必须在同一试验组内')
        deadline = (datetime.now(timezone.utc) + timedelta(seconds=self.config()['confirm_seconds'])).isoformat()
        arm = (a['arm'] if self.config()['experiment_enabled'] else 'auto') if source == 'auto' else 'manual'
        cur = db.execute("INSERT INTO squads(arm,a,b,status,confirms,feedback,fee_cents,created,version,deadline) VALUES(?,?,?,'proposed','[]','{}',?,?,?,?)",
                         (arm, a['id'], b['id'], 0 if self.config()['free_pilot'] else self.config()['fee_cents'], self.now(), VERSION, deadline))
        if pa.get('schema_version') == pb.get('schema_version') == 4:
            from app.services.recruitment_rules import agreement_v4
            db.execute('UPDATE squads SET agreement_json=? WHERE id=?',(json.dumps(agreement_v4(pa,pb),ensure_ascii=False),cur.lastrowid))
        db.execute("UPDATE tickets SET status='matched' WHERE id IN (?,?)", (a['id'], b['id']))
        for t in (a, b):
            self._event(db, t['id'], cur.lastrowid, 'proposed', source)

    def _auto(self, db, ticket):
        if ticket['status'] != 'waiting':
            return
        profile = json.loads(ticket['profile'])
        if profile.get('discovery', 'auto') != 'auto':
            return
        experiment=self.config()['experiment_enabled']
        candidates=[]
        for row in self._available_candidates(db,ticket['token'],ticket['arm'] if experiment else None):
            other=json.loads(row['profile'])
            if other.get('discovery','auto')=='auto' and not compatible(profile,other,ticket['arm'] if experiment else 'B'):
                candidates.append((row,other))
        if candidates:
            best=min(candidates,key=lambda pair:(abs(profile.get('strength_score',profile['rank'])-pair[1].get('strength_score',pair[1]['rank'])),pair[0]['created'],pair[0]['id']))
            self._reserve(db,ticket,best[0],'auto')

    def _state(self, db, token, auto=True):
        self._expire(db)
        p = self.participant(db, token, touch=True)
        ticket = self._latest(db, token)
        if ticket and auto:
            self._auto(db, ticket)
            ticket = self._latest(db, token)
        result = {'arm': p['arm'], 'ticket': None, 'squad': None, 'config': self.config(), 'server_now': self.now(), 'notice': None}
        if not ticket:
            return result
        result['ticket'] = {'id': ticket['id'], 'status': ticket['status'], 'created': ticket['created'],
                            'profile': json.loads(ticket['profile'])}
        event = db.execute("SELECT kind FROM pilot_events WHERE ticket_id=? AND kind IN ('rejected','timed_out','cancelled') ORDER BY id DESC LIMIT 1", (ticket['id'],)).fetchone()
        if event:
            result['notice'] = {'rejected': '上一组候选已被婉拒，已保留排队进度。', 'timed_out': '上一组确认已超时，名额已释放，排队进度保留。', 'cancelled': '上一组已取消，未退出成员保留排队进度。'}[event['kind']]
        # Waiting tickets may have historical proposals, but should not display those as active.
        squad = self._squad(db, ticket) if ticket['status'] != 'waiting' else None
        if squad:
            members = []
            confirms, feedback = json.loads(squad['confirms']), json.loads(squad['feedback'])
            for tid in [squad['a'], squad['b']]:
                row = db.execute('SELECT * FROM tickets WHERE id=?', (tid,)).fetchone()
                profile = json.loads(row['profile'])
                if row['token'] != token and squad['status'] not in ('ready', 'done'):
                    profile.pop('game_id', None)
                members.append({'id': tid, 'profile': profile, 'confirmed': str(tid) in confirms, 'is_me': row['token'] == token})
            a, b = [m['profile'] for m in members]
            result['squad'] = {'id': squad['id'], 'status': squad['status'], 'members': members,
                               'source': 'manual' if squad['arm'] == 'manual' else 'auto',
                               'deadline': squad['deadline'], 'fee_cents': squad['fee_cents'],
                               'feedback_submitted': str(ticket['id']) in feedback,
                               'assessment': assessment(a, b), 'agreement': {
                                   'goal': a['goal'] if a['goal'] == b['goal'] else '未统一：' + a['goal'] + ' / ' + b['goal'],
                                   'budget_min': max(a['budget_min'], b['budget_min']),
                                   'budget_max': min(a['budget_max'], b['budget_max']),
                                   'warnings': compatible(a, b, 'B'),
                                   'scope': '平台仅匹配一位队友，不控制官方对手；游戏内关闭自动补队。'}}
            if squad['agreement_json']:
                result['squad']['agreement']=json.loads(squad['agreement_json'])
        return result

    def state(self, token):
        with self.db() as db:
            return self._state(db, token)

    def _validated_profile(self, profile):
        if profile.get('schema_version') == 4:
            from app.schemas.recruitment_v4 import RecruitmentV4
            demo_metadata = {k:profile[k] for k in ('is_demo','demo_key') if k in profile}
            profile = {**RecruitmentV4.model_validate(profile).profile(), **demo_metadata}
        elif profile.get('goal') in ('33任务','跑刀','猛攻'):
            from app.schemas.pilot_preferences import Preferences
            demo_metadata = {k: profile[k] for k in ('is_demo','demo_key') if k in profile}
            profile = {**Preferences.model_validate(profile).model_dump(), **demo_metadata}
            profile['strength_score'] = round((profile['confidential_kd'] * .4 + profile['top_secret_kd'] * .6) * profile['combat_score'], 2)
        return {**profile, 'rules_version': VERSION, 'matching_mode': 'experiment' if self.config()['experiment_enabled'] else 'shared'}

    def join(self, token, profile):
        profile=self._validated_profile(profile)
        with self.db() as db:
            p = self.participant(db, token, touch=True)
            if db.execute('SELECT 1 FROM pilot_test_people WHERE owner=?',(token,)).fetchone():
                profile['is_demo']=True
                profile['nickname']='【测试】'+profile['nickname'].removeprefix('【测试】')[:20]
            self._expire(db)
            if db.execute("SELECT id FROM tickets WHERE token=? AND status IN ('waiting','matched')", (token,)).fetchone():
                raise ValueError('已有进行中的组局，请先完成或取消')
            cur = db.execute("INSERT INTO tickets(token,arm,profile,status,created) VALUES(?,?,?,'waiting',?)",
                             (token, p['arm'], json.dumps(profile, ensure_ascii=False), self.now()))
            self._event(db, cur.lastrowid, None, 'joined', profile.get('discovery', 'auto'))
            return self._state(db, token)

    def replace(self, token, expected_ticket_id, profile):
        # Validate first; all old/new ticket changes and auto reservation commit together.
        profile=self._validated_profile(profile)
        with self.db() as db:
            participant=self.participant(db,token,touch=True)
            self._expire(db)
            current=self._latest(db,token)
            if not current or current['id']!=expected_ticket_id:
                raise RecruitmentConflictError('招募已变化，请查看当前招募后重新选择')
            if current['status']=='matched':
                raise RecruitmentConflictError('已匹配到队友，请先处理现有队伍')
            if current['status']!='waiting':
                raise RecruitmentConflictError('原招募已结束，请返回后重新发布')
            if db.execute("SELECT 1 FROM tickets WHERE token=? AND id<>? AND status IN ('waiting','matched')",(token,current['id'])).fetchone():
                raise RecruitmentConflictError('存在其他进行中的招募，请先查看当前招募')
            if db.execute('SELECT 1 FROM pilot_test_people WHERE owner=?',(token,)).fetchone():
                profile['is_demo']=True
                profile['nickname']='【测试】'+profile['nickname'].removeprefix('【测试】')[:20]
            db.execute("UPDATE tickets SET status='replaced' WHERE id=?",(current['id'],))
            self._event(db,current['id'],None,'replaced')
            cur=db.execute("INSERT INTO tickets(token,arm,profile,status,created) VALUES(?,?,?,'waiting',?)",(token,participant['arm'],json.dumps(profile,ensure_ascii=False),self.now()))
            self._event(db,cur.lastrowid,None,'joined',profile.get('discovery','auto'))
            return self._state(db,token)

    def candidates(self, token, recommended=True, page=0, query=""):
        with self.db() as db:
            self.participant(db, token, touch=True)
            self._expire(db)
            mine = self._latest(db, token)
            profile = json.loads(mine['profile']) if mine else None
            rows = self._available_candidates(db,token)
            result = []
            for row in rows:
                other = json.loads(row['profile'])
                if not other.get('public', False):
                    continue
                if query.strip() and query.strip().casefold() not in other.get('nickname','').casefold():
                    continue
                check = assessment(profile, other) if profile else None
                if recommended and (not check or not check['eligible']):
                    continue
                # No game IDs, bearer credentials, decline reasons or feedback in public cards.
                other.pop('game_id', None)
                result.append({'id': row['id'], 'profile': other, 'assessment': check,
                               'created': row['created'], 'can_invite': bool(mine and mine['status'] == 'waiting' and check['eligible'])})
            if profile:
                result.sort(key=lambda c: (bool(c['profile'].get('is_demo')), not c['assessment']['eligible'],
                    abs(profile.get('strength_score',profile['rank']) - c['profile'].get('strength_score',c['profile']['rank'])), c['created'], c['id']))
            else:
                result.sort(key=lambda c:(bool(c['profile'].get('is_demo')), c['created'],c['id']))
            return {'items': result[page*6:page*6+6], 'total': len(result), 'page': page,
                    'can_recommend': profile is not None, 'my_status':mine['status'] if mine else None, 'server_now': self.now()}

    def invite(self, token, candidate_id):
        with self.db() as db:
            self.participant(db, token, touch=True)
            self._expire(db)
            mine = self._latest(db, token)
            other = db.execute('SELECT * FROM tickets WHERE id=?', (candidate_id,)).fetchone()
            if not mine or not other:
                raise ValueError('请先发布本局条件，并刷新候选')
            if not json.loads(other['profile']).get('public', False):
                raise ValueError('该玩家未公开招募')
            self._reserve(db, mine, other, 'manual')
            return self._state(db, token, auto=False)

    def skip(self, token, candidate_ids):
        with self.db() as db:
            self.participant(db, token, touch=True)
            self._expire(db)
            mine = self._latest(db, token)
            if not mine or mine['status'] != 'waiting':
                raise ValueError('只有排队时可以换一批')
            for tid in set(candidate_ids):
                other = db.execute("SELECT * FROM tickets WHERE id=? AND status='waiting' AND token<>?", (tid, token)).fetchone()
                if other and self.same_pool(db,token,other['token']) and json.loads(other['profile']).get('public', False):
                    self._exclude(db, token, other['token'])
            self._event(db, mine['id'], None, 'batch_changed')
            return {'ok': True}

    def action(self, token, action, feedback=None, expected_squad_id=None):
        with self.db() as db:
            # Do not create another proposal while handling a stale confirm/reject request.
            state = self._state(db, token, auto=False)
            ticket, squad = state['ticket'], state['squad']
            if not ticket:
                raise ValueError('尚未提交组局')
            if expected_squad_id is not None and (not squad or squad['id'] != expected_squad_id):
                raise ValueError('队伍已变化，请刷新后重新确认')
            if action == 'cancel':
                if squad:
                    row = db.execute('SELECT * FROM squads WHERE id=?', (squad['id'],)).fetchone()
                    if row['status'] not in ['proposed', 'ready']:
                        raise ValueError('本局已结束')
                    if json.loads(row['feedback']):
                        raise ValueError('已有局后反馈，请完成反馈而非取消')
                    self._release(db, row, 'cancelled')
                elif ticket['status'] != 'waiting':
                    raise ValueError('本局已结束')
                db.execute("UPDATE tickets SET status='cancelled' WHERE id=?", (ticket['id'],))
            elif action == 'reject':
                if not squad or squad['status'] != 'proposed':
                    raise ValueError('候选已变更或确认超时，请刷新')
                if not feedback or feedback.get('reason') not in REASONS:
                    raise ValueError('请选择一个婉拒原因')
                row = db.execute('SELECT * FROM squads WHERE id=?', (squad['id'],)).fetchone()
                self._release(db, row, 'rejected', ticket['id'], feedback['reason'])
            else:
                if not squad:
                    raise ValueError('没有待确认队伍，可能已超时，请刷新')
                row = db.execute('SELECT * FROM squads WHERE id=?', (squad['id'],)).fetchone()
                if action == 'confirm':
                    if row['status'] not in ['proposed', 'ready']:
                        raise ValueError('本局已结束')
                    confirms = json.loads(row['confirms'])
                    if str(ticket['id']) not in confirms:
                        confirms.append(str(ticket['id']))
                        self._event(db, ticket['id'], row['id'], 'confirmed')
                    ready = len(confirms) == 2
                    db.execute('UPDATE squads SET confirms=?,status=? WHERE id=?',
                               (json.dumps(confirms), 'ready' if ready else 'proposed', row['id']))
                elif action == 'feedback':
                    if row['status'] not in ['ready', 'done']:
                        raise ValueError('双方确认后才能提交局后反馈')
                    reviews, key = json.loads(row['feedback']), str(ticket['id'])
                    if key in reviews:
                        raise ValueError('本局反馈已提交')
                    reviews[key] = {**feedback, 'submitted_at': self.now()}
                    db.execute('UPDATE squads SET feedback=?,status=? WHERE id=?',
                               (json.dumps(reviews, ensure_ascii=False), 'done' if len(reviews) == 2 else 'ready', row['id']))
                    db.execute("UPDATE tickets SET status='done' WHERE id=?", (ticket['id'],))
                else:
                    raise ValueError('未知操作')
            return self._state(db, token, auto=False)

    def recent_squads(self, token, before=None):
        with self.db() as db:
            self.participant(db, token)
            rows = db.execute("""SELECT s.*,ta.token AS owner_a,ta.profile AS profile_a,tb.profile AS profile_b
                FROM squads s JOIN tickets ta ON ta.id=s.a JOIN tickets tb ON tb.id=s.b
                WHERE (ta.token=? OR tb.token=?) AND s.status IN ('ready','done') AND (? IS NULL OR s.id<?)
                ORDER BY s.id DESC LIMIT 11""", (token,token,before,before)).fetchall()
            items=[]
            for row in rows[:10]:
                mine=json.loads(row['profile_a'] if row['owner_a']==token else row['profile_b'])
                peer=json.loads(row['profile_b'] if row['owner_a']==token else row['profile_a'])
                agreement=json.loads(row['agreement_json']) if row['agreement_json'] else mine
                items.append({'id':row['id'],'status':row['status'],'created':row['created'],
                              'peer_nickname':peer['nickname'],'goal':agreement.get('goal',''),
                              'map':agreement.get('map',''),'difficulty':agreement.get('difficulty','')})
            return {'items':items,'next_before':items[-1]['id'] if len(rows)>10 else None}

    def metrics(self):
        with self.db() as db:
            self._expire(db)
            result = {}
            all_rows = db.execute('SELECT * FROM tickets').fetchall()
            demo_ids = {t['id'] for t in all_rows if json.loads(t['profile']).get('is_demo')}
            demo_tokens = {t['token'] for t in all_rows if t['id'] in demo_ids}
            demo_squads = [s for s in db.execute('SELECT * FROM squads').fetchall() if s['a'] in demo_ids or s['b'] in demo_ids]
            demo_request_ids = demo_ids | {tid for s in demo_squads for tid in (s['a'],s['b'])}
            demo_squad_ids = {s['id'] for s in demo_squads}
            for arm in ['A', 'B', 'auto', 'manual']:
                all_tickets = db.execute('SELECT * FROM tickets').fetchall()
                tickets = [t for t in all_tickets if t['id'] not in demo_request_ids and json.loads(t['profile']).get('rules_version') == VERSION and
                    (json.loads(t['profile']).get('discovery', 'auto') == 'browse' if arm == 'manual'
                     else json.loads(t['profile']).get('discovery', 'auto') == 'auto' and ((arm == 'auto' and json.loads(t['profile']).get('matching_mode') == 'shared') or (arm != 'auto' and t['arm'] == arm and json.loads(t['profile']).get('matching_mode') == 'experiment')))]
                squads = db.execute('SELECT * FROM squads WHERE arm=? AND version=? ORDER BY id', (arm, VERSION)).fetchall()
                squads = [s for s in squads if s['id'] not in demo_squad_ids]
                first, all_reviews = {}, []
                wait_seconds = []
                for s in squads:
                    for tid in (s['a'], s['b']):
                        t = db.execute('SELECT * FROM tickets WHERE id=?', (tid,)).fetchone()
                        wait_seconds.append(max(0, (datetime.fromisoformat(s['created']) - datetime.fromisoformat(t['created'])).total_seconds()))
                    for tid, review in json.loads(s['feedback']).items():
                        all_reviews.append(review)
                        if review['played']:
                            owner = db.execute('SELECT token FROM tickets WHERE id=?', (int(tid),)).fetchone()[0]
                            first.setdefault(owner, review)
                reviews = list(first.values())
                yes = sum(r['again'] == 'yes' for r in reviews)
                events = db.execute('SELECT e.kind,e.reason,s.id AS squad_id FROM pilot_events e JOIN squads s ON s.id=e.squad_id WHERE s.arm=? AND s.version=?', (arm, VERSION)).fetchall()
                events = [e for e in events if e['squad_id'] not in demo_squad_ids]
                reasons = {key: sum(e['kind'] == 'rejected' and e['reason'] == key for e in events) for key in REASONS}
                result[arm] = {
                    'assigned': sum(p['token'] not in demo_tokens for p in db.execute('SELECT token FROM participants WHERE arm=?', (arm,)).fetchall()) if arm != 'manual' else None,
                    'requests': len(tickets), 'squads': len(squads), 'waiting': sum(t['status'] == 'waiting' for t in tickets),
                    'confirmed_squads': sum(s['status'] in ('ready', 'done') for s in squads),
                    'expired': sum(t['status'] == 'expired' for t in tickets),
                    'timed_out_squads': sum(s['status'] == 'timed_out' for s in squads),
                    'rejected_squads': sum(s['status'] == 'rejected' for s in squads),
                    'decline_reasons': reasons,
                    'mean_proposal_wait_seconds': round(sum(wait_seconds)/len(wait_seconds), 1) if wait_seconds else None,
                    'cancelled_squads': sum(s['status'] == 'cancelled' for s in squads),
                    'responses': len(all_reviews), 'unplayed_responses': sum(not r['played'] for r in all_reviews),
                    'played_first_responses': len(reviews), 'yes': yes,
                    'again_rate': round(yes/len(reviews)*100, 1) if reviews else None,
                    'goal_met': sum(r['goal_met'] for r in reviews),
                    'budget_met': sum(r['budget_met'] for r in reviews),
                    'skill_met': sum(r['skill_met'] for r in reviews)}
            return {'groups': result, 'version': VERSION,
                    'notice': '【测试】玩家及其组局不计入效果统计。自动匹配 A/B 与自主挑选单独统计。按本版本首次已游玩反馈计算，缺失不计为否；自主选择存在选择偏差，不可直接推断策略胜负。'}
