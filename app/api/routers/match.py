"""组队撮合状态机（小队 3 人：房主 + 招募 1~2 人）。

建房 -> 申请 -> 房主接受(建队,双方产生 ¥1 待支付订单) -> 各自支付
-> 入队可见 KD -> 全员确定 -> 锁定(交易完成,不退款)
锁定前退出 -> 退 80%，平台收 20% 服务费；房主退出则解散并给全员退款。
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models import (
    ChatMessage,
    MatchApplication,
    MatchPost,
    Payment,
    PlayerProfile,
    Refund,
    TeamMatch,
    TeamMember,
    User,
)
from app.schemas.match import (
    ApplicationOut,
    PostCreateIn,
    PostOut,
    RoomCodeIn,
    TeamMemberOut,
    TeamOut,
)
from app.services.billing import match_fee_cents, refund_split

router = APIRouter()

TEAM_SIZE = 3


def _post_or_404(db: Session, post_id: int) -> MatchPost:
    post = db.get(MatchPost, post_id)
    if not post:
        raise HTTPException(status_code=404, detail="房间不存在")
    return post


def _match_or_404(db: Session, match_id: int) -> TeamMatch:
    m = db.get(TeamMatch, match_id)
    if not m:
        raise HTTPException(status_code=404, detail="队伍不存在")
    return m


def _member_or_403(db: Session, match_id: int, user_id: int) -> TeamMember:
    mem = (
        db.query(TeamMember)
        .filter(TeamMember.match_id == match_id, TeamMember.user_id == user_id)
        .first()
    )
    if not mem:
        raise HTTPException(status_code=403, detail="你不在这支队伍中")
    return mem


def _paid_payment(db: Session, member: TeamMember) -> Payment:
    if not member.payment_id:
        raise HTTPException(status_code=400, detail="请先支付 ¥1 匹配费")
    p = db.get(Payment, member.payment_id)
    if not p or p.status != "paid":
        raise HTTPException(status_code=400, detail="请先支付 ¥1 匹配费")
    return p


def _make_order(db: Session, user_id: int, match_id: int) -> Payment:
    p = Payment(
        user_id=user_id,
        match_id=match_id,
        order_no=uuid.uuid4().hex,
        amount_cents=match_fee_cents(),
        status="pending",
    )
    db.add(p)
    return p


def _kda_str(db: Session, user_id: int) -> str | None:
    prof = db.query(PlayerProfile).filter(PlayerProfile.user_id == user_id).first()
    if not prof:
        return None
    return f"{prof.kda_k:g}/{prof.kda_d:g}/{prof.kda_a:g}"


def _team_out(db: Session, match: TeamMatch) -> TeamOut:
    members = db.query(TeamMember).filter(TeamMember.match_id == match.id).all()
    outs = []
    for mem in members:
        user = db.get(User, mem.user_id)
        outs.append(
            TeamMemberOut(
                user_id=mem.user_id,
                nickname=user.nickname if user else "",
                has_confirmed=mem.has_confirmed,
                kda=_kda_str(db, mem.user_id),
            )
        )
    return TeamOut(
        match_id=match.id,
        status=match.status,
        members=outs,
        all_confirmed=bool(members) and all(m.has_confirmed for m in members),
    )


def _refund_payment(db: Session, payment: Payment, reason: str) -> int:
    """锁定前退款：退 80%。返回退给用户的金额（分）。"""
    if payment.status != "paid":
        payment.status = "cancelled"
        db.add(payment)
        return 0
    refund_cents, fee_cents = refund_split(payment.amount_cents)
    payment.status = "refunded"
    db.add(payment)
    db.add(
        Refund(
            payment_id=payment.id,
            amount_cents=refund_cents,
            platform_fee_cents=fee_cents,
            reason=reason,
        )
    )
    return refund_cents


# ---------------- 房帖 ----------------


@router.post("/posts", response_model=PostOut)
def create_post(data: PostCreateIn, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    post = MatchPost(owner_id=me.id, **data.model_dump())
    db.add(post)
    db.commit()
    db.refresh(post)
    return PostOut(
        id=post.id, owner_id=post.owner_id, island=post.island, mode=post.mode,
        recruit_count=post.recruit_count, tier_min=post.tier_min, tier_max=post.tier_max,
        kd_min=post.kd_min, roles_needed=post.roles_needed, mic_required=post.mic_required,
        play_time=post.play_time, status=post.status, member_count=1, created_at=post.created_at,
    )


@router.post("/posts/{post_id}/apply", response_model=ApplicationOut)
def apply_post(post_id: int, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    post = _post_or_404(db, post_id)
    if post.status != "open":
        raise HTTPException(status_code=400, detail="该房间已不在招募中")
    if post.owner_id == me.id:
        raise HTTPException(status_code=400, detail="不能申请自己的房间")
    exists = (
        db.query(MatchApplication)
        .filter(
            MatchApplication.post_id == post_id,
            MatchApplication.applicant_id == me.id,
            MatchApplication.status.in_(["pending", "accepted"]),
        )
        .first()
    )
    if exists:
        raise HTTPException(status_code=400, detail="你已申请过该房间")
    app_ = MatchApplication(post_id=post_id, applicant_id=me.id)
    db.add(app_)
    db.commit()
    db.refresh(app_)
    return app_


@router.post("/applications/{app_id}/accept", response_model=TeamOut)
def accept_application(app_id: int, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    app_ = db.get(MatchApplication, app_id)
    if not app_ or app_.status != "pending":
        raise HTTPException(status_code=404, detail="申请不存在或已处理")
    post = _post_or_404(db, app_.post_id)
    if post.owner_id != me.id:
        raise HTTPException(status_code=403, detail="只有房主可以接受申请")
    if post.status != "open":
        raise HTTPException(status_code=400, detail="该房间已不在招募中")

    accepted = (
        db.query(MatchApplication)
        .filter(MatchApplication.post_id == post.id, MatchApplication.status == "accepted")
        .count()
    )
    if accepted >= post.recruit_count:
        raise HTTPException(status_code=400, detail="招募名额已满")

    app_.status = "accepted"
    match = db.query(TeamMatch).filter(TeamMatch.post_id == post.id).first()
    if not match:
        match = TeamMatch(post_id=post.id)
        db.add(match)
        db.flush()
        db.add(TeamMember(match_id=match.id, user_id=post.owner_id))  # 房主入队
    db.add(TeamMember(match_id=match.id, user_id=app_.applicant_id))
    db.flush()

    # 双方产生 ¥1 待支付订单（发起匹配时付费）
    for mem in db.query(TeamMember).filter(TeamMember.match_id == match.id).all():
        if mem.payment_id is None:
            order = _make_order(db, mem.user_id, match.id)
            db.flush()
            mem.payment_id = order.id
    db.commit()

    if accepted + 1 >= post.recruit_count:
        post.status = "full"
        db.commit()
    db.refresh(match)
    return _team_out(db, match)


@router.post("/applications/{app_id}/reject", response_model=ApplicationOut)
def reject_application(app_id: int, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    app_ = db.get(MatchApplication, app_id)
    if not app_ or app_.status != "pending":
        raise HTTPException(status_code=404, detail="申请不存在或已处理")
    post = _post_or_404(db, app_.post_id)
    if post.owner_id != me.id:
        raise HTTPException(status_code=403, detail="只有房主可以拒绝申请")
    app_.status = "rejected"
    db.commit()
    return app_


# ---------------- 小队 ----------------


@router.get("/me/teams")
def my_teams(me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """我加入的所有小队（含待支付/组建中）。"""
    members = db.query(TeamMember).filter(TeamMember.user_id == me.id).all()
    result = []
    for mem in members:
        match = db.get(TeamMatch, mem.match_id)
        if not match:
            continue
        post = db.get(MatchPost, match.post_id)
        count = db.query(TeamMember).filter(TeamMember.match_id == match.id).count()
        payment = db.get(Payment, mem.payment_id) if mem.payment_id else None
        result.append(
            {
                "match_id": match.id,
                "post_id": match.post_id,
                "island": post.island if post else "",
                "status": match.status,
                "member_count": count,
                "recruit_count": post.recruit_count if post else 1,
                "has_confirmed": mem.has_confirmed,
                "pay_status": payment.status if payment else "none",
                "is_owner": post.owner_id == me.id if post else False,
            }
        )
    return result


@router.get("/posts/{post_id}/applications", response_model=list[ApplicationOut])
def post_applications(post_id: int, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """房主查看自己房间的申请列表。"""
    post = _post_or_404(db, post_id)
    if post.owner_id != me.id:
        raise HTTPException(status_code=403, detail="只有房主可以查看申请")
    apps = (
        db.query(MatchApplication)
        .filter(MatchApplication.post_id == post_id)
        .order_by(MatchApplication.id.desc())
        .all()
    )
    return apps


@router.post("/matches/{match_id}/confirm", response_model=TeamOut)
def confirm_match(match_id: int, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """队员确定组队：需已支付。全员确定且满员 -> 锁定，交易完成，不退款。"""
    match = _match_or_404(db, match_id)
    if match.status == "locked":
        return _team_out(db, match)
    if match.status != "forming":
        raise HTTPException(status_code=400, detail="队伍不在可确认状态")
    member = _member_or_403(db, match_id, me.id)
    _paid_payment(db, member)  # 未付费不能确定

    member.has_confirmed = True
    db.commit()

    members = db.query(TeamMember).filter(TeamMember.match_id == match.id).all()
    post = _post_or_404(db, match.post_id)
    full_size = 1 + post.recruit_count
    if len(members) == full_size and all(m.has_confirmed for m in members):
        match.status = "locked"
        post.status = "locked"
        for m in members:  # 保证金核销
            p = db.get(Payment, m.payment_id) if m.payment_id else None
            if p and p.status == "paid":
                p.status = "consumed"
        db.add(ChatMessage(match_id=match.id, sender_id=me.id, content="队伍已锁定，交易完成", kind="system"))
        db.commit()
    return _team_out(db, match)


@router.post("/matches/{match_id}/quit")
def quit_match(match_id: int, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """锁定前可退出：退 80%。锁定后不可退出。房主退出则解散队伍。"""
    match = _match_or_404(db, match_id)
    if match.status == "locked":
        raise HTTPException(status_code=400, detail="已确定组队，交易完成，不可退出")
    if match.status != "forming":
        raise HTTPException(status_code=400, detail="队伍不在可退出状态")
    member = _member_or_403(db, match_id, me.id)
    post = _post_or_404(db, match.post_id)

    payment = db.get(Payment, member.payment_id) if member.payment_id else None
    refund_cents = _refund_payment(db, payment, "锁定前退出队伍") if payment else 0
    db.delete(member)

    if me.id == post.owner_id:
        # 房主退出：解散，给其他已付费成员退款
        for m in db.query(TeamMember).filter(TeamMember.match_id == match.id).all():
            p = db.get(Payment, m.payment_id) if m.payment_id else None
            if p:
                _refund_payment(db, p, "房主解散队伍")
            db.delete(m)
        match.status = "disbanded"
        post.status = "closed"
    else:
        post.status = "open"  # 重新开放招募
    db.commit()
    return {"ok": True, "refund_cents": refund_cents}


@router.get("/matches/{match_id}/team", response_model=TeamOut)
def team_detail(match_id: int, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """入队后可见全员 KD。"""
    match = _match_or_404(db, match_id)
    _member_or_403(db, match_id, me.id)
    return _team_out(db, match)


@router.post("/matches/{match_id}/room-code")
def post_room_code(
    match_id: int, data: RoomCodeIn, me: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """锁定后在队伍公屏发送游戏房间码/ID。"""
    match = _match_or_404(db, match_id)
    if match.status != "locked":
        raise HTTPException(status_code=400, detail="队伍锁定后才能发送房间码")
    _member_or_403(db, match_id, me.id)
    msg = ChatMessage(match_id=match_id, sender_id=me.id, content=data.room_code, kind="room_code")
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return {"ok": True, "message_id": msg.id}
