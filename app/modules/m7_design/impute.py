"""M7 缺日插补敏感性：缺日按丢弃 / 按 0 / 按同序列均值 三种补法各算一遍。

朴素实现把缺日直接跳过：有效天数变少、日均被放大、波动被压低，实验期定短，
跑完判不出来。本模块把三种补法都算出来，只要结论不一致就显式标出：
- 显著性翻转：三种补法下预期区间是否含 0 不一致；
- 天数翻转：所需实验天数的极差超过 10%。

缺日进的是历史序列，补法只改日均与波动，不改任何成交行。
"""
from __future__ import annotations

from decimal import Decimal

from . import power

MODES = (
    ("drop", "缺日丢弃"),
    ("zero", "缺日按 0"),
    ("mean", "缺日按均值"),
)
MODE_KEYS = tuple(key for key, _ in MODES)
MODE_LABELS = dict(MODES)
# 所需天数极差超过这个比例就算「缺日改变结论」
SIZE_SLACK = 0.10


def _mode_label(mode: str) -> str:
    return MODE_LABELS.get(mode, mode)


def fill(series, mode: str) -> dict:
    """按补法取一组日销售额，返回日均、日波动与补齐天数。"""
    present = [day for day in series if day["present"]]
    if mode == "zero":
        values = [day["gmv"] if day["present"] else Decimal("0") for day in series]
    elif mode == "mean":
        mean = (sum((day["gmv"] for day in present), Decimal("0")) / Decimal(len(present))
                if present else Decimal("0"))
        values = [day["gmv"] if day["present"] else mean for day in series]
    elif mode == "drop":
        values = [day["gmv"] for day in present]
    else:
        raise ValueError(f"未知的缺日补法：{mode}")
    stats = power.stats(values)
    return {
        "mode": mode,
        "label": _mode_label(mode),
        "days": len(series),
        "used": len(values),
        "present": len(present),
        "filled": len(series) - len(present) if mode != "drop" else 0,
        "values": values,
        "mean": stats["mean"],
        "sd": stats["sd"],
        "cv": stats["cv"],
    }


def sensitivity(series, *, target_lift: float, alpha: float, power_target: float,
                budget_days: int, baseline_days: int, variance_factor: float) -> dict:
    """三种补法各算：日均、波动、所需天数、预算下功效、可识别效应、预期区间。"""
    rows: list[dict] = []
    for mode, _label in MODES:
        filled = fill(series, mode)
        sd_used = filled["sd"] * (variance_factor ** 0.5)
        cv_used = sd_used / filled["mean"] if filled["mean"] > 0 else 0.0
        min_days = power.required_days(cv_used, target_lift, alpha, power_target,
                                       baseline_days)
        band = power.interval(filled["mean"], filled["sd"], budget_days, target_lift,
                              alpha, baseline_days, factor=variance_factor)
        ratio_power = power.power_at(budget_days, cv_used, target_lift, alpha,
                                     baseline_days)
        mde = power.detectable_effect(budget_days, cv_used, alpha, power_target,
                                      baseline_days)
        if min_days is None:
            verdict, level = "不可行", "error"
        elif min_days <= budget_days:
            verdict, level = "预算够", "ok"
        else:
            verdict, level = "预算不足", "warn"
        rows.append({
            "mode": mode,
            "label": filled["label"],
            "days": filled["days"],
            "used": filled["used"],
            "filled": filled["filled"],
            "mean": power.dec(filled["mean"], "0.01"),
            "sd": power.dec(filled["sd"], "0.01"),
            "cv": power.dec(cv_used, "0.0001"),
            "min_days": min_days,
            "min_days_text": str(min_days) if min_days is not None else "—",
            "budget_power": power.dec(ratio_power, "0.0001"),
            "budget_power_pct": power.pct(ratio_power, 1),
            "mde": power.dec(mde, "0.0001") if mde is not None else "—",
            "mde_pct": power.pct(mde, 1) if mde is not None else "—",
            "interval_low": str(band["low"]),
            "interval_high": str(band["high"]),
            "contains_zero": bool(band["contains_zero"]),
            "verdict": verdict,
            "verdict_level": level,
        })
    return {
        "rows": rows,
        "present_days": sum(1 for day in series if day["present"]),
        "missing_days": sum(1 for day in series if not day["present"]),
        "summary": _summary(rows),
    }


def _summary(rows) -> dict:
    """把三种补法放在一起判：显著性是否翻转、所需天数极差是否超限。"""
    zero_flags = {row["mode"] for row in rows if row["contains_zero"]}
    solid_flags = {row["mode"] for row in rows if not row["contains_zero"]}
    significance_flip = bool(zero_flags) and bool(solid_flags)
    sizes = [row["min_days"] for row in rows if row["min_days"] is not None]
    if len(sizes) < len(rows):
        size_flip = True
        smallest = largest = None
    else:
        smallest, largest = min(sizes), max(sizes)
        size_flip = smallest > 0 and (largest - smallest) / smallest > SIZE_SLACK
    sensitive = significance_flip or size_flip
    if significance_flip:
        detail = "区间是否含 0 因补法而变"
    elif size_flip and smallest is not None:
        detail = f"所需天数在 {smallest} 到 {largest} 天之间摆动"
    elif size_flip:
        detail = "有的补法算不出所需天数"
    else:
        detail = "三种补法结论一致"
    return {
        "significance_flip": significance_flip,
        "size_flip": size_flip,
        "sensitive": sensitive,
        "zero_modes": sorted(zero_flags),
        "solid_modes": sorted(solid_flags),
        "min_days_small": smallest,
        "min_days_large": largest,
        "detail": detail,
    }
