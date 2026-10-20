from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ProfileBase(BaseModel):
    game_uid: str = ""
    game_name: str = ""
    mode: str = "烽火地带"
    tier_name: str = ""
    tier_stars: int = 0
    tier_score: int = 0
    matches: int = 0
    play_hours: float = 0.0
    radar_combat: int = 0
    radar_survival: int = 0
    radar_coop: int = 0
    radar_search: int = 0
    radar_wealth: int = 0
    profit_ratio: str = ""
    extract_value: str = ""
    kda_k: float = 0.0
    kda_d: float = 0.0
    kda_a: float = 0.0
    kills: int = 0
    hit_rate: float = 0.0
    precise_kill_rate: float = 0.0
    extract_rate: float = 0.0
    teammate_extract_value: str = ""
    rescues: int = 0
    revives: int = 0
    season: str = ""


class ProfileIn(ProfileBase):
    """OCR 确认 / 手动更新档案。"""


class ProfileOut(ProfileBase):
    model_config = ConfigDict(from_attributes=True)

    user_id: int
    ocr_confidence: float | None = None
    verified_at: datetime | None = None
    updated_at: datetime | None = None


class IslandPlayerOut(BaseModel):
    """岛屿浏览卡片：未入队用户看不到 KD（隐私规则）。"""

    model_config = ConfigDict(from_attributes=True)

    user_id: int
    nickname: str
    faction: str
    tier_name: str
    tier_stars: int
    tier_score: int
    matches: int
    extract_rate: float
    kda: str | None = None  # 仅入队后返回，如 "1/1.5/0.9"
    match_score: float | None = None
