"""Two-person matching: private plans, opt-in public recruitment and atomic reservations."""
import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta

from app.core.config import settings

VERSION = 'two-person-discovery-v2'
HARD_FIELDS = [('region', '区服'), ('platform', '平台'), ('language', '语言'),
               ('map', '地图'), ('difficulty', '难度'), ('voice', '语音方式'), ('slot', '开局时段')]
REASONS = {'goal': '目标不同', 'budget': '装备预算不合适', 'skill': '实力预期不符',
           'communication': '沟通方式不合适', 'time': '时间安排变化', 'other': '其他原因'}


def assessment(a, b):
    hard = [label + '不一致' for field, label in HARD_FIELDS if a[field] != b[field]]
    gap = abs(a['skill'] - b['skill'])
    checks = [
        {'label': '本局目标一致', 'ok': a['goal'] == b['goal']},
        {'label': '装备预算有交集', 'ok': max(a['budget_min'], b['budget_min']) <= min(a['budget_max'], b['budget_max'])},
        {'label': '对方实力在我接受范围内', 'ok': gap <= a['gap']},
        {'label': '我的实力在对方接受范围内', 'ok': gap <= b['gap']},
    ]
    return {'eligible': not hard and all(c['ok'] for c in checks), 'hard_conflicts': hard,
            'checks': checks, 'skill_gap': gap, 'data_source': '玩家自报，未核验'}


def compatible(a, b, arm):
    check = assessment(a, b)
    reasons = list(check['hard_conflicts'])
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
            columns = {r['name'] for r in db.execute('PRAGMA table_info(squads)')}
            if 'deadline' not in columns:
                db.execute('ALTER TABLE squads ADD COLUMN deadline TEXT')
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
            db.execute('INSERT INTO participants VALUES(?,?,?)', (token, arm, self.now()))
        return {'token': token}

    def participant(self, db, token):
        p = db.execute('SELECT * FROM participants WHERE token=?', (token,)).fetchone()
        if not p:
            raise ValueError('体验身份无效，请重新进入')
        return p

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
        if a['token'] == b['token']:
            raise ValueError('不能与自己组队')
        if self._blocked(db, a['token'], b['token']):
            raise ValueError('近期已跳过该候选，请选择其他玩家')
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
        db.execute("UPDATE tickets SET status='matched' WHERE id IN (?,?)", (a['id'], b['id']))
        for t in (a, b):
            self._event(db, t['id'], cur.lastrowid, 'proposed', source)

    def _auto(self, db, ticket):
        if ticket['status'] != 'waiting':
            return
        profile = json.loads(ticket['profile'])
        if profile.get('discovery', 'auto') != 'auto':
            return
        if self.config()['experiment_enabled']:
            candidates = db.execute("SELECT * FROM tickets WHERE arm=? AND status='waiting' AND token<>? ORDER BY created,id",
                                    (ticket['arm'], ticket['token'])).fetchall()
        else:
            candidates = db.execute("SELECT * FROM tickets WHERE status='waiting' AND token<>? ORDER BY created,id",
                                    (ticket['token'],)).fetchall()
        candidates = [c for c in candidates if json.loads(c['profile']).get('discovery', 'auto') == 'auto'
                      and not self._blocked(db, ticket['token'], c['token'])
                      and not compatible(profile, json.loads(c['profile']), ticket['arm'] if self.config()['experiment_enabled'] else 'B')]
        candidates.sort(key=lambda c: (abs(profile['rank'] - json.loads(c['profile'])['rank']), c['created'], c['id']))
        if candidates:
            self._reserve(db, ticket, candidates[0], 'auto')

    def _state(self, db, token, auto=True):
        self._expire(db)
        p = self.participant(db, token)
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
        return result

    def state(self, token):
        with self.db() as db:
            return self._state(db, token)

    def join(self, token, profile):
        profile = {**profile, 'rules_version': VERSION, 'matching_mode': 'experiment' if self.config()['experiment_enabled'] else 'shared'}
        with self.db() as db:
            p = self.participant(db, token)
            self._expire(db)
            if db.execute("SELECT id FROM tickets WHERE token=? AND status IN ('waiting','matched')", (token,)).fetchone():
                raise ValueError('已有进行中的组局，请先完成或取消')
            cur = db.execute("INSERT INTO tickets(token,arm,profile,status,created) VALUES(?,?,?,'waiting',?)",
                             (token, p['arm'], json.dumps(profile, ensure_ascii=False), self.now()))
            self._event(db, cur.lastrowid, None, 'joined', profile.get('discovery', 'auto'))
            return self._state(db, token)

    def candidates(self, token, recommended=True, page=0, query=""):
        with self.db() as db:
            self.participant(db, token)
            self._expire(db)
            mine = self._latest(db, token)
            profile = json.loads(mine['profile']) if mine else None
            rows = db.execute("SELECT * FROM tickets WHERE status='waiting' AND token<>? ORDER BY created,id", (token,)).fetchall()
            result = []
            for row in rows:
                other = json.loads(row['profile'])
                if not other.get('public', False) or self._blocked(db, token, row['token']):
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
                    abs(profile['rank'] - c['profile']['rank']), c['created'], c['id']))
            else:
                result.sort(key=lambda c:(bool(c['profile'].get('is_demo')), c['created'],c['id']))
            return {'items': result[page*6:page*6+6], 'total': len(result), 'page': page,
                    'can_recommend': profile is not None, 'my_status':mine['status'] if mine else None, 'server_now': self.now()}

    def invite(self, token, candidate_id):
        with self.db() as db:
            self.participant(db, token)
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
            self.participant(db, token)
            self._expire(db)
            mine = self._latest(db, token)
            if not mine or mine['status'] != 'waiting':
                raise ValueError('只有排队时可以换一批')
            for tid in set(candidate_ids):
                other = db.execute("SELECT * FROM tickets WHERE id=? AND status='waiting' AND token<>?", (tid, token)).fetchone()
                if other and json.loads(other['profile']).get('public', False):
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
