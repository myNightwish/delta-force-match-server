import secrets
from typing import Literal
from fastapi import Request, APIRouter, Header, HTTPException, Query, UploadFile, File, Form, Response
from pydantic import BaseModel, Field, model_validator
from app.services.pilot import Pilot, InvalidIdentityError
from app.services.pilot_chat import PilotChat, MAX_AUDIO_BYTES
from app.core.config import settings
from app.services.phone_auth import PhoneAuth, PhoneError
from app.services.pilot_limits import PilotLimits, RateLimitError

router=APIRouter(prefix='/pilot',tags=['matching-pilot'])
service=Pilot()

from app.schemas.pilot_preferences import Preferences
from app.schemas.recruitment_v4 import RecruitmentV4

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
    try:return PhoneAuth(service).resolve(authorization[7:])
    except PhoneError as e:raise HTTPException(e.status,str(e))


def run(fn,*args):
    try: return fn(*args)
    except RateLimitError as e: raise HTTPException(429, {'code':'rate_limited','message':str(e)}, headers={'Retry-After':str(e.retry_after)})
    except InvalidIdentityError as e: raise HTTPException(401, {'code':'anonymous_identity_invalid','message':str(e)})
    except PhoneError as e: raise HTTPException(e.status,str(e))
    except PermissionError as e: raise HTTPException(403,str(e))
    except LookupError as e: raise HTTPException(404,str(e))
    except ValueError as e: raise HTTPException(400,str(e))

def guard(request, action, owner=None):
    # Use the ASGI client address; never trust arbitrary forwarded headers here
    run(PilotLimits(service).check,action,request.client.host if request.client else 'unknown',owner)

@router.get('/config')
def config(): return service.config()

@router.post('/participants')
def register(request:Request):
    guard(request,'register')
    return service.register()

@router.get('/state')
def state(request:Request,authorization: str|None=Header(default=None)):
    owner=token(authorization);guard(request,'read',owner)
    return run(service.state,owner)

@router.post('/join')
def join(data: RecruitmentV4 | Preferences,request:Request,authorization: str|None=Header(default=None)):
    owner=token(authorization);guard(request,'join',owner)
    return run(service.join,owner,data.model_dump())

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
def candidates(request:Request,recommended: bool=True, page: int=Query(default=0,ge=0,le=1000), query: str=Query(default='',max_length=64), authorization: str|None=Header(default=None)):
    owner=token(authorization);guard(request,'read',owner)
    return run(service.candidates,owner,recommended,page,query)

@router.post('/invite')
def invite(data: CandidateIn, request:Request, authorization: str|None=Header(default=None)):
    owner=token(authorization);guard(request,'invite',owner)
    return run(service.invite,owner,data.candidate_id)

@router.post('/skip')
def skip(data: SkipIn, request:Request, authorization: str|None=Header(default=None)):
    owner=token(authorization);guard(request,'skip',owner)
    return run(service.skip,owner,data.candidate_ids)

@router.post('/reject')
def reject(data: RejectIn, request:Request, authorization: str|None=Header(default=None)):
    owner=token(authorization);guard(request,'reject',owner)
    return run(service.action,owner,'reject',data.model_dump(exclude={'squad_id'}),data.squad_id)


@router.get('/squads/recent')
def recent_squads(before:int|None=Query(default=None,gt=0), authorization:str|None=Header(default=None)):
    return run(service.recent_squads,token(authorization),before)


class TextMessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=2000)

@router.get('/squads/{squad_id}/messages')
def messages(squad_id: int, request:Request, after: int=Query(default=0,ge=0), authorization: str|None=Header(default=None)):
    owner=token(authorization);guard(request,'read',owner)
    return run(PilotChat(service).messages,owner,squad_id,after)

@router.post('/squads/{squad_id}/messages')
def send_message(squad_id: int, data: TextMessageIn, authorization: str|None=Header(default=None)):
    return run(PilotChat(service).send,token(authorization),squad_id,data.content)

@router.post('/squads/{squad_id}/voice')
async def send_voice(squad_id: int, file: UploadFile=File(...), duration: float=Form(...), authorization: str|None=Header(default=None)):
    bearer=token(authorization)
    # Check room membership before reading or storing media.
    run(PilotChat(service).messages,bearer,squad_id,0)
    try:
        audio=await file.read(MAX_AUDIO_BYTES+1)
        return run(PilotChat(service).send,bearer,squad_id,'',audio,file.content_type,duration)
    finally:
        await file.close()

@router.get('/squads/{squad_id}/voice/{message_id}')
def get_voice(squad_id: int, message_id: int, authorization: str|None=Header(default=None)):
    data,mime=run(PilotChat(service).audio,token(authorization),squad_id,message_id)
    return Response(content=data,media_type=mime,headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})


class PhoneStartIn(BaseModel):
    phone: str=Field(min_length=7,max_length=32)
class PhoneVerifyIn(PhoneStartIn):
    challenge_id: str=Field(min_length=16,max_length=100)
    code: str=Field(min_length=4,max_length=10)
class PhoneProfileIn(BaseModel):
    profile: dict

def raw_credential(authorization):
    if not authorization or not authorization.startswith('Bearer '):raise HTTPException(401,'请先进入页面')
    return authorization[7:]

@router.get('/auth/phone/config')
def phone_config():return {'available':PhoneAuth.available()}

@router.post('/auth/phone/request')
def phone_start(data:PhoneStartIn, request:Request, authorization:str|None=Header(default=None)):
    return run(PhoneAuth(service).start,raw_credential(authorization),data.phone,request.client.host if request.client else 'unknown')

@router.post('/auth/phone/verify')
def phone_verify(data:PhoneVerifyIn, authorization:str|None=Header(default=None)):
    return run(PhoneAuth(service).verify,raw_credential(authorization),data.challenge_id,data.phone,data.code)

@router.get('/auth/me')
def phone_me(authorization:str|None=Header(default=None)):
    return run(PhoneAuth(service).me,raw_credential(authorization))

@router.post('/auth/profile')
def phone_profile(data:PhoneProfileIn, authorization:str|None=Header(default=None)):
    return run(PhoneAuth(service).save_profile,raw_credential(authorization),data.profile)

@router.post('/auth/logout')
def phone_logout(authorization:str|None=Header(default=None)):
    return run(PhoneAuth(service).logout,raw_credential(authorization))


# Test tooling is disabled unless a strong server-side access code is configured
from app.services.testing_tools import TestingTools
class TestUnlockIn(BaseModel):
    code:str=Field(min_length=1,max_length=128)
class TestAccessIn(BaseModel):
    access_token:str=Field(min_length=20,max_length=128)
class TestImportIn(TestAccessIn):
    profile:dict
    new_identity:bool=False

@router.post('/testing/unlock')
def testing_unlock(data:TestUnlockIn,request:Request,authorization:str|None=Header(default=None)):
    owner=token(authorization);guard(request,'test_unlock',owner)
    return run(TestingTools(service).unlock,owner,data.code)

@router.post('/testing/scenarios')
def testing_scenarios(data:TestAccessIn,authorization:str|None=Header(default=None)):
    return run(TestingTools(service).scenarios,token(authorization),data.access_token)

@router.post('/testing/import')
def testing_import(data:TestImportIn,request:Request,authorization:str|None=Header(default=None)):
    owner=token(authorization);guard(request,'test_import',owner)
    return run(TestingTools(service).import_profile,owner,data.access_token,data.profile,data.new_identity)

@router.post('/testing/lock')
def testing_lock(data:TestAccessIn,authorization:str|None=Header(default=None)):
    return run(TestingTools(service).lock,token(authorization),data.access_token)


from app.services.simulation import Simulation
class SimulationStartIn(TestAccessIn):
    profile:dict|None=None
    behavior:Literal['accept','reject','timeout']='accept'

@router.post('/testing/simulation/start')
def simulation_start(data:SimulationStartIn,request:Request,authorization:str|None=Header(default=None)):
    owner=token(authorization);guard(request,'test_simulation',owner)
    return run(Simulation(service).start,owner,data.access_token,data.profile,data.behavior)

@router.post('/testing/simulation/current')
def simulation_current(data:TestAccessIn,authorization:str|None=Header(default=None)):
    return run(Simulation(service).current,token(authorization),data.access_token)

@router.post('/testing/simulation/stop')
def simulation_stop(data:TestAccessIn,authorization:str|None=Header(default=None)):
    return run(Simulation(service).stop,token(authorization),data.access_token)

# Growth features: public links expose only recruitment fields; operations endpoints fail closed.
from app.services.growth import Growth, ActivityIn

def operator(authorization):
    credential=authorization[7:] if authorization and authorization.startswith('Bearer ') else ''
    if len(settings.PILOT_METRICS_TOKEN)<32 or not secrets.compare_digest(credential,settings.PILOT_METRICS_TOKEN):
        raise HTTPException(403,'无权访问运营管理')

class VisitIn(BaseModel):
    source:Literal['direct','share']='direct'

@router.post('/growth/visit')
def growth_visit(data:VisitIn,request:Request,authorization:str|None=Header(default=None)):
    owner=token(authorization);guard(request,'growth',owner)
    return run(Growth(service).visit,owner,data.source)

@router.post('/shares')
def create_share(request:Request,authorization:str|None=Header(default=None)):
    owner=token(authorization);guard(request,'growth',owner)
    return run(Growth(service).create_share,owner)

@router.get('/shares/{code}')
def shared_recruitment(code:str,request:Request,authorization:str|None=Header(default=None)):
    guard(request,'public_read')
    return run(Growth(service).shared,code,token(authorization) if authorization else None)

@router.post('/shares/{code}/invite')
def shared_invite(code:str,request:Request,authorization:str|None=Header(default=None)):
    owner=token(authorization);guard(request,'invite',owner)
    return run(Growth(service).invite_share,owner,code)

@router.get('/activities')
def list_activities(request:Request,authorization:str|None=Header(default=None)):
    owner=token(authorization);guard(request,'read',owner)
    return run(Growth(service).activities,owner)

@router.post('/activities/{activity_id}/reserve')
def reserve_activity(activity_id:int,request:Request,authorization:str|None=Header(default=None)):
    owner=token(authorization);guard(request,'growth',owner)
    return run(Growth(service).reserve_activity,owner,activity_id)

@router.post('/activities/{activity_id}/cancel')
def cancel_activity(activity_id:int,request:Request,authorization:str|None=Header(default=None)):
    owner=token(authorization);guard(request,'growth',owner)
    return run(Growth(service).reserve_activity,owner,activity_id,True)

@router.get('/operations/metrics')
def operations_metrics(request:Request,authorization:str|None=Header(default=None)):
    guard(request,'operator');operator(authorization)
    return run(Growth(service).metrics)

@router.post('/operations/activities')
def operations_activity(data:ActivityIn,request:Request,authorization:str|None=Header(default=None)):
    guard(request,'operator');operator(authorization)
    return run(Growth(service).create_activity,data)

@router.post('/operations/activities/{activity_id}/close')
def close_activity(activity_id:int,request:Request,authorization:str|None=Header(default=None)):
    guard(request,'operator');operator(authorization)
    return run(Growth(service).close_activity,activity_id)
