"""组队撮合：房帖 -> 申请 -> 小队(3人) -> 双确认锁定。"""
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MatchPost(Base):
    """房主发布的组队需求。island: tech=技术岛 companion=陪玩岛。"""

    __tablename__ = "match_posts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    island: Mapped[str] = mapped_column(String(16), default="tech")
    mode: Mapped[str] = mapped_column(String(32), default="烽火地带")
    recruit_count: Mapped[int] = mapped_column(Integer, default=1)  # 招 1 人或 2 人
    tier_min: Mapped[str | None] = mapped_column(String(16), nullable=True)
    tier_max: Mapped[str | None] = mapped_column(String(16), nullable=True)
    kd_min: Mapped[float | None] = mapped_column(nullable=True)
    roles_needed: Mapped[list | None] = mapped_column(JSON, nullable=True)
    mic_required: Mapped[bool] = mapped_column(Boolean, default=False)
    play_time: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open/full/locked/closed
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class MatchApplication(Base):
    __tablename__ = "match_applications"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("match_posts.id"), index=True)
    applicant_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending/accepted/rejected/cancelled
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class TeamMatch(Base):
    """一支小队：房主 + 被接受的申请者，最多 3 人。"""

    __tablename__ = "team_matches"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("match_posts.id"), unique=True)
    status: Mapped[str] = mapped_column(String(16), default="forming")  # forming/locked/done/disbanded
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class TeamMember(Base):
    __tablename__ = "team_members"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("team_matches.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    payment_id: Mapped[int | None] = mapped_column(ForeignKey("payments.id"), nullable=True)
    has_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    joined_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
