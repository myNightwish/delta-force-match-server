from typing import Literal
from pydantic import BaseModel, Field, model_validator

class Preferences(BaseModel):
    schema_version: Literal[3] = 3
    nickname: str=Field(min_length=1,max_length=24)
    game_id: str=Field(default='',max_length=64)
    region: Literal['NA','EU','SEA','OCE','ME']
    platform: Literal['PC','Console','Mobile']
    language: Literal['中文','English','日本語']
    map: Literal['零号大坝','长弓溪谷','巴克什']
    difficulty: Literal['简单','普通']
    voice: Literal['开麦','听指挥与标记']
    slot: Literal['现在起一小时','今天晚间']
    goal: Literal['33任务','跑刀','猛攻','任务','搜集撤离','主动交战']
    task: Literal['调查','放置','带出撤离任务','击杀干员','击杀特殊兵种','普通局内任务','标杆任务','命运契约'] | None = None
    armor: int | None = Field(default=None,ge=1,le=5)
    ammo: int | None = Field(default=None,ge=1,le=5)
    style: int | None = Field(default=None,ge=1,le=5)
    partner_style: int | None = Field(default=None,ge=1,le=5)
    confidential_kd: float | None = Field(default=None,ge=0,le=1000,allow_inf_nan=False)
    top_secret_kd: float | None = Field(default=None,ge=0,le=1000,allow_inf_nan=False)
    combat_score: float | None = Field(default=None,ge=0,le=1000000,allow_inf_nan=False)
    strength_min: float | None = Field(default=None,ge=0,le=1000000000,allow_inf_nan=False)
    strength_max: float | None = Field(default=None,ge=0,le=1000000000,allow_inf_nan=False)
    budget_min: int=Field(default=0,ge=0,le=1000)
    budget_max: int=Field(default=1000,ge=0,le=1000)
    rank: int=Field(default=4,ge=1,le=7)
    skill: int=Field(default=3,ge=1,le=5)
    gap: int=Field(default=1,ge=0,le=4)
    discovery: Literal["browse", "auto"] = "auto"
    public: bool = False
    @model_validator(mode='after')
    def valid_budget(self):
        if self.goal in ('33任务','跑刀','猛攻'):
            if any(getattr(self,k) is None for k in ('confidential_kd','top_secret_kd','combat_score','strength_min','strength_max')): raise ValueError('请填写实力数据及队友实力范围')
            if self.strength_min > self.strength_max: raise ValueError('队友实力下限不能超过上限')
            if self.style is None or self.partner_style is None: raise ValueError('请填写双方打法风格')
            if self.goal == '33任务' and self.task is None: raise ValueError('请选择任务类型')
            if self.goal != '跑刀' and (self.armor is None or self.ammo is None): raise ValueError('请选择护甲和子弹等级')
            if self.goal == '跑刀': self.armor = self.ammo = None
            if self.goal != '33任务': self.task = None
        if self.goal not in ('33任务','跑刀','猛攻') and self.budget_min>self.budget_max: raise ValueError('预算下限不能超过上限')
        self.nickname=self.nickname.strip(); self.game_id=self.game_id.strip()
        if not self.nickname: raise ValueError('昵称不能为空')
        return self

