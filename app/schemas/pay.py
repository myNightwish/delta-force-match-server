from datetime import datetime

from pydantic import BaseModel, ConfigDict


class OrderCreateIn(BaseModel):
    """发起匹配时创建 ¥1 保证金订单。match_id 可为空（房主建房时先建小队后补）。"""

    match_id: int | None = None


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    order_no: str
    amount_cents: int
    status: str
    # 开发桩：模拟支付二维码载荷，生产换微信 JSAPI/Native 参数
    pay_payload: dict | None = None


class PayNotifyIn(BaseModel):
    """模拟支付回调。生产换微信支付 v3 回调 + 验签。"""

    order_no: str
    paid: bool = True


class RefundOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    payment_id: int
    amount_cents: int
    platform_fee_cents: int
    reason: str
    created_at: datetime | None = None
