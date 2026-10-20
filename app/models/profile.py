"""玩家档案：以真实战绩截图为字段基准。

上传一次即为个人档案，后续上岛直接读取；赛季更新时可重新上传覆盖。
"""
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PlayerProfile(Base):
    __tablename__ = "player_profiles"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)

    game_uid: Mapped[str] = mapped_column(String(64), default="")
    game_name: Mapped[str] = mapped_column(String(64), default="")
    mode: Mapped[str] = mapped_column(String(32), default="烽火地带")

    tier_name: Mapped[str] = mapped_column(String(16), default="")
    tier_stars: Mapped[int] = mapped_column(Integer, default=0)
    tier_score: Mapped[int] = mapped_column(Integer, default=0)
    matches: Mapped[int] = mapped_column(Integer, default=0)
    play_hours: Mapped[float] = mapped_column(Float, default=0.0)

    # 五维雷达
    radar_combat: Mapped[int] = mapped_column(Integer, default=0)
    radar_survival: Mapped[int] = mapped_column(Integer, default=0)
    radar_coop: Mapped[int] = mapped_column(Integer, default=0)
    radar_search: Mapped[int] = mapped_column(Integer, default=0)
    radar_wealth: Mapped[int] = mapped_column(Integer, default=0)

    profit_ratio: Mapped[str] = mapped_column(String(16), default="")
    extract_value: Mapped[str] = mapped_column(String(16), default="")

    # 战损比 K/D/A，匹配用 KD 取 kda_d
    kda_k: Mapped[float] = mapped_column(Float, default=0.0)
    kda_d: Mapped[float] = mapped_column(Float, default=0.0)
    kda_a: Mapped[float] = mapped_column(Float, default=0.0)
    kills: Mapped[int] = mapped_column(Integer, default=0)
    hit_rate: Mapped[float] = mapped_column(Float, default=0.0)
    precise_kill_rate: Mapped[float] = mapped_column(Float, default=0.0)
    extract_rate: Mapped[float] = mapped_column(Float, default=0.0)
    teammate_extract_value: Mapped[str] = mapped_column(String(16), default="")
    rescues: Mapped[int] = mapped_column(Integer, default=0)
    revives: Mapped[int] = mapped_column(Integer, default=0)

    season: Mapped[str] = mapped_column(String(32), default="")
    ocr_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
