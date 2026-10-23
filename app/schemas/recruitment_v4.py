from typing import Literal
from pydantic import BaseModel, Field, model_validator
from app.services.recruitment_rules import MAP_MODES, TASKS

class MapChoice(BaseModel):
    map: str
    difficulty: str
    @model_validator(mode='after')
    def valid_pair(self):
        if self.map not in MAP_MODES or self.difficulty not in MAP_MODES[self.map]:
            raise ValueError('请选择该地图支持的难度')
        return self

class StrengthInputs(BaseModel):
    confidential_kd: float=Field(ge=0,le=1000,allow_inf_nan=False)
    top_secret_kd: float=Field(ge=0,le=1000,allow_inf_nan=False)
    combat_score: float=Field(ge=0,le=1000000,allow_inf_nan=False)

    def result(self):
        score=round((self.confidential_kd*.4+self.top_secret_kd*.6)*self.combat_score,2)
        return {'strength_score':score,'strength_min':round(score*.7,2),'strength_max':round(score*1.3,2)}

class RecruitmentV4(BaseModel):
    schema_version: Literal[4]
    nickname: str=Field(min_length=1,max_length=24)
    game_id: str=Field(default='',max_length=64)
    region: Literal['NA','EU','SEA','OCE','ME']
    platform: Literal['PC','Console','Mobile']
    language: Literal['中文','English','日本語']
    voice: Literal['开麦','听指挥与标记']
    slot: Literal['现在起一小时','今天晚间']
    goal: Literal['33任务','卡战备跑刀','搜打撤','猛攻']
    tasks: list[str]=Field(default_factory=list,max_length=8)
    maps: list[MapChoice]=Field(min_length=1,max_length=6)
    helmet: int|None=Field(default=None,ge=1,le=6)
    armor: int|None=Field(default=None,ge=1,le=6)
    ammo: int|None=Field(default=None,ge=1,le=6)
    style: int=Field(ge=1,le=5)
    partner_style_min: int=Field(ge=1,le=5)
    partner_style_max: int=Field(ge=1,le=5)
    partner_helmet_min: int|None=Field(default=None,ge=1,le=6)
    partner_armor_min: int|None=Field(default=None,ge=1,le=6)
    partner_ammo_min: int|None=Field(default=None,ge=1,le=6)
    score_mode: Literal['auto']='auto'
    manual_score: Literal[None]=None
    confidential_kd: float|None=Field(default=None,ge=0,le=1000,allow_inf_nan=False)
    top_secret_kd: float|None=Field(default=None,ge=0,le=1000,allow_inf_nan=False)
    combat_score: float|None=Field(default=None,ge=0,le=1000000,allow_inf_nan=False)
    strength_range_mode: Literal['auto','custom']='auto'
    strength_min: float|None=Field(default=None,ge=0,le=1000000000,allow_inf_nan=False)
    strength_max: float|None=Field(default=None,ge=0,le=1300000000,allow_inf_nan=False)
    discovery: Literal['browse','auto']='browse'
    public: bool=True

    @model_validator(mode='after')
    def valid(self):
        self.nickname=self.nickname.strip()
        self.game_id=self.game_id.strip()
        if not self.nickname: raise ValueError('请填写昵称')
        if len({m.map for m in self.maps})!=len(self.maps):raise ValueError('一张地图只能选择一个难度')
        if len(set(self.tasks))!=len(self.tasks) or any(t not in TASKS for t in self.tasks):raise ValueError('请选择有效且不重复的任务')
        if self.goal=='33任务' and not self.tasks:raise ValueError('请至少选择一个任务')
        if self.goal!='33任务':self.tasks=[]
        if self.partner_style_min>self.partner_style_max:raise ValueError('打法范围下限不能高于上限')
        for k in ('helmet','armor','ammo'):
            requirement='partner_'+k+'_min'
            if self.goal=='卡战备跑刀':
                setattr(self,k,None);setattr(self,requirement,None)
            elif getattr(self,k) is None or getattr(self,requirement) is None:
                raise ValueError('请填写自身装备与队友装备门槛')
            elif getattr(self,requirement)>getattr(self,k):raise ValueError('队友装备门槛不能高于自己的对应装备')
        if self.score_mode=='auto' and any(getattr(self,k) is None for k in ('confidential_kd','top_secret_kd','combat_score')):raise ValueError('请填写 KD 和战斗分')
        if self.strength_range_mode=='custom' and (self.strength_min is None or self.strength_max is None or self.strength_min>self.strength_max):raise ValueError('请填写有效的队友实力范围')
        return self

    def profile(self):
        p=self.model_dump()
        p.update(StrengthInputs(confidential_kd=self.confidential_kd,top_secret_kd=self.top_secret_kd,combat_score=self.combat_score).result())
        if self.strength_range_mode=='custom':
            p['strength_min']=self.strength_min
            p['strength_max']=self.strength_max
        # Compatibility fields let historical UI and reports continue reading old squads.
        p.update(map=self.maps[0].map,difficulty=self.maps[0].difficulty,task=self.tasks[0] if self.tasks else None,
                 budget_min=0,budget_max=1000,rank=4,skill=3,gap=1)
        return p
