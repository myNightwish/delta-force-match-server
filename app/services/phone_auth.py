"""Optional Verify-backed login; anonymous matching continues independently."""
from app.schemas.time_slots import TIME_SLOT_OPTIONS
import hashlib
import hmac
import json
import math
import re
import secrets
import time
import httpx
from app.core.config import settings

class PhoneError(ValueError):
    def __init__(self,status,message):self.status=status;super().__init__(message)

class TwilioVerify:
    def request(self,phone):return self._post('Verifications',{'To':phone,'Channel':'sms'})
    def check(self,phone,code):return self._post('VerificationCheck',{'To':phone,'Code':code}).get('status')=='approved'
    def _post(self,path,data):
        url=f'https://verify.twilio.com/v2/Services/{settings.TWILIO_VERIFY_SERVICE_SID}/{path}'
        try:
            response=httpx.post(url,data=data,auth=(settings.TWILIO_ACCOUNT_SID,settings.TWILIO_AUTH_TOKEN),timeout=10)
        except httpx.HTTPError:raise PhoneError(503,'短信服务暂时不可用，请稍后重试')
        if response.status_code==429:raise PhoneError(429,'操作过于频繁，请稍后重试')
        if path=='VerificationCheck' and response.status_code in (400,404):return {'status':'rejected'}
        if response.status_code>=400:raise PhoneError(503,'短信发送失败，请稍后重试')
        return response.json()

class PhoneAuth:
    def __init__(self,pilot,provider=None):self.pilot=pilot;self.provider=provider or TwilioVerify()
    @staticmethod
    def available():
        return bool(settings.PHONE_AUTH_ENABLED and settings.PHONE_AUTH_PROVIDER=='twilio_verify' and len(settings.PHONE_AUTH_SECRET)>=32 and re.fullmatch(r'AC[0-9a-fA-F]{32}',settings.TWILIO_ACCOUNT_SID) and re.fullmatch(r'VA[0-9a-fA-F]{32}',settings.TWILIO_VERIFY_SERVICE_SID) and settings.TWILIO_AUTH_TOKEN)
    @staticmethod
    def schema(db):
        # Individual DDL statements preserve Pilot.db's transaction boundaries.
        for sql in [
         'CREATE TABLE IF NOT EXISTS phone_accounts(phone_key TEXT PRIMARY KEY,owner TEXT UNIQUE NOT NULL,masked TEXT NOT NULL,profile TEXT NOT NULL DEFAULT "{}")',
         'CREATE TABLE IF NOT EXISTS phone_sessions(digest TEXT PRIMARY KEY,owner TEXT NOT NULL,expires REAL NOT NULL)',
         'CREATE TABLE IF NOT EXISTS phone_challenges(id TEXT PRIMARY KEY,phone_key TEXT NOT NULL,subject TEXT NOT NULL,owner TEXT NOT NULL,ip_key TEXT NOT NULL,created REAL NOT NULL,expires REAL NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,status TEXT NOT NULL)',
         'CREATE INDEX IF NOT EXISTS phone_challenge_rate ON phone_challenges(phone_key,created)',
         'CREATE INDEX IF NOT EXISTS phone_challenge_actor ON phone_challenges(subject,created)',
         'CREATE INDEX IF NOT EXISTS phone_challenge_ip ON phone_challenges(ip_key,created)',
        ]:db.execute(sql)
    @staticmethod
    def digest(value):return hashlib.sha256(value.encode()).hexdigest()
    @staticmethod
    def private_key(value):return hmac.new(settings.PHONE_AUTH_SECRET.encode(),value.encode(),hashlib.sha256).hexdigest()
    @staticmethod
    def number(phone):
        value=re.sub(r'[\s()\-]','',phone)
        if not re.fullmatch(r'\+[1-9][0-9]{6,14}',value):raise PhoneError(400,'请输入包含国际区号的手机号')
        return value
    def require_enabled(self):
        if not self.available():raise PhoneError(503,'手机号登录暂未开放，可继续匿名使用')
    def resolve(self,credential):
        if not credential.startswith('ph_'):return credential
        with self.pilot.db() as db:
            self.schema(db)
            row=db.execute('SELECT owner FROM phone_sessions WHERE digest=? AND expires>?',(self.digest(credential),time.time())).fetchone()
            if not row:raise PhoneError(401,'登录已过期，请重新登录')
            return row['owner']
    def require_session(self,credential):
        if not credential.startswith('ph_'):raise PhoneError(401,'请先登录手机号')
        return self.resolve(credential)
    def me(self,credential):
        if not credential.startswith('ph_'):return {'authenticated':False,'available':self.available()}
        owner=self.require_session(credential)
        with self.pilot.db() as db:
            self.schema(db);row=db.execute('SELECT masked,profile FROM phone_accounts WHERE owner=?',(owner,)).fetchone()
            if not row:raise PhoneError(401,'登录已失效')
            return {'authenticated':True,'available':self.available(),'masked_phone':row['masked'],'profile':json.loads(row['profile'])}
    def start(self,credential,phone,ip):
        self.require_enabled();phone=self.number(phone);owner=self.resolve(credential)
        now=time.time();key=self.private_key(phone);subject=self.digest(credential);ip_key=self.private_key(ip)
        cid=secrets.token_urlsafe(24)
        with self.pilot.db() as db:
            self.schema(db);self.pilot.participant(db,owner)
            account=db.execute('SELECT phone_key FROM phone_accounts WHERE owner=?',(owner,)).fetchone()
            if account and account['phone_key']!=key:raise PhoneError(409,'请先退出当前手机号，再登录其他账号')
            db.execute('DELETE FROM phone_challenges WHERE created<?',(now-86400,))
            db.execute('DELETE FROM phone_sessions WHERE expires<=?',(now,))
            recent=db.execute('SELECT created FROM phone_challenges WHERE (phone_key=? OR subject=?) AND created>? LIMIT 1',(key,subject,now-60)).fetchone()
            if recent:raise PhoneError(429,'请在 60 秒后重新获取验证码')
            for field,value,maximum in [('phone_key',key,5),('subject',subject,5),('ip_key',ip_key,20)]:
                count=db.execute(f'SELECT count(*) FROM phone_challenges WHERE {field}=? AND created>?',(value,now-3600)).fetchone()[0]
                if count>=maximum:raise PhoneError(429,'操作过于频繁，请稍后再试')
            db.execute('INSERT INTO phone_challenges(id,phone_key,subject,owner,ip_key,created,expires,status) VALUES(?,?,?,?,?,?,?,?)',(cid,key,subject,owner,ip_key,now,now+600,'sending'))
        try:self.provider.request(phone)
        except Exception:
            with self.pilot.db() as db:db.execute("UPDATE phone_challenges SET status='failed' WHERE id=?",(cid,))
            raise
        with self.pilot.db() as db:db.execute("UPDATE phone_challenges SET status='pending' WHERE id=?",(cid,))
        return {'challenge_id':cid,'retry_after':60,'expires_in':600}
    def verify(self,credential,challenge_id,phone,code):
        self.require_enabled();phone=self.number(phone)
        if not re.fullmatch(r'[0-9]{4,10}',code):raise PhoneError(400,'请输入有效验证码')
        now=time.time();subject=self.digest(credential);owner=self.resolve(credential);key=self.private_key(phone)
        with self.pilot.db() as db:
            self.schema(db)
            row=db.execute('SELECT * FROM phone_challenges WHERE id=?',(challenge_id,)).fetchone()
            if not row or row['phone_key']!=key or row['subject']!=subject or row['owner']!=owner or row['status']!='pending' or row['expires']<=now:raise PhoneError(400,'验证码已失效，请重新获取')
            if row['attempts']>=5:raise PhoneError(429,'验证次数过多，请重新获取验证码')
            db.execute('UPDATE phone_challenges SET attempts=attempts+1 WHERE id=?',(challenge_id,))
        if not self.provider.check(phone,code):raise PhoneError(400,'验证码不正确或已失效')
        session='ph_'+secrets.token_urlsafe(32)
        with self.pilot.db() as db:
            row=db.execute('SELECT status,expires FROM phone_challenges WHERE id=?',(challenge_id,)).fetchone()
            if row['status']!='pending' or row['expires']<=time.time():raise PhoneError(400,'验证码已失效，请重新获取')
            account=db.execute('SELECT owner FROM phone_accounts WHERE phone_key=?',(key,)).fetchone()
            target=account['owner'] if account else owner
            if target!=owner and db.execute("SELECT 1 FROM tickets WHERE token=? AND status IN ('waiting','matched')",(owner,)).fetchone():raise PhoneError(409,'请先结束当前组局，再切换账号')
            bound=db.execute('SELECT phone_key FROM phone_accounts WHERE owner=?',(owner,)).fetchone()
            if bound and bound['phone_key']!=key:raise PhoneError(409,'请先退出当前账号')
            if not account:db.execute('INSERT INTO phone_accounts(phone_key,owner,masked) VALUES(?,?,?)',(key,owner,phone[:3]+'••••'+phone[-4:]))
            db.execute("UPDATE phone_challenges SET status='used' WHERE id=?",(challenge_id,))
            db.execute('INSERT INTO phone_sessions VALUES(?,?,?)',(self.digest(session),target,time.time()+30*86400))
        return {'token':session,**self.me(session)}
    def save_profile(self,credential,profile):
        owner=self.require_session(credential);clean=clean_profile(profile)
        with self.pilot.db() as db:
            self.schema(db);db.execute('UPDATE phone_accounts SET profile=? WHERE owner=?',(json.dumps(clean,ensure_ascii=False),owner))
        return {'saved':True,'profile':clean}
    def logout(self,credential):
        self.require_session(credential)
        with self.pilot.db() as db:db.execute('DELETE FROM phone_sessions WHERE digest=?',(self.digest(credential),))
        return self.pilot.register()

# Only personal defaults are synced. Recruiting goals and teammate requirements stay local.
PROFILE_CHOICES={'region':['NA','EU','SEA','OCE','ME'],'platform':['PC','Console','Mobile'],'language':['中文','English','日本語'],'voice':['开麦','听指挥与标记'],'slot':TIME_SLOT_OPTIONS,'score_mode':['auto','manual']}
PROFILE_BOUNDS={'helmet':(1,6),'armor':(1,6),'ammo':(1,6),'style':(1,5),'confidential_kd':(0,1000),'top_secret_kd':(0,1000),'combat_score':(0,1000000),'manual_score':(0,1000000000)}
def clean_profile(profile):
    if not isinstance(profile,dict) or len(json.dumps(profile,ensure_ascii=False).encode())>8192:raise PhoneError(400,'个人资料格式不正确')
    clean={}
    for k,v in profile.items():
        if k=='nickname':
            if not isinstance(v,str) or len(v)>24:raise PhoneError(400,'昵称不能超过 24 个字')
            clean[k]=v.strip()
        elif k in PROFILE_CHOICES:
            if v not in PROFILE_CHOICES[k]:raise PhoneError(400,'个人资料选项无效')
            clean[k]=v
        elif k in PROFILE_BOUNDS:
            lo,hi=PROFILE_BOUNDS[k]
            if v is None or v=='':clean[k]=None;continue
            if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not lo<=v<=hi or (k in ('helmet','armor','ammo','style') and not isinstance(v,int)):raise PhoneError(400,'个人资料数值无效')
            clean[k]=v
    return clean
