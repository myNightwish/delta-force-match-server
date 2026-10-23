"""Two-person matching: private plans, opt-in public recruitment and atomic reservations."""
import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta

from app.core.config import settings

VERSION = 'two-person-recruitment-v5'
PRESENCE_SECONDS = 75

class RecruitmentConflictError(ValueError):
    pass

class InvalidIdentityError(ValueError):
    pass

HARD_FIELDS = [('region', 'profile.region'), ('platform', 'profile.platform'), ('language', 'ui.language'),
               ('map', 'recruitment.maps'), ('difficulty', 'profile.difficulty'), ('voice', 'profile.voice'), ('slot', 'recruitment.slot')]
REASONS = {'goal': 'ui.different_goals', 'budget': 'ui.gear_budget_mismatch', 'skill': 'ui.skill_expectations_differ',
           'communication': 'ui.communication_mismatch', 'time': 'ui.schedule_changed', 'other': 'ui.other_reason'}


def assessment(a, b):
    if a.get('schema_version') == 4 or b.get('schema_version') == 4:
        if a.get('schema_version') != b.get('schema_version'):
            return {'eligible':False,'hard_conflicts':['matching.versionConflict'],'checks':[],'skill_gap':None}
        from app.services.recruitment_rules import assessment_v4
        return assessment_v4(a,b)
    hard = ['conflict.'+field for field, _ in HARD_FIELDS if a[field] != b[field]]
    gap = abs(a['skill'] - b['skill'])
    checks = [
        {'label': 'ui.same_session_goal', 'ok': a['goal'] == b['goal']},
        {'label': 'ui.compatible_gear_budgets', 'ok': max(a['budget_min'], b['budget_min']) <= min(a['budget_max'], b['budget_max'])},
        {'label': 'ui.their_skill_fits_your_range_358', 'ok': gap <= a['gap']},
        {'label': 'ui.your_skill_fits_their_range_359', 'ok': gap <= b['gap']},
    ]
    if a['goal'] in ('goal.missions','goal.budget_run','goal.aggressive') or b['goal'] in ('goal.missions','goal.budget_run','goal.aggressive'):
        checks[2:4] = [{'label':'ui.their_skill_fits_your_range_360','ok': b.get('strength_score') is not None and a.get('strength_min') is not None and a.get('strength_max') is not None and a['strength_min'] <= b['strength_score'] <= a['strength_max']},
                       {'label':'ui.your_skill_fits_their_range_361','ok': a.get('strength_score') is not None and b.get('strength_min') is not None and b.get('strength_max') is not None and b['strength_min'] <= a['strength_score'] <= b['strength_max']}]
        checks[1] = {'label': 'ui.compatible_gear_levels', 'ok': a['goal'] == b['goal'] and (a['goal'] == 'goal.budget_run' or all(a.get(k) is not None and a.get(k) == b.get(k) for k in ('armor','ammo')))}
        if a['goal'] == b['goal'] == 'goal.missions':
            checks.append({'label':'ui.same_mission_type','ok': bool(a.get('task')) and a.get('task') == b.get('task')})
        checks.extend([{'label':'ui.their_playstyle_fits_your_range_362','ok': a.get('partner_style') is not None and a.get('partner_style') == b.get('style')},
                       {'label':'ui.your_playstyle_fits_their_range_363','ok': b.get('partner_style') is not None and b.get('partner_style') == a.get('style')}])
    return {'eligible': not hard and all(c['ok'] for c in checks), 'hard_conflicts': hard,
            'checks': checks, 'skill_gap': gap, 'data_source': 'profile.selfReported'}


def compatible(a, b, arm):
    check = assessment(a, b)
    reasons = list(check['hard_conflicts'])
    if a.get('schema_version') == 4 or b.get('schema_version') == 4:
        return reasons + [c['label'] for c in check['checks'] if not c['ok']]
    if a['goal'] in ('goal.missions','goal.budget_run','goal.aggressive') or b['goal'] in ('goal.missions','goal.budget_run','goal.aggressive'):
        return reasons + [c['label'] for c in check['checks'] if not c['ok']]
    if arm != 'A':
        if a['goal'] != b['goal']:
            reasons.append('ui.different_goals')
        if not check['checks'][1]['ok']:
            reasons.append('ui.gear_budget_mismatch')
        if not all(c['ok'] for c in check['checks'][2:]):
            reasons.append('ui.skill_expectations_differ')
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
            raise InvalidIdentityError('ui.your_anonymous_session_has_expired')
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
            raise ValueError('ui.another_team_reserved_this_player_refresh_the_candidates')
        if not self.same_pool(db,a['token'],b['token']):
            raise ValueError('ui.players_in_different_test_pools_cannot_team_up')
        if a['token'] == b['token']:
            raise ValueError('ui.you_cannot_team_up_with_yourself')
        if self._blocked(db, a['token'], b['token']):
            raise ValueError('ui.you_recently_skipped_this_candidate_choose_another_player')
        if not self.online(db, a['token']) or not self.online(db, b['token']):
            raise ValueError('ui.this_player_is_offline_refresh_the_candidates')
        pa, pb = json.loads(a['profile']), json.loads(b['profile'])
        strategy = a['arm'] if source == 'auto' and self.config()['experiment_enabled'] else 'B'
        conflicts = compatible(pa, pb, strategy)
        if conflicts:
            raise ValueError('matching.cannotFormTeam')
        if source == 'auto' and self.config()['experiment_enabled'] and a['arm'] != b['arm']:
            raise ValueError('ui.auto_match_requires_the_same_experiment_group')
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
            result['notice'] = {'rejected': 'ui.the_previous_candidate_was_declined_your_queue_progress_was', 'timed_out': 'ui.the_previous_confirmation_timed_out_slots_were_released_and', 'cancelled': 'ui.the_previous_team_was_cancelled_remaining_players_keep_their'}[event['kind']]
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
                                   'goal': a['goal'] if a['goal'] == b['goal'] else 'agreement.mixedGoals',
                                   'budget_min': max(a['budget_min'], b['budget_min']),
                                   'budget_max': min(a['budget_max'], b['budget_max']),
                                   'warnings': compatible(a, b, 'B'),
                                   'scope': 'ui.this_service_finds_one_teammate_and_does_not_control'}}
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
        elif profile.get('goal') in ('goal.missions','goal.budget_run','goal.aggressive'):
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
                profile['nickname']='[TEST]'+profile['nickname'].removeprefix('[TEST]')[:18]
            self._expire(db)
            if db.execute("SELECT id FROM tickets WHERE token=? AND status IN ('waiting','matched')", (token,)).fetchone():
                raise ValueError('ui.an_active_team_already_exists_finish_or_cancel_it')
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
                raise RecruitmentConflictError('ui.your_listing_changed_review_it_and_try_again')
            if current['status']=='matched':
                raise RecruitmentConflictError('ui.a_teammate_was_found_review_your_current_team_first')
            if current['status']!='waiting':
                raise RecruitmentConflictError('ui.the_original_listing_has_ended_go_back_and_publish')
            if db.execute("SELECT 1 FROM tickets WHERE token=? AND id<>? AND status IN ('waiting','matched')",(token,current['id'])).fetchone():
                raise RecruitmentConflictError('ui.another_listing_is_active_review_your_current_listing_first')
            if db.execute('SELECT 1 FROM pilot_test_people WHERE owner=?',(token,)).fetchone():
                profile['is_demo']=True
                profile['nickname']='[TEST]'+profile['nickname'].removeprefix('[TEST]')[:18]
            db.execute("UPDATE tickets SET status='replaced' WHERE id=?",(current['id'],))
            self._event(db,current['id'],None,'replaced')
            cur=db.execute("INSERT INTO tickets(token,arm,profile,status,created) VALUES(?,?,?,'waiting',?)",(token,participant['arm'],json.dumps(profile,ensure_ascii=False),self.now()))
            self._event(db,cur.lastrowid,None,'joined',profile.get('discovery','auto'))
            return self._state(db,token)

    def candidates(self, token, recommended=True, page=0, query='', listing_language='zh'):
        with self.db() as db:
            self.participant(db, token, touch=True)
            self._expire(db)
            mine = self._latest(db, token)
            profile = json.loads(mine['profile']) if mine else None
            rows = self._available_candidates(db,token)
            result = []
            for row in rows:
                other = json.loads(row['profile'])
                pool=profile.get('listing_language','zh') if profile else listing_language
                if other.get('listing_language','zh')!=pool:continue
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
                raise ValueError('ui.publish_your_conditions_and_refresh_the_candidates')
            if not json.loads(other['profile']).get('public', False):
                raise ValueError('ui.this_player_s_listing_is_private')
            self._reserve(db, mine, other, 'manual')
            return self._state(db, token, auto=False)

    def skip(self, token, candidate_ids):
        with self.db() as db:
            self.participant(db, token, touch=True)
            self._expire(db)
            mine = self._latest(db, token)
            if not mine or mine['status'] != 'waiting':
                raise ValueError('ui.you_can_only_refresh_candidates_while_queued')
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
                raise ValueError('ui.you_have_not_published_a_listing')
            if expected_squad_id is not None and (not squad or squad['id'] != expected_squad_id):
                raise ValueError('ui.the_team_changed_refresh_before_confirming_again')
            if action == 'cancel':
                if squad:
                    row = db.execute('SELECT * FROM squads WHERE id=?', (squad['id'],)).fetchone()
                    if row['status'] not in ['proposed', 'ready']:
                        raise ValueError('ui.this_session_has_ended')
                    if json.loads(row['feedback']):
                        raise ValueError('ui.feedback_has_started_complete_it_instead_of_cancelling')
                    self._release(db, row, 'cancelled')
                elif ticket['status'] != 'waiting':
                    raise ValueError('ui.this_session_has_ended')
                db.execute("UPDATE tickets SET status='cancelled' WHERE id=?", (ticket['id'],))
            elif action == 'reject':
                if not squad or squad['status'] != 'proposed':
                    raise ValueError('ui.the_candidate_changed_or_confirmation_expired_please_refresh')
                if not feedback or feedback.get('reason') not in REASONS:
                    raise ValueError('ui.select_a_reason_for_declining')
                row = db.execute('SELECT * FROM squads WHERE id=?', (squad['id'],)).fetchone()
                self._release(db, row, 'rejected', ticket['id'], feedback['reason'])
            else:
                if not squad:
                    raise ValueError('ui.no_team_is_awaiting_confirmation_it_may_have_expired')
                row = db.execute('SELECT * FROM squads WHERE id=?', (squad['id'],)).fetchone()
                if action == 'confirm':
                    if row['status'] not in ['proposed', 'ready']:
                        raise ValueError('ui.this_session_has_ended')
                    confirms = json.loads(row['confirms'])
                    if str(ticket['id']) not in confirms:
                        confirms.append(str(ticket['id']))
                        self._event(db, ticket['id'], row['id'], 'confirmed')
                    ready = len(confirms) == 2
                    db.execute('UPDATE squads SET confirms=?,status=? WHERE id=?',
                               (json.dumps(confirms), 'ready' if ready else 'proposed', row['id']))
                elif action == 'feedback':
                    if row['status'] not in ['ready', 'done']:
                        raise ValueError('ui.both_players_must_confirm_before_submitting_feedback')
                    reviews, key = json.loads(row['feedback']), str(ticket['id'])
                    if key in reviews:
                        raise ValueError('ui.feedback_for_this_session_has_already_been_submitted')
                    reviews[key] = {**feedback, 'submitted_at': self.now()}
                    db.execute('UPDATE squads SET feedback=?,status=? WHERE id=?',
                               (json.dumps(reviews, ensure_ascii=False), 'done' if len(reviews) == 2 else 'ready', row['id']))
                    db.execute("UPDATE tickets SET status='done' WHERE id=?", (ticket['id'],))
                else:
                    raise ValueError('ui.unknown_action')
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
                    'notice': 'ui.test_players_and_teams_are_excluded_auto_match_a'}
