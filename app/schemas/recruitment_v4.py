from app.schemas.time_slots import TimeSlot
from app.services.strength_range import tolerance_bounds
from typing import Literal
from pydantic import BaseModel, Field, model_validator
from app.services.recruitment_rules import MAP_MODES, TASKS

class MapChoice(BaseModel):
    map: str
    difficulty: str
    @model_validator(mode='after')
    def valid_pair(self):
        if self.map not in MAP_MODES or self.difficulty not in MAP_MODES[self.map]:
            raise ValueError('ui.select_a_difficulty_supported_by_this_map')
        return self

class StrengthInputs(BaseModel):
    confidential_kd: float=Field(ge=0,le=1000,allow_inf_nan=False)
    top_secret_kd: float=Field(ge=0,le=1000,allow_inf_nan=False)
    combat_score: float=Field(ge=0,le=1000000,allow_inf_nan=False)

    def result(self):
        score=round((self.confidential_kd*.4+self.top_secret_kd*.6)*self.combat_score,2)
        lo,hi=tolerance_bounds(score)
        return {'strength_score':score,'strength_min':lo,'strength_max':hi}

class RecruitmentV4(BaseModel):
    schema_version: Literal[4]
    listing_language: Literal['zh','en']
    nickname: str=Field(min_length=1,max_length=24)
    game_id: str=Field(default='',max_length=64)
    region: Literal['NA','EU','SEA','OCE','ME']
    platform: Literal['PC']
    voice: Literal['voice.mic','voice.listen']
    slot: TimeSlot
    goal: Literal['goal.missions','goal.budget_run','goal.loot_extract','goal.aggressive']
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
    strength_range_mode: Literal['auto','custom','percent']='auto'
    strength_tolerance_percent: int=Field(default=30,ge=0,le=100,strict=True)
    strength_min: float|None=Field(default=None,ge=0,le=1000000000,allow_inf_nan=False)
    strength_max: float|None=Field(default=None,ge=0,le=2000000000,allow_inf_nan=False)
    discovery: Literal['browse','auto']='browse'
    public: bool=True

    @model_validator(mode='after')
    def valid(self):
        self.nickname=self.nickname.strip()
        self.game_id=self.game_id.strip()
        if not self.nickname: raise ValueError('ui.enter_your_nickname_689')
        if len({m.map for m in self.maps})!=len(self.maps):raise ValueError('ui.choose_only_one_difficulty_per_map')
        if len(set(self.tasks))!=len(self.tasks) or any(t not in TASKS for t in self.tasks):raise ValueError('ui.select_valid_non_duplicate_missions')
        if self.goal=='goal.missions' and not self.tasks:raise ValueError('ui.select_at_least_one_mission_692')
        if self.goal!='goal.missions':self.tasks=[]
        if self.partner_style_min>self.partner_style_max:raise ValueError('ui.minimum_playstyle_cannot_exceed_maximum')
        for k in ('helmet','armor','ammo'):
            requirement='partner_'+k+'_min'
            if self.goal=='goal.budget_run':
                setattr(self,k,None);setattr(self,requirement,None)
            elif getattr(self,k) is None or getattr(self,requirement) is None:
                raise ValueError('ui.enter_your_gear_and_minimum_teammate_gear')
            elif getattr(self,requirement)>getattr(self,k):raise ValueError('ui.minimum_teammate_gear_cannot_exceed_your_own_corresponding_gear')
        if self.score_mode=='auto' and any(getattr(self,k) is None for k in ('confidential_kd','top_secret_kd','combat_score')):raise ValueError('ui.enter_your_k_d_ratios_and_combat_score')
        if self.strength_range_mode=='custom' and (self.strength_min is None or self.strength_max is None or self.strength_min>self.strength_max):raise ValueError('ui.enter_a_valid_teammate_skill_range')
        return self

    def profile(self):
        p=self.model_dump()
        p['language']='language.'+self.listing_language
        p.update(StrengthInputs(confidential_kd=self.confidential_kd,top_secret_kd=self.top_secret_kd,combat_score=self.combat_score).result())
        if self.strength_range_mode=='percent':
            p['strength_min'],p['strength_max']=tolerance_bounds(p['strength_score'],self.strength_tolerance_percent)
        elif self.strength_range_mode=='custom':
            p['strength_min']=self.strength_min
            p['strength_max']=self.strength_max
        # Compatibility fields let historical UI and reports continue reading old squads.
        p.update(map=self.maps[0].map,difficulty=self.maps[0].difficulty,task=self.tasks[0] if self.tasks else None,
                 budget_min=0,budget_max=1000,rank=4,skill=3,gap=1)
        return p
