"""活动价版本解析：左闭右开区间、版本优先级、重叠标记、空档标记。

对同一行的两种朴素写法都会错：
1. 闭区间 [start, end]：v2 结束日与 v3 开始日是同一天，这天被算两次；
2. 按文件书写顺序覆盖：把 v2 写在 v1 前面，生效版本就变了。
本模块按版本号排序后逐日覆盖，并用左闭右开区间切分，结果与书写顺序无关。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from ... import config
from . import coerce


@dataclass
class Version:
    promo_id: str
    sku: str
    channel: str
    version: int
    start: dt.date | None
    end: dt.date | None
    price_incl: Decimal | None
    price_excl: Decimal | None
    tax_rate: Decimal
    status: str
    note: str
    row_no: int
    effective: bool = False
    overlap: bool = False
    problems: list[str] = field(default_factory=list)


@dataclass
class Window:
    promo_id: str
    sku: str
    channel: str
    version: int
    start: dt.date
    end: dt.date
    price_incl: Decimal
    price_excl: Decimal

    @property
    def days(self) -> int:
        return max(0, (self.end - self.start).days)


def _iter_dates(start: dt.date, end: dt.date):
    cursor = start
    while cursor < end:
        yield cursor
        cursor += dt.timedelta(days=1)


def parse_versions(records) -> list[Version]:
    out: list[Version] = []
    for record in records:
        data = record.data
        promo_id = (data.get("促销编号") or "").strip()
        if not promo_id:
            continue
        start = coerce.to_date(data.get("开始日期"))
        end = coerce.to_date(data.get("结束日期"))
        price_incl, notes = coerce.to_price_per_piece(
            data.get("活动价"), data.get("单价单位", ""))
        rate, tax_note = coerce.to_tax_rate(data.get("税率"))
        try:
            version = int(coerce.clean_number(data.get("版本") or "0"))
        except (TypeError, ValueError):
            version = 0
        item = Version(
            promo_id=promo_id,
            sku=(data.get("商品编码") or "").strip(),
            channel=(data.get("渠道") or "").strip(),
            version=version,
            start=start,
            end=end,
            price_incl=price_incl,
            price_excl=(coerce.excl_of(price_incl, rate) if price_incl is not None else None),
            tax_rate=rate,
            status=(data.get("状态") or "").strip(),
            note=(data.get("备注") or "").strip(),
            row_no=record.line_no,
        )
        if notes:
            item.problems.extend(notes)
        if tax_note:
            item.problems.append(tax_note)
        out.append(item)
    return out


def build(records) -> dict:
    versions = parse_versions(records)
    quarantine: list[dict] = []

    candidates: dict[str, list[Version]] = {}
    day_map: dict[str, dict[dt.date, int]] = {}
    cover: dict[str, dict[dt.date, int]] = {}
    span: dict[str, tuple[dt.date, dt.date]] = {}
    by_promo: dict[str, list[Version]] = {}

    for item in versions:
        by_promo.setdefault(item.promo_id, []).append(item)
        if item.status != config.PROMO_STATUS_ACTIVE:
            continue
        if item.start is None or item.end is None or item.end <= item.start:
            quarantine.append({
                "code": "Q_PROMO_BAD_RANGE", "stage": "quality",
                "source": "promo_calendar.csv", "line_no": item.row_no,
                "field": "开始日期", "raw_value": format_range(item),
                "detail": f"{item.promo_id} v{item.version} 区间无效",
            })
            continue
        if item.price_incl is None or item.price_incl <= 0:
            quarantine.append({
                "code": "P_PRICE_ZERO", "stage": "quality",
                "source": "promo_calendar.csv", "line_no": item.row_no,
                "field": "活动价",
                "raw_value": str(item.price_incl) if item.price_incl is not None else "—",
                "detail": f"{item.promo_id} v{item.version} 活动价不可用",
            })
            continue
        candidates.setdefault(item.promo_id, []).append(item)

    windows: list[Window] = []
    overlap_days: dict[str, int] = {}
    gap_days: dict[str, list[str]] = {}

    for promo_id, items in candidates.items():
        # 显式优先级：版本号升序逐日覆盖，最大版本号最终生效
        ordered = sorted(items, key=lambda v: (v.version, v.start, v.end))
        days = day_map.setdefault(promo_id, {})
        counter = cover.setdefault(promo_id, {})
        lo, hi = ordered[0].start, ordered[0].end
        for item in ordered:
            lo = min(lo, item.start)
            hi = max(hi, item.end)
            for day in _iter_dates(item.start, item.end):
                days[day] = item.version
                counter[day] = counter.get(day, 0) + 1
        span[promo_id] = (lo, hi)
        overlap_days[promo_id] = sum(1 for c in counter.values() if c > 1)
        gaps = [d.isoformat() for d in _iter_dates(lo, hi) if d not in days]
        gap_days[promo_id] = gaps
        price_by_version = {v.version: v for v in ordered}
        runs: list[tuple[dt.date, dt.date, int]] = []
        cursor = lo
        while cursor < hi:
            version_here = days.get(cursor)
            if version_here is None:
                cursor += dt.timedelta(days=1)
                continue
            run_start = cursor
            while cursor < hi and days.get(cursor) == version_here:
                cursor += dt.timedelta(days=1)
            runs.append((run_start, cursor, version_here))
        for run_start, run_end, run_version in runs:
            item = price_by_version[run_version]
            windows.append(Window(
                promo_id=promo_id, sku=item.sku, channel=item.channel,
                version=run_version, start=run_start, end=run_end,
                price_incl=item.price_incl, price_excl=item.price_excl))

    for item in versions:
        item.effective = item.promo_id in candidates and item.status == config.PROMO_STATUS_ACTIVE
        item.overlap = overlap_days.get(item.promo_id, 0) > 0

    # 整体没有任何可生效版本的促销：分别给出原因
    for promo_id, items in by_promo.items():
        if promo_id in candidates:
            continue
        # 有生效版本但被价格/区间拦下的，已经记了具体原因码，不重复记「无生效版本」
        if any(v.status == config.PROMO_STATUS_ACTIVE for v in items):
            continue
        draft = [v for v in items if v.status == config.PROMO_STATUS_DRAFT]
        void = [v for v in items if v.status == config.PROMO_STATUS_VOID]
        if draft:
            code, detail = "Q_PROMO_NO_VERSION", "版本状态为待审，未生效"
        elif void:
            code, detail = "Q_PROMO_NO_VERSION", "所有版本都已作废"
        else:
            code, detail = "Q_PROMO_NO_VERSION", "没有生效版本"
        quarantine.append({
            "code": code, "stage": "quality", "source": "promo_calendar.csv",
            "line_no": items[0].row_no, "field": "状态",
            "raw_value": "、".join(sorted({v.status for v in items})),
            "detail": f"{promo_id}：{detail}",
        })

    for promo_id, days_list in gap_days.items():
        if not days_list:
            continue
        quarantine.append({
            "code": "Q_PROMO_GAP", "stage": "quality", "source": "promo_calendar.csv",
            "line_no": next(v.row_no for v in by_promo[promo_id]),
            "field": "开始日期", "raw_value": f"{len(days_list)} 天",
            "detail": f"{promo_id} 空档 {days_list[0]} 起 {len(days_list)} 天",
        })

    for promo_id, count in overlap_days.items():
        if count <= 0:
            continue
        quarantine.append({
            "code": "Q_PROMO_OVERLAP", "stage": "quality", "source": "promo_calendar.csv",
            "line_no": next(v.row_no for v in by_promo[promo_id]),
            "field": "开始日期", "raw_value": f"{count} 天",
            "detail": f"{promo_id} 两版重叠 {count} 天，按版本号大者生效",
        })

    pending_promos: list[str] = []
    blocked_promos: list[str] = []
    for promo_id, items in by_promo.items():
        if promo_id in candidates:
            continue
        if any(v.status == config.PROMO_STATUS_ACTIVE for v in items):
            blocked_promos.append(promo_id)
        else:
            pending_promos.append(promo_id)

    windows.sort(key=lambda w: (w.start, w.promo_id, w.version))
    return {
        "pending_promos": sorted(pending_promos),
        "blocked_promos": sorted(blocked_promos),
        "versions": versions,
        "window_objs": windows,
        "quarantine": quarantine,
        "overlap_days": overlap_days,
        "gap_days": {k: len(v) for k, v in gap_days.items()},
        "active_promos": sorted(candidates),
        "promo_versions": {k: len(v) for k, v in by_promo.items()},
        "inactive_promos": sorted(p for p in by_promo if p not in candidates),
    }


def format_range(item: Version) -> str:
    start = item.start.isoformat() if item.start else "—"
    end = item.end.isoformat() if item.end else "—"
    return f"{start}~{end}"


def windows_to_rows(windows) -> list[dict]:
    rows = []
    for w in windows:
        rows.append({
            "promo_id": w.promo_id, "sku": w.sku, "channel": w.channel,
            "start_date": w.start.isoformat(), "end_date": w.end.isoformat(),
            "version": w.version, "price_incl": str(w.price_incl),
            "price_excl": str(w.price_excl), "days": w.days,
            "base_price_excl": "0",
        })
    return rows
