from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models import PlayerProfile, User
from app.schemas.profile import ProfileIn, ProfileOut

router = APIRouter()


@router.get("/me")
def get_me(me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    profile = db.query(PlayerProfile).filter(PlayerProfile.user_id == me.id).first()
    return {
        "user_id": me.id,
        "phone": me.phone,
        "nickname": me.nickname,
        "faction": me.faction,
        "reputation": me.reputation,
        "profile": ProfileOut.model_validate(profile).model_dump() if profile else None,
    }


@router.put("/me/profile", response_model=ProfileOut)
def upsert_profile(data: ProfileIn, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """上传一次即为档案；赛季更新时重新调用覆盖。"""
    profile = db.query(PlayerProfile).filter(PlayerProfile.user_id == me.id).first()
    payload = data.model_dump()
    if profile is None:
        profile = PlayerProfile(user_id=me.id, verified_at=datetime.utcnow(), **payload)
        db.add(profile)
    else:
        for k, v in payload.items():
            setattr(profile, k, v)
        profile.verified_at = datetime.utcnow()
    db.commit()
    db.refresh(profile)
    return profile


@router.get("/me/profile", response_model=ProfileOut | None)
def get_profile(me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(PlayerProfile).filter(PlayerProfile.user_id == me.id).first()
