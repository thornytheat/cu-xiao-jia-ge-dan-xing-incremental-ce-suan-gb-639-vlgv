"""M7 控制组选择：水平比、前置趋势差、实验期促销重叠。

两个坑：
1. 只看日均接近就选对照——两组前置趋势不同，同期倍数把趋势差一起算进基线；
2. 对照商品自己也在这段时间促销——同期倍数被它自己的促销抬起来，基线跟着虚高。
   所以先按实验期判重叠（有重叠直接排除），再按前置趋势差排序。

前置趋势差 = |ln(对照后半段/前半段) − ln(目标后半段/前半段)|，取对数差是为了可加。
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field

from ... import config
from . import power

# 前置趋势差超过这个数视为选择偏差
TREND_SLACK = 0.10
# 有促销重叠时的偏误惩罚，保证它排在所有无重叠候选之后
OVERLAP_PENALTY = 0.5
# 前置段可用天数不足时给的未知惩罚
SHORT_PENALTY = 0.5
# 两个半段各自至少要有的有效天数
MIN_HALF_DAYS = 3


@dataclass
class Candidate:
    sku: str
    channel: str
    registered: bool
    level_ratio: float
    target_trend: float | None
    control_trend: float | None
    trend_gap: float
    overlap_days: int
    overlap_dates: list[str] = field(default_factory=list)
    bias: float = 0.0
    status: str = "可用"
    code: str = ""
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "sku": self.sku,
            "channel": self.channel,
            "registered": 1 if self.registered else 0,
            "level_ratio": power.dec(self.level_ratio, "0.0001"),
            "target_trend": (power.dec(self.target_trend, "0.0001")
                             if self.target_trend is not None else "—"),
            "control_trend": (power.dec(self.control_trend, "0.0001")
                              if self.control_trend is not None else "—"),
            "trend_gap": power.dec(self.trend_gap, "0.0001"),
            "overlap_days": self.overlap_days,
            "overlap_dates": self.overlap_dates,
            "bias": power.dec(self.bias, "0.0001"),
            "status": self.status,
            "code": self.code,
            "note": self.note,
        }


def overlap_dates(windows, sku: str, channel: str, start: dt.date,
                  end: dt.date) -> list[str]:
    """对照商品在实验窗内自己生效的促销日期（左闭右开，重叠日只算一次）。"""
    hits: set[str] = set()
    for window in windows:
        if window["sku"] != sku or window["channel"] != channel:
            continue
        try:
            w_start = dt.date.fromisoformat(str(window["start_date"]))
            w_end = dt.date.fromisoformat(str(window["end_date"]))
        except (TypeError, ValueError):
            continue
        for day in power.dates_between(max(start, w_start), min(end, w_end)):
            hits.add(day.isoformat())
    return sorted(hits)


def trend(series, window: set[str] | None = None) -> float | None:
    """前半段与后半段的日均之比取自然对数；任一半段有效天数不足就返回 None。

    window 给定时只在那些日期里比（促销开始前的干净窗口），否则用整段历史。
    """
    if not series:
        return None
    picked = [day for day in series if window is None or day["date"] in window]
    if len(picked) < MIN_HALF_DAYS * 2:
        return None
    middle = len(picked) // 2
    early = [day for day in picked[:middle] if day["present"]]
    late = [day for day in picked[middle:] if day["present"]]
    if len(early) < MIN_HALF_DAYS or len(late) < MIN_HALF_DAYS:
        return None
    early_mean = power.stats([day["gmv"] for day in early])["mean"]
    late_mean = power.stats([day["gmv"] for day in late])["mean"]
    if early_mean <= 0 or late_mean <= 0:
        return None
    return math.log(late_mean / early_mean)


def build(rows, windows, target_series, target_key: tuple[str, str],
         start: dt.date, end: dt.date, trend_window: set[str] | None = None
         ) -> list[Candidate]:
    """同渠道其它商品序列逐个评分；映射表里登记的那个只加标记，不加分。"""
    target_sku, channel = target_key
    target_trend = trend(target_series, trend_window)
    target_mean = power.stats([day["gmv"] for day in target_series if day["present"]])["mean"]
    registered = config.CONTROL_MAP.get(target_sku)
    others: dict[str, None] = {}
    for row in rows:
        if row["channel"] != channel or row["sku"] == target_sku:
            continue
        others.setdefault(row["sku"], None)
    out: list[Candidate] = []
    for sku in sorted(others):
        series = power.build_series(rows, sku, channel)
        if not series:
            continue
        control_trend = trend(series, trend_window)
        mean = power.stats([day["gmv"] for day in series if day["present"]])["mean"]
        level_ratio = (mean / target_mean) if target_mean > 0 else 0.0
        if target_trend is None or control_trend is None:
            gap = 0.0
            code, note, status = "D_CONTROL_SHORT", "半段天数不足，趋势不可比", "不可比"
            bias = SHORT_PENALTY
        else:
            gap = abs(control_trend - target_trend)
            if gap > TREND_SLACK:
                code, note, status = "D_CONTROL_TREND", "前置趋势差过大", "偏差"
            else:
                code, note, status = "", "趋势可比", "可用"
            bias = gap
        hits = overlap_dates(windows, sku, channel, start, end)
        if hits:
            code = "D_CONTROL_OVERLAP"
            note = f"实验期内有 {len(hits)} 天自有促销"
            status = "排除"
            bias += OVERLAP_PENALTY
        if registered == sku and status == "可用":
            note = "趋势可比 · 映射内"
        out.append(Candidate(
            sku=sku, channel=channel, registered=registered == sku,
            level_ratio=level_ratio, target_trend=target_trend,
            control_trend=control_trend, trend_gap=gap, overlap_days=len(hits),
            overlap_dates=hits, bias=bias, status=status, code=code, note=note,
        ))
    rank = {"可用": 0, "偏差": 1, "不可比": 2, "排除": 3}
    out.sort(key=lambda item: (rank.get(item.status, 9), -1 if item.registered else 0,
                               item.bias, item.sku))
    return out


def best(candidates) -> Candidate | None:
    """只从「可用」里挑；一个可用都没有时返回 None，由编排层给阻断原因码。"""
    for item in candidates:
        if item.status == "可用":
            return item
    return None


def target_window_conflict(windows, sku: str, channel: str, start: dt.date,
                           end: dt.date) -> list[str]:
    """目标商品自己在实验窗内的促销日期：设计期撞上自己的促销，窗口就不干净。"""
    return overlap_dates(windows, sku, channel, start, end)
