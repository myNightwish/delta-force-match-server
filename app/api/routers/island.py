"""岛屿大厅：免费浏览。KD 对未入队用户隐藏，入队后可见。"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models import MatchPost, PlayerProfile, TeamMember, TeamMatch, User
from app.schemas.match import PostOut
from app.schemas.profile import IslandPlayerOut
from app.services.matching import match_score

router = APIRouter()


def _my_profile_dict(db: Session, user_id: int) -> dict:
    p = db.query(PlayerProfile).filter(PlayerProfile.user_id == user_id).first()
    if not p:
        return {}
    return {"tier_score": p.tier_score, "kd": p.kda_d}


@router.get("/island/players", response_model=list[IslandPlayerOut])
def list_players(
    island: str = Query(pattern="^(tech|companion)$"),
    kd_min: float | None = None,
    limit: int = Query(20, le=100),
    me: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = (
        db.query(User, PlayerProfile)
        .join(PlayerProfile, PlayerProfile.user_id == User.id)
        .filter(User.faction == island, User.id != me.id)
    )
    if kd_min is not None:
        q = q.filter(PlayerProfile.kda_d >= kd_min)
    rows = q.limit(limit).all()

    my = _my_profile_dict(db, me.id)
    out: list[IslandPlayerOut] = []
    for user, prof in rows:
        score = match_score(my, {"tier_score": prof.tier_score, "kd": prof.kda_d}) if my else None
        out.append(
            IslandPlayerOut(
                user_id=user.id,
                nickname=user.nickname,
                faction=user.faction,
                tier_name=prof.tier_name,
                tier_stars=prof.tier_stars,
                tier_score=prof.tier_score,
                matches=prof.matches,
                extract_rate=prof.extract_rate,
                kda=None,  # 未入队隐藏
                match_score=score,
            )
        )
    out.sort(key=lambda x: (x.match_score is not None, x.match_score or 0), reverse=True)
    return out


@router.get("/island/posts", response_model=list[PostOut])
def list_posts(
    island: str = Query(pattern="^(tech|companion)$"),
    me: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    posts = (
        db.query(MatchPost)
        .filter(MatchPost.island == island, MatchPost.status == "open")
        .order_by(MatchPost.id.desc())
        .limit(50)
        .all()
    )
    my = _my_profile_dict(db, me.id)
    result = []
    for post in posts:
        # 约定：TeamMatch 创建时房主即为成员，因此成员数 = TeamMember 计数；无小队时为 1（房主）
        tm = db.query(TeamMatch).filter(TeamMatch.post_id == post.id).first()
        if tm:
            count = db.query(TeamMember).filter(TeamMember.match_id == tm.id).count()
        else:
            count = 1
        owner = db.get(User, post.owner_id)
        oprof = db.query(PlayerProfile).filter(PlayerProfile.user_id == post.owner_id).first()
        score = (
            match_score(my, {"tier_score": oprof.tier_score, "kd": oprof.kda_d})
            if (my and oprof)
            else None
        )
        result.append(
            PostOut(
                id=post.id,
                owner_id=post.owner_id,
                island=post.island,
                mode=post.mode,
                recruit_count=post.recruit_count,
                tier_min=post.tier_min,
                tier_max=post.tier_max,
                kd_min=post.kd_min,
                roles_needed=post.roles_needed,
                mic_required=post.mic_required,
                play_time=post.play_time,
                status=post.status,
                member_count=count,
                created_at=post.created_at,
                owner_nickname=owner.nickname if owner else f"岛民#{post.owner_id}",
                owner_tier_name=oprof.tier_name if oprof else None,
                owner_tier_stars=oprof.tier_stars if oprof else None,
                owner_matches=oprof.matches if oprof else None,
                owner_extract_rate=oprof.extract_rate if oprof else None,
                match_score=score,
            )
        )
    return result
