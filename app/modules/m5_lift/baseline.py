"""基线：促销前 14 天日均 × 有效天数 × 对照组同期倍数。

朴素实现用「促销前一天销量 × 天数」当基线：忽略了自然增长与品类趋势，
一周的促销跨了两周，基线直接偏 10%。对照组倍数把品类整体走势扣掉。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from ... import config
from ..m2_price import coerce


@dataclass
class Baseline:
    pre_days: int
    pre_daily_qty: Decimal
    pre_price_excl: Decimal
    control_pre_daily: Decimal
    control_during_daily: Decimal
    control_factor: Decimal
    base_daily_qty: Decimal
    base_qty: Decimal
    base_gmv: Decimal
    price_excl: Decimal
    promo_days: int

    def as_dict(self) -> dict:
        return {
            "pre_days": self.pre_days,
            "pre_daily_qty": str(self.pre_daily_qty),
            "pre_price_excl": str(self.pre_price_excl),
            "control_pre_daily": str(self.control_pre_daily),
            "control_during_daily": str(self.control_during_daily),
            "control_factor": str(self.control_factor),
            "base_daily_qty": str(self.base_daily_qty),
            "base_qty": str(self.base_qty),
            "base_gmv": str(self.base_gmv),
            "price_excl": str(self.price_excl),
            "promo_days": self.promo_days,
        }


def daterange(start, end):
    out = []
    cursor = start
    while cursor < end:
        out.append(cursor)
        cursor += dt.timedelta(days=1)
    return out


def _daily(rows, dates, sku, channel) -> Decimal:
    if not dates:
        return Decimal("0")
    seen: dict[object, Decimal] = {}
    for row in rows:
        if row.sku != sku or row.channel != channel or row.sale_date not in dates:
            continue
        seen[row.sale_date] = seen.get(row.sale_date, Decimal("0")) + Decimal(row.qty)
    if not seen:
        return Decimal("0")
    total = sum(seen.values(), Decimal("0"))
    return (total / Decimal(len(seen))).quantize(Decimal("0.0001"))


def _weighted_price(rows, dates, sku, channel, fallback: Decimal) -> Decimal:
    total_days = 0
    acc = Decimal("0")
    for date in dates:
        day_rows = [r for r in rows
                    if r.sku == sku and r.channel == channel and r.sale_date == date]
        if not day_rows:
            continue
        price = sum((r.price_excl for r in day_rows), Decimal("0")) / Decimal(len(day_rows))
        acc += price
        total_days += 1
    if total_days == 0:
        return fallback
    return (acc / Decimal(total_days)).quantize(config.PRICE_QUANT)


def compute(promo_windows, promo_dates, rows, control, base_prices) -> Baseline | str:
    """返回 Baseline，或用字符串返回阻断原因码。"""
    sku = promo_windows[0].sku
    channel = promo_windows[0].channel
    control_sku = config.CONTROL_MAP.get(sku)
    if not control_sku:
        return "A_NO_CONTROL"

    start = min(w.start for w in promo_windows)
    pre_start = start - dt.timedelta(days=config.BASELINE_WINDOW_DAYS)
    pre_dates = set(daterange(pre_start, start))
    pre_daily = _daily(rows, pre_dates, sku, channel)
    if pre_daily <= 0:
        return "A_NO_BASELINE"
    pre_price = _weighted_price(rows, sorted(pre_dates), sku, channel,
                                base_prices.get((sku, channel), Decimal("0")))
    if pre_price <= 0:
        return "A_NO_BASELINE"

    control_pre = _daily(control, pre_dates, control_sku, channel)
    control_during = _daily(control, promo_dates, control_sku, channel)
    if control_pre <= 0 or control_during <= 0:
        return "A_CONTROL_EMPTY"
    control_factor = (control_during / control_pre).quantize(Decimal("0.0001"))

    total_days = 0
    weighted = Decimal("0")
    for window in promo_windows:
        weighted += window.price_excl * Decimal(window.days)
        total_days += window.days
    if total_days == 0:
        return "A_NO_BASELINE"
    price_excl = (weighted / Decimal(total_days)).quantize(config.PRICE_QUANT)

    days_valid = Decimal(len(promo_dates))
    base_daily = (pre_daily * control_factor).quantize(Decimal("0.0001"))
    base_qty = (base_daily * days_valid).quantize(Decimal("0.0001"))
    base_gmv = coerce.money(base_qty * pre_price)
    return Baseline(
        pre_days=len(pre_dates),
        pre_daily_qty=pre_daily,
        pre_price_excl=pre_price,
        control_pre_daily=control_pre,
        control_during_daily=control_during,
        control_factor=control_factor,
        base_daily_qty=base_daily,
        base_qty=base_qty,
        base_gmv=base_gmv,
        price_excl=price_excl,
        promo_days=total_days,
    )


def actual_totals(rows, promo_dates, sku, channel) -> tuple[Decimal, Decimal]:
    qty = Decimal("0")
    gmv = Decimal("0")
    for row in rows:
        if row.sku == sku and row.channel == channel and row.sale_date in promo_dates:
            qty += Decimal(row.qty)
            gmv += row.amount_excl
    return qty, coerce.money(gmv)


def channel_totals(rows, dates, sku) -> dict[str, Decimal]:
    out: dict[str, Decimal] = {}
    for row in rows:
        if row.sku != sku or row.sale_date not in dates:
            continue
        out[row.channel] = out.get(row.channel, Decimal("0")) + row.amount_excl
    return {k: coerce.money(v) for k, v in out.items()}
