"""计费规则：¥1 匹配保证金。

- 锁定前退出/取消：退 80%，平台收 20% 服务费
- 全员确定组队后：交易完成，不退款
"""
from app.core.config import settings


def refund_split(paid_cents: int) -> tuple[int, int]:
    """返回 (退给用户金额, 平台服务费)，单位分。"""
    refund_cents = int(paid_cents * settings.REFUND_RATE)
    fee_cents = paid_cents - refund_cents
    return refund_cents, fee_cents


def match_fee_cents() -> int:
    return settings.MATCH_FEE_CENTS
