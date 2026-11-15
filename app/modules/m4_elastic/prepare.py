"""建模样本准备：价格取对数、0 销量两种处理、分组与最小样本判定。

样本规则（记在 DATA-DICTIONARY.md）：
- x = ln(不含税单价)；
- shift 方式：y = ln(销量 + 0.5)，所有点统一平移，0 销量的点保留；
- drop 方式：y = ln(销量)，0 销量的点剔除并单独计数；
- 同一商品同一渠道一天一条，重复日已在质量阶段处理；
- 至少 6 个样本且价格至少 2 个不同取值才允许估计。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal

from ... import config


@dataclass
class Sample:
    scope: str
    group_key: str
    xs: list[float] = field(default_factory=list)
    ys: list[float] = field(default_factory=list)
    zero_count: int = 0
    dropped_count: int = 0
    prices: set = field(default_factory=set)
    note_codes: list[str] = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.xs)

    @property
    def price_levels(self) -> int:
        return len(self.prices)


def _log_price(price: Decimal) -> float:
    return math.log(float(price))


def build(rows, zero_mode: str = config.ZERO_MODE_SHIFT) -> list[Sample]:
    buckets: dict[tuple[str, str], Sample] = {}
    pooled: dict[str, Sample] = {}
    overall = Sample(scope="all", group_key="全渠道")

    for row in sorted(rows, key=lambda r: (r.sku, r.channel, r.sale_date, r.line_no)):
        key = (row.sku, row.channel)
        if key not in buckets:
            buckets[key] = Sample(scope="sku", group_key=f"{row.sku}/{row.channel}")
        if row.channel not in pooled:
            pooled[row.channel] = Sample(scope="channel", group_key=row.channel)
        targets = (buckets[key], pooled[row.channel], overall)
        for sample in targets:
            sample.prices.add(str(row.price_excl))
            if row.qty <= 0:
                sample.zero_count += 1
                if zero_mode == config.ZERO_MODE_DROP:
                    sample.dropped_count += 1
                    continue
            if zero_mode == config.ZERO_MODE_SHIFT:
                y = math.log(float(row.qty) + float(config.ZERO_SHIFT))
            else:
                y = math.log(float(row.qty))
            sample.xs.append(_log_price(row.price_excl))
            sample.ys.append(y)

    out = list(buckets.values()) + list(pooled.values()) + [overall]
    for sample in out:
        if sample.zero_count and zero_mode == config.ZERO_MODE_SHIFT:
            sample.note_codes.append("L_ZERO_QTY_SHIFTED")
        if sample.dropped_count and zero_mode == config.ZERO_MODE_DROP:
            sample.note_codes.append("L_ZERO_QTY_DROPPED")
    out.sort(key=lambda s: (s.scope, s.group_key))
    return out


def readable(sample: Sample) -> bool:
    if sample.n < config.MIN_SAMPLES:
        return False
    if sample.price_levels < 2:
        return False
    return True


def blocking_code(sample: Sample) -> str | None:
    if sample.price_levels < 2:
        return "L_NO_PRICE_VARIATION"
    if sample.n < config.MIN_SAMPLES:
        return "L_SAMPLE_TOO_SMALL"
    return None
