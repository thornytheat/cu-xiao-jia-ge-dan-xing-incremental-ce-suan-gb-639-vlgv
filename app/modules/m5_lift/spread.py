"""渠道分摊：按实际销售额占比把增量分到渠道，尾差用最大余额法补齐。

朴素实现各自四舍五入到 0.01：两档比例各剩 0.005 时分别进位，
分摊合计比总额多 0.01，报表与总额对不上。最大余额法先取整到下沿，
再把剩下的最小单位按余数从大到小逐份补，合计恒等于总额。
"""
from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

from ... import config
from ..m2_price import coerce

UNIT = config.MONEY_QUANT


def largest_remainder(total: Decimal, weights: dict[str, Decimal]) -> dict[str, Decimal]:
    """把 total 按 weights 比例分到各渠道，分完合计恒等于 total。"""
    items = {key: value for key, value in weights.items() if value > 0}
    if not items:
        return {}
    total_units = int((coerce.money(total) / UNIT).to_integral_value(rounding=ROUND_DOWN))
    sign = -1 if total_units < 0 else 1
    total_units = abs(total_units)
    weight_sum = sum(items.values(), Decimal("0"))
    raw = {key: (Decimal(total_units) * value / weight_sum) for key, value in items.items()}
    floors = {key: int(value.to_integral_value(rounding=ROUND_DOWN))
              for key, value in raw.items()}
    assigned = sum(floors.values())
    leftover = total_units - assigned
    order = sorted(raw, key=lambda k: (-(raw[k] - floors[k]), k))
    for index in range(leftover):
        floors[order[index % len(order)]] += 1
    return {key: coerce.money(Decimal(sign * floors[key]) * UNIT) for key in floors}


def allocate(lift_gmv: Decimal, channel_gmv: dict[str, Decimal]) -> list[dict]:
    if not channel_gmv:
        return [{"channel": "全渠道", "ratio": "1.0000", "lift_gmv": str(lift_gmv),
                 "actual_gmv": str(coerce.money(lift_gmv))}]
    total_weight = sum(channel_gmv.values(), Decimal("0"))
    shares = largest_remainder(lift_gmv, channel_gmv)
    out = []
    for channel in sorted(channel_gmv):
        weight = channel_gmv[channel]
        out.append({
            "channel": channel,
            "ratio": str((weight / total_weight).quantize(Decimal("0.0001"))),
            "lift_gmv": str(shares[channel]),
            "actual_gmv": str(weight),
        })
    return out


def remainder_of(lift_gmv: Decimal, shares: list[dict]) -> Decimal:
    allocated = sum((Decimal(s["lift_gmv"]) for s in shares), Decimal("0"))
    return coerce.money(Decimal(lift_gmv) - allocated)
