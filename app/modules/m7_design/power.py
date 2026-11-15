"""M7 基础计算：日序列、波动、最小实验期、功效、可识别效应与预期区间。

三个刻意的选择：
1. 统计单位是「一天一条日销售额」，不是一条明细行——同一天多行会被当成多个样本，
   实验期定短了却看不出来；
2. 波动用变异系数 `cv = sd / mean` 表达，换商品不用重调参数；
3. 基线窗天数进公式：`SE ≈ sd × sqrt(实验天数 + 基线天数)`，漏掉基线窗会让实验期偏短。

记 m = 基线窗天数，Δ = mean × 目标效应：
    k    = z(1 − α/2) + z(功效)
    SE   = sd × sqrt(n + m)
    Δ/SE = 目标效应 × n / (cv × sqrt(n + m))
    令 Δ/SE = k 解出 n：n = (A² + sqrt(A⁴ + 4·A²·m)) / 2，A = k·cv / 目标效应
"""
from __future__ import annotations

import datetime as dt
import math
from decimal import Decimal, ROUND_HALF_UP

# 目标效应允许的取值范围（相对效应）
MAX_RELATIVE_EFFECT = 1.0
# 实验期上限：超过这个天数就不适合单店单次实验，直接说不可行
MAX_EXPERIMENT_DAYS = 365

# Acklam 逆正态近似系数：误差 < 1.15e-9，够用来算 z 分位
_A = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
      1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
_B = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
      6.680131188771972e+01, -1.328068155288572e+01)
_C = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
      -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
_D = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
      3.754408661907416e+00)
_P_LOW = 0.02425
_P_HIGH = 1.0 - _P_LOW


def z_quantile(p: float) -> float:
    """标准正态分位：z(0.975) ≈ 1.959964，z(0.80) ≈ 0.841621。"""
    if not 0.0 < p < 1.0:
        raise ValueError("分位点必须落在 (0, 1) 开区间")
    if p < _P_LOW:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((_C[0] * q + _C[1]) * q + _C[2]) * q + _C[3]) * q + _C[4]) * q + _C[5]) / \
               ((((_D[0] * q + _D[1]) * q + _D[2]) * q + _D[3]) * q + 1.0)
    if p > _P_HIGH:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        return -(((((_C[0] * q + _C[1]) * q + _C[2]) * q + _C[3]) * q + _C[4]) * q + _C[5]) / \
               ((((_D[0] * q + _D[1]) * q + _D[2]) * q + _D[3]) * q + 1.0)
    q = p - 0.5
    r = q * q
    return (((((_A[0] * r + _A[1]) * r + _A[2]) * r + _A[3]) * r + _A[4]) * r + _A[5]) * q / \
           (((((_B[0] * r + _B[1]) * r + _B[2]) * r + _B[3]) * r + _B[4]) * r + 1.0)


def normal_cdf(x: float) -> float:
    """标准正态分布函数，用 erf 直算，不查表。"""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def build_series(rows, sku: str, channel: str) -> list[dict] | None:
    """把某商品某渠道的日销汇总成按日序列，缺日留在序列里并标 present=False。

    返回 None 表示这个商品在该渠道没有任何记录。
    """
    days: dict[str, dict] = {}
    for row in rows:
        if row["sku"] != sku or row["channel"] != channel:
            continue
        day = row["sale_date"]
        slot = days.setdefault(day, {"gmv": Decimal("0"), "qty": 0,
                                     "price": Decimal("0"), "lines": 0})
        slot["gmv"] += Decimal(str(row["amount_excl"]))
        slot["qty"] += int(row["qty"])
        slot["price"] += Decimal(str(row["price_excl"] or "0"))
        slot["lines"] += 1
    if not days:
        return None
    keys = sorted(days)
    cursor = dt.date.fromisoformat(keys[0])
    last = dt.date.fromisoformat(keys[-1])
    out: list[dict] = []
    while cursor <= last:
        key = cursor.isoformat()
        slot = days.get(key)
        if slot is None:
            out.append({"date": key, "present": False, "gmv": None,
                        "qty": 0, "price": None})
        else:
            out.append({"date": key, "present": True,
                        "gmv": slot["gmv"].quantize(Decimal("0.01")),
                        "qty": slot["qty"],
                        "price": (slot["price"] / Decimal(slot["lines"])).quantize(
                            Decimal("0.0001")) if slot["lines"] else None})
        cursor += dt.timedelta(days=1)
    return out


def stats(values) -> dict:
    """日均、日波动、变异系数；样本标准差用 n-1 作分母。"""
    numbers = [float(v) for v in values]
    n = len(numbers)
    if n == 0:
        return {"n": 0, "mean": 0.0, "sd": 0.0, "cv": 0.0}
    mean = sum(numbers) / n
    if n < 2:
        return {"n": n, "mean": mean, "sd": 0.0, "cv": 0.0}
    var = sum((x - mean) ** 2 for x in numbers) / (n - 1)
    sd = math.sqrt(var)
    cv = sd / mean if mean > 0 else 0.0
    return {"n": n, "mean": mean, "sd": sd, "cv": cv}


def critical_k(alpha: float, power: float) -> float:
    """双侧检验所需的标准化效应：z(1−α/2) + z(功效)。"""
    return z_quantile(1.0 - alpha / 2.0) + z_quantile(power)


def required_days(cv: float, target_lift: float, alpha: float, power: float,
                  baseline_days: int) -> int | None:
    """达到目标功效所需的最少实验天数；不可行时返回 None。

    不可行的情形：目标效应相对波动太小，14 天基线窗自身的不确定度已经吃掉全部预算，
    方程无正解。这时只能提示「把目标效应调大」或「加长基线窗」。
    """
    if cv <= 0 or target_lift <= 0 or baseline_days <= 0:
        return None
    k = critical_k(alpha, power)
    a = k * cv / target_lift
    a2 = a * a
    disc = a2 * a2 + 4.0 * a2 * baseline_days
    n = (a2 + math.sqrt(disc)) / 2.0
    days = int(math.ceil(n))
    if days <= 0 or days > MAX_EXPERIMENT_DAYS:
        return None
    return days


def detection_ratio(n: int, cv: float, target_lift: float, baseline_days: int) -> float:
    """Δ/SE：拿 n 天实验期去测目标效应，标准化后的信噪比。"""
    if n <= 0 or cv <= 0:
        return 0.0
    return target_lift * n / (cv * math.sqrt(n + baseline_days))


def power_at(n: int, cv: float, target_lift: float, alpha: float,
             baseline_days: int) -> float:
    """给定实验天数的功效：Φ(Δ/SE − z) + Φ(−Δ/SE − z)。"""
    ratio = detection_ratio(n, cv, target_lift, baseline_days)
    z = z_quantile(1.0 - alpha / 2.0)
    return min(1.0, max(0.0, normal_cdf(ratio - z) + normal_cdf(-ratio - z)))


def detectable_effect(n: int, cv: float, alpha: float, power: float,
                      baseline_days: int) -> float | None:
    """给定实验天数能识别的最小相对效应；天数或波动不可用时返回 None。"""
    if n <= 0 or cv <= 0:
        return None
    k = critical_k(alpha, power)
    return k * cv * math.sqrt(n + baseline_days) / n


def interval(mean: float, sd: float, n: int, target_lift: float, alpha: float,
             baseline_days: int, factor: float = 1.0) -> dict:
    """预期区间：预期增量 ± z × SE(增量)，SE 已含基线窗与稳健方差放大。"""
    z = z_quantile(1.0 - alpha / 2.0)
    total = Decimal(str(mean)) * Decimal(str(target_lift)) * Decimal(n)
    se = Decimal(str(sd)) * Decimal(str(math.sqrt(n + baseline_days))) * \
        Decimal(str(math.sqrt(max(factor, 1.0))))
    total = total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    se = se.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    half = (z * float(se)).__round__(6)
    low = (total - Decimal(str(half))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    high = (total + Decimal(str(half))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return {"lift": total, "se": se, "low": low, "high": high,
            "contains_zero": low <= 0}


def window_of(start: dt.date, days: int) -> tuple[dt.date, dt.date]:
    """实验窗左闭右开 [开始日, 开始日 + 天数)，与促销区间同一套端点规则。"""
    return start, start + dt.timedelta(days=max(0, days))


def dates_between(start: dt.date, end: dt.date) -> list[dt.date]:
    out: list[dt.date] = []
    cursor = start
    while cursor < end:
        out.append(cursor)
        cursor += dt.timedelta(days=1)
    return out


def dec(value, pattern: str = "0.0001") -> str:
    return str(Decimal(str(value)).quantize(Decimal(pattern), rounding=ROUND_HALF_UP))


def pct(value, digits: int = 2) -> str:
    return f"{float(value) * 100:.{digits}f}%"
