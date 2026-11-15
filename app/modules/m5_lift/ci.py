"""置信区间：用弹性斜率的 delta 法给出增量的区间宽度。

区间不是凭空给的：基线点估计来自对照组，宽度来自弹性标准误。
d(基线×exp(b·Δlnp))/db = 基线×Δlnp×exp(b·Δlnp)，乘上不含税单价就是金额的标准误。
斜率标准误为 0 时区间收成一点，这是可验证的不变量。
"""
from __future__ import annotations

import math
from decimal import Decimal

from ... import config
from ..m2_price import coerce


def log_ratio(promo_price: Decimal, base_price: Decimal) -> float | None:
    if promo_price <= 0 or base_price <= 0:
        return None
    return math.log(float(promo_price)) - math.log(float(base_price))


def interval(*, lift_gmv: Decimal, base_qty: Decimal, base_price: Decimal,
             dlnp: float | None, slope: float, se_slope: float) -> dict:
    if dlnp is None:
        return {"se_gmv": "0", "ci_low": str(coerce.money(lift_gmv)),
                "ci_high": str(coerce.money(lift_gmv)), "ratio": "1.0000"}
    ratio = math.exp(slope * dlnp)
    se_float = (float(base_price) * float(base_qty) * abs(dlnp)
                * ratio * float(se_slope))
    se_gmv = coerce.money(Decimal(str(se_float)))
    half = coerce.money(Decimal(str(float(config.SIGNIFICANCE_T))) * se_gmv)
    return {
        "se_gmv": str(se_gmv),
        "ci_low": str(coerce.money(lift_gmv - half)),
        "ci_high": str(coerce.money(lift_gmv + half)),
        "ratio": f"{Decimal(str(ratio)).quantize(Decimal('0.0001'))}",
    }


def crosses_zero(ci_low: Decimal, ci_high: Decimal) -> bool:
    return ci_low <= 0 <= ci_high
