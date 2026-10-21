"""Persistent per-address and per-identity limits for anonymous discovery actions."""
import hashlib
import math
import time

# Generous shared-address caps; tighter identity caps limit individual repeated actions
POLICIES = {
    'register': {'ip':[(20,60),(120,3600)]},
    'join': {'ip':[(30,60),(300,3600)],'owner':[(5,60),(30,3600)]},
    'invite': {'ip':[(60,60)],'owner':[(10,60)]},
    'skip': {'ip':[(90,60)],'owner':[(20,60)]},
    'reject': {'ip':[(60,60)],'owner':[(10,60)]},
}

class RateLimitError(ValueError):
    def __init__(self,retry_after):
        self.retry_after=retry_after
        super().__init__(f'操作过于频繁，请在 {retry_after} 秒后重试')

class PilotLimits:
    def __init__(self,pilot):self.pilot=pilot
    def check(self,action,ip,owner=None):
        now=time.time();rules=POLICIES[action];entries=[]
        with self.pilot.db() as db:
            if owner:self.pilot.participant(db,owner)
            db.execute('CREATE TABLE IF NOT EXISTS pilot_rate_events(scope TEXT NOT NULL,subject TEXT NOT NULL,created REAL NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS pilot_rate_subject ON pilot_rate_events(scope,subject,created)')
            db.execute('CREATE INDEX IF NOT EXISTS pilot_rate_expiry ON pilot_rate_events(created)')
            db.execute('DELETE FROM pilot_rate_events WHERE created<=?',(now-3600,))
            retry=0
            for kind,value in [('ip',ip),('owner',owner)]:
                if not value or kind not in rules:continue
                scope=action+':'+kind;key=hashlib.sha256(value.encode()).hexdigest()
                entries.append((scope,key,now))
                for maximum,window in rules[kind]:
                    rows=db.execute('SELECT created FROM pilot_rate_events WHERE scope=? AND subject=? AND created>? ORDER BY created',(scope,key,now-window)).fetchall()
                    if len(rows)>=maximum:
                        retry=max(retry,max(1,math.ceil(rows[len(rows)-maximum][0]+window-now)))
            if retry:raise RateLimitError(retry)
            db.executemany('INSERT INTO pilot_rate_events VALUES(?,?,?)',entries)
