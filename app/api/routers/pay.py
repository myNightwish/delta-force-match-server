"""匹配费用（¥1 保证金）。

当前为开发桩：订单与回调均为模拟。
生产 TODO: 企业微信支付商户号 + v3 API + 回调验签。
"""
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models import Payment, User
from app.schemas.pay import OrderCreateIn, OrderOut, PayNotifyIn
from app.services.billing import match_fee_cents

router = APIRouter()


@router.post("/pay/order", response_model=OrderOut)
def create_order(data: OrderCreateIn, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # 幂等：同一小队已有待支付订单则直接返回（接受申请时已自动建单）
    if data.match_id is not None:
        existing = (
            db.query(Payment)
            .filter(
                Payment.user_id == me.id,
                Payment.match_id == data.match_id,
                Payment.status == "pending",
            )
            .first()
        )
        if existing:
            return OrderOut(
                order_no=existing.order_no,
                amount_cents=existing.amount_cents,
                status=existing.status,
                pay_payload={
                    "qr_stub": f"wxpay-dev://{existing.order_no}",
                    "hint": "开发桩：调用 POST /api/pay/notify 模拟支付回调",
                },
            )
    order_no = uuid.uuid4().hex
    payment = Payment(
        user_id=me.id,
        match_id=data.match_id,
        order_no=order_no,
        amount_cents=match_fee_cents(),
        status="pending",
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)
    return OrderOut(
        order_no=order_no,
        amount_cents=payment.amount_cents,
        status=payment.status,
        pay_payload={
            "qr_stub": f"wxpay-dev://{order_no}",
            "hint": "开发桩：调用 POST /api/pay/notify 模拟支付回调",
        },
    )


@router.post("/pay/notify")
def pay_notify(data: PayNotifyIn, db: Session = Depends(get_db)):
    # TODO: 生产换微信支付回调验签
    payment = db.query(Payment).filter(Payment.order_no == data.order_no).first()
    if payment is None:
        raise HTTPException(status_code=404, detail="订单不存在")
    if data.paid and payment.status == "pending":
        payment.status = "paid"
        payment.paid_at = datetime.utcnow()
        db.commit()
    return {"ok": True, "status": payment.status}


@router.get("/pay/orders")
def my_orders(me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    payments = db.query(Payment).filter(Payment.user_id == me.id).order_by(Payment.id.desc()).all()
    return [
        {
            "order_no": p.order_no,
            "amount_cents": p.amount_cents,
            "status": p.status,
            "match_id": p.match_id,
            "paid_at": p.paid_at,
        }
        for p in payments
    ]
