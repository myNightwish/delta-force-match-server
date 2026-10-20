from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PostCreateIn(BaseModel):
    island: str = Field(pattern="^(tech|companion)$")
    mode: str = "烽火地带"
    recruit_count: int = Field(ge=1, le=2, description="招 1 人或 2 人")
    tier_min: str | None = None
    tier_max: str | None = None
    kd_min: float | None = None
    roles_needed: list[str] | None = None
    mic_required: bool = False
    play_time: str | None = None


class PostOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    owner_id: int
    island: str
    mode: str
    recruit_count: int
    tier_min: str | None
    tier_max: str | None
    kd_min: float | None
    roles_needed: list[str] | None
    mic_required: bool
    play_time: str | None
    status: str
    member_count: int = 0
    created_at: datetime | None = None
    # 房主展示信息（卡片用，KD 仍隐藏）
    owner_nickname: str | None = None
    owner_tier_name: str | None = None
    owner_tier_stars: int | None = None
    owner_matches: int | None = None
    owner_extract_rate: float | None = None
    match_score: float | None = None


class ApplicationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    post_id: int
    applicant_id: int
    status: str
    created_at: datetime | None = None


class TeamMemberOut(BaseModel):
    user_id: int
    nickname: str
    has_confirmed: bool
    kda: str | None = None  # 入队后可见


class TeamOut(BaseModel):
    match_id: int
    status: str
    members: list[TeamMemberOut]
    all_confirmed: bool


class RoomCodeIn(BaseModel):
    room_code: str = Field(min_length=1, max_length=64)
