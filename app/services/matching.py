"""匹配分：综合实力分 + 偏好加成，用于岛屿玩家/房间排序。

综合分 = 段位分×0.4 + KD×0.3 + 撤离率×0.2 + 场次×0.1（均为归一化）
偏好加成：位置互补 +5，时段/模式一致由调用方过滤
"""
import math


def _norm_tier(tier_score: int) -> float:
    return max(0.0, min(1.0, tier_score / 6000.0))


def _norm_kd(kd: float) -> float:
    return max(0.0, min(1.0, kd / 4.0))


def _norm_rate(rate: float) -> float:
    # 撤离率按百分比传入（如 40.4）
    return max(0.0, min(1.0, rate / 100.0))


def _norm_matches(matches: int) -> float:
    return max(0.0, min(1.0, math.log10(matches + 1) / 4.0))


def power_score(tier_score: int, kd: float, extract_rate: float, matches: int) -> float:
    """个人综合实力分，0~100。"""
    s = (
        0.4 * _norm_tier(tier_score)
        + 0.3 * _norm_kd(kd)
        + 0.2 * _norm_rate(extract_rate)
        + 0.1 * _norm_matches(matches)
    )
    return round(s * 100, 1)


def match_score(me: dict, cand: dict) -> float:
    """我 vs 候选人的匹配度，0~100。段位/KD 越接近分越高。"""
    tier_gap = abs(_norm_tier(me.get("tier_score", 0)) - _norm_tier(cand.get("tier_score", 0)))
    kd_gap = abs(_norm_kd(me.get("kd", 0)) - _norm_kd(cand.get("kd", 0)))
    score = 100 * (1 - 0.6 * tier_gap - 0.4 * kd_gap)
    return round(max(0.0, min(100.0, score)), 1)
