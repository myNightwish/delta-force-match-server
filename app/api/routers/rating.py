from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models import Rating, TeamMatch, TeamMember, User
from app.schemas.chat import RateIn

router = APIRouter()


@router.post("/matches/{match_id}/rate")
def rate_teammate(
    match_id: int, data: RateIn, me: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """赛后互评，影响信誉分。"""
    match = db.get(TeamMatch, match_id)
    if not match or match.status not in ("locked", "done"):
        raise HTTPException(status_code=400, detail="队伍未锁定，不能评价")
    member_ids = {
        m.user_id for m in db.query(TeamMember).filter(TeamMember.match_id == match_id).all()
    }
    if me.id not in member_ids or data.ratee_id not in member_ids or data.ratee_id == me.id:
        raise HTTPException(status_code=400, detail="评价对象不合法")
    exists = (
        db.query(Rating)
        .filter(
            Rating.match_id == match_id,
            Rating.rater_id == me.id,
            Rating.ratee_id == data.ratee_id,
        )
        .first()
    )
    if exists:
        raise HTTPException(status_code=400, detail="已经评价过")
    db.add(Rating(match_id=match_id, rater_id=me.id, ratee_id=data.ratee_id, score=data.score, tags=data.tags))
    ratee = db.get(User, data.ratee_id)
    if ratee:
        ratee.reputation = max(0.0, min(100.0, ratee.reputation + (data.score - 3) * 5))
    db.commit()
    return {"ok": True}
