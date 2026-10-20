import secrets
from typing import Literal
from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from app.services.pilot import Pilot
from app.core.config import settings

router=APIRouter(prefix='/pilot',tags=['matching-pilot'])
service=Pilot()

class Preferences(BaseModel):
    nickname: str=Field(min_length=1,max_length=24)
    game_id: str=Field(min_length=1,max_length=64)
    region: Literal['NA','EU','SEA','OCE','ME']
    platform: Literal['PC','Console','Mobile']
    language: Literal['中文','English','日本語']
    map: Literal['零号大坝','长弓溪谷','巴克什']
    difficulty: Literal['简单','普通']
    voice: Literal['开麦','听指挥与标记']
    slot: Literal['现在起一小时','今天晚间']
    goal: Literal['任务','搜集撤离','主动交战']
    budget_min: int=Field(ge=0,le=1000)
    budget_max: int=Field(ge=0,le=1000)
    rank: int=Field(ge=1,le=7)
    skill: int=Field(ge=1,le=5)
    gap: int=Field(ge=0,le=4)
    discovery: Literal["browse", "auto"] = "auto"
    public: bool = False
    @model_validator(mode='after')
    def valid_budget(self):
        if self.budget_min>self.budget_max: raise ValueError('预算下限不能超过上限')
        self.nickname=self.nickname.strip(); self.game_id=self.game_id.strip()
        if not self.nickname or not self.game_id: raise ValueError('昵称和游戏 ID 不能为空')
        return self

class SquadActionIn(BaseModel):
    squad_id: int | None = Field(default=None, gt=0)

class Feedback(SquadActionIn):
    played: bool
    again: Literal['yes','unsure','no']
    goal_met: bool
    budget_met: bool
    skill_met: bool
    reason: str=Field(default='',max_length=300)


def token(authorization):
    if not authorization or not authorization.startswith('Bearer '): raise HTTPException(401,'需要体验身份')
    return authorization[7:]


def run(fn,*args):
    try: return fn(*args)
    except ValueError as e: raise HTTPException(400,str(e))

@router.get('/config')
def config(): return service.config()

@router.post('/participants')
def register(): return service.register()

@router.get('/state')
def state(authorization: str|None=Header(default=None)): return run(service.state,token(authorization))

@router.post('/join')
def join(data: Preferences,authorization: str|None=Header(default=None)): return run(service.join,token(authorization),data.model_dump())

@router.post('/confirm')
def confirm(data: SquadActionIn | None=None, authorization: str|None=Header(default=None)): return run(service.action,token(authorization),'confirm',None,data.squad_id if data else None)

@router.post('/cancel')
def cancel(data: SquadActionIn | None=None, authorization: str|None=Header(default=None)): return run(service.action,token(authorization),'cancel',None,data.squad_id if data else None)

@router.post('/feedback')
def feedback(data: Feedback,authorization: str|None=Header(default=None)): return run(service.action,token(authorization),'feedback',data.model_dump(exclude={'squad_id'}),data.squad_id)

@router.get('/metrics')
def metrics(authorization: str|None=Header(default=None)):
    if settings.APP_ENV == 'production':
        credential = authorization[7:] if authorization and authorization.startswith('Bearer ') else ''
        if not settings.PILOT_METRICS_TOKEN or not secrets.compare_digest(credential, settings.PILOT_METRICS_TOKEN):
            raise HTTPException(403, '无权访问内部统计')
    return service.metrics()


class CandidateIn(BaseModel):
    candidate_id: int = Field(gt=0)

class SkipIn(BaseModel):
    candidate_ids: list[int] = Field(min_length=1, max_length=6)

class RejectIn(SquadActionIn):
    reason: Literal['goal','budget','skill','communication','time','other']

@router.get('/candidates')
def candidates(recommended: bool=True, page: int=Query(default=0,ge=0,le=1000), query: str=Query(default='',max_length=64), authorization: str|None=Header(default=None)):
    return run(service.candidates,token(authorization),recommended,page,query)

@router.post('/invite')
def invite(data: CandidateIn, authorization: str|None=Header(default=None)):
    return run(service.invite,token(authorization),data.candidate_id)

@router.post('/skip')
def skip(data: SkipIn, authorization: str|None=Header(default=None)):
    return run(service.skip,token(authorization),data.candidate_ids)

@router.post('/reject')
def reject(data: RejectIn, authorization: str|None=Header(default=None)):
    return run(service.action,token(authorization),'reject',data.model_dump(exclude={'squad_id'}),data.squad_id)
