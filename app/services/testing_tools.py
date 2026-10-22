"""Password-gated test personas; never bypass the real recruitment/matching rules."""
import hashlib
import json
import secrets
import time
from pathlib import Path
from pydantic import ValidationError
from app.core.config import settings
from app.schemas.recruitment_v4 import RecruitmentV4
from app.services.phone_auth import PhoneError

class TestingTools:
    def __init__(self,pilot):self.pilot=pilot
    @staticmethod
    def schema(db):
        db.execute('CREATE TABLE IF NOT EXISTS pilot_test_access(digest TEXT PRIMARY KEY,owner TEXT NOT NULL,expires REAL NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS pilot_test_people(owner TEXT PRIMARY KEY)')
    @staticmethod
    def digest(value):return hashlib.sha256(value.encode()).hexdigest()
    def enabled(self):
        if len(settings.PILOT_TEST_ACCESS_CODE)<12:raise PhoneError(503,'测试工具尚未开放')
    def grant(self,db,owner):
        access=secrets.token_urlsafe(32)
        db.execute('INSERT INTO pilot_test_access VALUES(?,?,?)',(self.digest(access),owner,time.time()+1800))
        return access
    def unlock(self,owner,code):
        self.enabled()
        if not secrets.compare_digest(code.encode(),settings.PILOT_TEST_ACCESS_CODE.encode()):raise PhoneError(403,'口令不正确')
        with self.pilot.db() as db:
            self.pilot.participant(db,owner);self.schema(db)
            db.execute('DELETE FROM pilot_test_access WHERE expires<=?',(time.time(),))
            return {'access_token':self.grant(db,owner),'expires_in':1800}
    def authorize(self,db,owner,access):
        self.enabled();self.pilot.participant(db,owner);self.schema(db)
        if not db.execute('SELECT 1 FROM pilot_test_access WHERE digest=? AND owner=? AND expires>?',(self.digest(access),owner,time.time())).fetchone():raise PhoneError(403,'测试授权已过期，请重新输入口令')
    def scenarios(self,owner,access):
        with self.pilot.db() as db:self.authorize(db,owner,access)
        return {'scenarios':json.loads((Path(__file__).resolve().parents[1]/'testing/scenarios.json').read_text())}
    def import_profile(self,owner,access,profile,new_identity=False):
        # Validate before creating an identity or changing test flags
        if len(json.dumps(profile,ensure_ascii=False).encode())>16384:raise ValueError('JSON 不能超过 16 KB')
        try:clean=RecruitmentV4.model_validate(profile).model_dump()
        except ValidationError as e:raise ValueError('招募 JSON 有误：'+str(e.errors()[0]['msg']))
        clean['nickname']='【测试】'+clean['nickname'].removeprefix('【测试】')[:20]
        clean.pop('game_id',None)
        with self.pilot.db() as db:
            self.authorize(db,owner,access)
            self.pilot._expire(db)
            if not new_identity and db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='phone_accounts'").fetchone() and db.execute('SELECT 1 FROM phone_accounts WHERE owner=?',(owner,)).fetchone():raise PhoneError(409,'请新建测试玩家，避免将手机号账号标记为测试')
            active=db.execute("SELECT 1 FROM tickets WHERE token=? AND status IN ('waiting','matched')",(owner,)).fetchone()
            is_test=db.execute('SELECT 1 FROM pilot_test_people WHERE owner=?',(owner,)).fetchone()
            if active and (not new_identity or not is_test):raise PhoneError(409,'请先结束当前招募，或在另一个窗口导入')
        if new_identity:
            # Creation is allowed only through this authenticated, rate-limited import action
            owner=self.pilot.register()['token']
        with self.pilot.db() as db:
            self.authorize(db,owner,access) if not new_identity else self.schema(db)
            db.execute('INSERT OR IGNORE INTO pilot_test_people(owner) VALUES(?)',(owner,))
            new_access=self.grant(db,owner)
        return {'token':owner if new_identity else None,'access_token':new_access,'profile':clean,'is_demo':True}
    def lock(self,owner,access):
        with self.pilot.db() as db:
            self.schema(db)
            db.execute('DELETE FROM pilot_test_access WHERE owner=?',(owner,))
        return {'ok':True}
