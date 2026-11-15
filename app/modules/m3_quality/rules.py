"""质量规则：重复日、缺日、覆盖不足、促销商品缺失、窗口越界。

朴素实现把「没有记录的那一天」直接跳过：促销 20 天里有 1 天没数据，
日均被放大 5%，增量跟着虚高，而页面上看不出少了哪一天。
本模块把缺日单独计数并逐日给出日期，天数账三格相加恒等于促销天数。
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from decimal import Decimal

from ... import config


def key_of(sku: str, channel: str, day) -> tuple[str, str, str]:
    return sku, channel, day.isoformat()


def split_duplicates(rows) -> tuple[list, list[dict]]:
    """同一商品同一渠道同一天多条：保留行号最小的一条，其余隔离。"""
    seen: dict[tuple[str, str, str], object] = {}
    kept: list = []
    quarantine: list[dict] = []
    for row in sorted(rows, key=lambda r: (r.sale_date, r.sku, r.channel, r.line_no)):
        key = key_of(row.sku, row.channel, row.sale_date)
        if key not in seen:
            seen[key] = row
            kept.append(row)
            continue
        first = seen[key]
        quarantine.append({
            "code": "Q_DUP_DAY", "stage": "quality", "source": "daily_sales",
            "line_no": row.line_no, "field": "日期",
            "raw_value": f"{row.sale_date.isoformat()} qty={row.qty}",
            "detail": f"与行号 {first.line_no} 重复（qty={first.qty}），保留首见行",
        })
    kept.sort(key=lambda r: (r.sale_date, r.channel, r.sku))
    return kept, quarantine


def window_dates(window) -> list[dt.date]:
    out: list[dt.date] = []
    cursor = window.start
    while cursor < window.end:
        out.append(cursor)
        cursor += dt.timedelta(days=1)
    return out


def promo_dates(windows) -> dict[str, set[dt.date]]:
    out: dict[str, set[dt.date]] = defaultdict(set)
    for window in windows:
        out[window.promo_id].update(window_dates(window))
    return out


def assess_promo(windows, rows, dropped) -> list[dict]:
    """按促销逐日对账：天数 = 有效 + 缺日 + 剔除。"""
    dates_by_promo = promo_dates(windows)
    valid: dict[str, set[dt.date]] = defaultdict(set)
    skus: dict[str, tuple[str, str]] = {}
    for window in windows:
        skus.setdefault(window.promo_id, (window.sku, window.channel))
    for row in rows:
        key = (row.sku, row.channel)
        for promo_id, pair in skus.items():
            if pair == key and row.sale_date in dates_by_promo[promo_id]:
                valid[promo_id].add(row.sale_date)
    dropped_map: dict[str, set[dt.date]] = defaultdict(set)
    for item in dropped:
        pair = (item["sku"], item["channel"])
        for promo_id, owner in skus.items():
            if pair == owner and item["sale_date"] in dates_by_promo[promo_id]:
                dropped_map[promo_id].add(item["sale_date"])

    out = []
    for promo_id, dates in dates_by_promo.items():
        valid_days = valid.get(promo_id, set()) & dates
        dropped_days = (dropped_map.get(promo_id, set()) - valid_days) & dates
        missing_days = dates - valid_days - dropped_days
        out.append({
            "promo_id": promo_id,
            "sku": skus[promo_id][0],
            "channel": skus[promo_id][1],
            "days": len(dates),
            "days_valid": len(valid_days),
            "days_dropped": len(dropped_days),
            "days_missing": len(missing_days),
            "missing_dates": sorted(d.isoformat() for d in missing_days),
            "coverage": (Decimal(len(valid_days)) / Decimal(len(dates))).quantize(
                Decimal("0.0001")) if dates else Decimal("0"),
        })
    out.sort(key=lambda r: r["promo_id"])
    return out


def missing_day_quarantine(accounts, skus) -> list[dict]:
    rows = []
    for account in accounts:
        for day in account["missing_dates"]:
            rows.append({
                "code": "Q_MISSING_DAY", "stage": "quality", "source": "daily_sales",
                "line_no": None, "field": "日期", "raw_value": day,
                "detail": f"{account['promo_id']} {account['sku']}/{account['channel']} 缺日",
            })
    return rows


def low_coverage_quarantine(accounts) -> list[dict]:
    rows = []
    for account in accounts:
        if account["coverage"] >= config.MIN_COVERAGE_RATIO:
            continue
        rows.append({
            "code": "Q_LOW_COVERAGE", "stage": "quality", "source": "promo_calendar.csv",
            "line_no": None, "field": "日期",
            "raw_value": f"{account['days_valid']}/{account['days']}",
            "detail": f"{account['promo_id']} 有效天数占比 {account['coverage']:.2%}",
        })
    return rows


def unknown_sku_quarantine(windows, known_pairs) -> list[dict]:
    rows = []
    seen = set()
    for window in windows:
        pair = (window.sku, window.channel)
        if pair in known_pairs or pair in seen:
            continue
        seen.add(pair)
        rows.append({
            "code": "Q_PROMO_UNKNOWN_SKU", "stage": "quality",
            "source": "promo_calendar.csv", "line_no": None, "field": "商品编码",
            "raw_value": f"{window.sku}/{window.channel}",
            "detail": f"{window.promo_id} 在日销文件中没有该商品",
        })
    return rows


def out_of_window(rows, control, bounds) -> list[dict]:
    if not bounds:
        return []
    low, high = bounds
    out = []
    for row in list(rows) + list(control):
        if low <= row.sale_date <= high:
            continue
        source = "control_group.csv" if row in control else "daily_sales"
        out.append({
            "code": "Q_OUT_OF_WINDOW", "stage": "quality", "source": source,
            "line_no": row.line_no, "field": "日期",
            "raw_value": row.sale_date.isoformat(),
            "detail": f"建模窗口 {low.isoformat()} ~ {high.isoformat()}",
        })
    return out


def window_bounds(rows) -> tuple[dt.date, dt.date] | None:
    if not rows:
        return None
    dates = [row.sale_date for row in rows]
    return min(dates), max(dates)
