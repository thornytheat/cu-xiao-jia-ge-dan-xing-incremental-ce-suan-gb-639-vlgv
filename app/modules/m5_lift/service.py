"""增量编排与批次：采信 → 归价 → 质量 → 弹性 → 增量 → 报告，一次事务落库。

批次键 = 样例集 + 0 销量处理方式 + 规则版本 + 三份输入 sha256。
同键重跑直接复用已完成批次的结果，不新增行——这就是幂等的保证点。
"""
from __future__ import annotations

import datetime as dt
import time
import uuid
from collections import defaultdict
from decimal import Decimal

from ... import config, manifest, reasons
from ...store import Store
from ..m1_ingest import service as ingest_service
from ..m2_price import service as price_service
from ..m3_quality import quarantine as quarantine_mod
from ..m3_quality import service as quality_service
from ..m4_elastic import service as elastic_service
from . import baseline, ci, spread

REPLAY_NOTE = "同键批次已存在，复用结果"


def now_iso() -> str:
    return dt.datetime.now(config.UTC).isoformat(timespec="seconds")


def _group_windows(windows) -> dict[str, list]:
    out: dict[str, list] = defaultdict(list)
    for window in windows:
        out[window.promo_id].append(window)
    return out


def promo_dates_of(windows) -> set:
    dates = set()
    for window in windows:
        cursor = window.start
        while cursor < window.end:
            dates.add(cursor)
            cursor += dt.timedelta(days=1)
    return dates


def compute_lift(assessed, elastic_rows) -> dict:
    windows_by_promo = _group_windows(assessed["windows"])
    accounts = {a["promo_id"]: a for a in assessed["accounts"]}
    base_excl = assessed["base_prices_excl"]
    rows: list[dict] = []
    spread_rows: list[dict] = []
    quarantine: list[dict] = []
    measured: list[str] = []

    for promo_id in sorted(windows_by_promo):
        windows = windows_by_promo[promo_id]
        account = accounts.get(promo_id)
        if account is None:
            quarantine.append({
                "code": "Q_PROMO_UNKNOWN_SKU", "stage": "lift", "source": "promo_calendar.csv",
                "line_no": None, "field": "商品编码",
                "raw_value": f"{windows[0].sku}/{windows[0].channel}",
                "detail": f"{promo_id} 没有对应的日销样本",
            })
            continue
        if account["coverage"] < config.MIN_COVERAGE_RATIO:
            continue
        sku, channel = account["sku"], account["channel"]
        slope_row = next((r for r in elastic_rows
                          if r["scope"] == "sku" and r["group_key"] == f"{sku}/{channel}"),
                         None)
        if slope_row is None:
            quarantine.append({
                "code": "L_SAMPLE_TOO_SMALL", "stage": "lift", "source": "daily_sales",
                "line_no": None, "field": "销量",
                "raw_value": f"{sku}/{channel}",
                "detail": f"{promo_id} 没有可用的弹性斜率",
            })
            continue
        dates = promo_dates_of(windows)
        fit = baseline.compute(windows, dates, assessed["sales"], assessed["control"],
                               base_excl)
        if isinstance(fit, str):
            quarantine.append({
                "code": fit, "stage": "lift", "source": "daily_sales",
                "line_no": None, "field": "销量",
                "raw_value": f"{sku}/{channel}",
                "detail": f"{promo_id} 基线不可用",
            })
            continue
        actual_qty, actual_gmv = baseline.actual_totals(
            assessed["sales"], dates, sku, channel)
        dlnp = ci.log_ratio(fit.price_excl, fit.pre_price_excl)
        lift_qty = (actual_qty - fit.base_qty).quantize(Decimal("0.0001"))
        lift_gmv = (actual_gmv - fit.base_gmv).quantize(config.MONEY_QUANT)
        band = ci.interval(lift_gmv=lift_gmv, base_qty=fit.base_qty,
                           base_price=fit.pre_price_excl, dlnp=dlnp,
                           slope=float(slope_row["slope"]),
                           se_slope=float(slope_row["se"]))
        start = min(w.start for w in windows)
        end = max(w.end for w in windows)
        rows.append({
            "promo_id": promo_id, "sku": sku, "channel": channel,
            "start_date": start.isoformat(), "end_date": end.isoformat(),
            "days": account["days"], "days_valid": account["days_valid"],
            "days_missing": account["days_missing"], "days_dropped": account["days_dropped"],
            "actual_qty": str(actual_qty), "base_qty": str(fit.base_qty),
            "actual_gmv": str(actual_gmv), "base_gmv": str(fit.base_gmv),
            "lift_qty": str(lift_qty), "lift_gmv": str(lift_gmv),
            "se_gmv": band["se_gmv"], "ci_low": band["ci_low"], "ci_high": band["ci_high"],
            "price_excl": str(fit.price_excl),
            "base_price_excl": str(fit.pre_price_excl),
            "control_sku": config.CONTROL_MAP.get(sku, ""),
            "control_factor": str(fit.control_factor),
            "dlnp": f"{dlnp:.6f}" if dlnp is not None else "0",
            "slope": str(slope_row["slope"]),
            "note": "区间包含 0" if Decimal(band["ci_low"]) <= 0 <= Decimal(band["ci_high"])
            else "区间不含 0",
        })
        measured.append(promo_id)
        channel_gmv = baseline.channel_totals(assessed["sales"], dates, sku)
        shares = spread.allocate(lift_gmv, channel_gmv)
        for share in shares:
            spread_rows.append({
                "promo_id": promo_id, "channel": share["channel"],
                "ratio": share["ratio"], "lift_gmv": share["lift_gmv"],
                "actual_gmv": share["actual_gmv"],
            })
        if spread.remainder_of(lift_gmv, shares) != 0:
            quarantine.append({
                "code": "A_SPREAD_REMAINDER", "stage": "lift",
                "source": "daily_sales", "line_no": None, "field": "增量",
                "raw_value": str(spread.remainder_of(lift_gmv, shares)),
                "detail": f"{promo_id} 分摊尾差已按最大余额法补齐",
            })
    return {"rows": rows, "spread": spread_rows, "quarantine": quarantine,
            "measured": measured}


def _stage(stage: str, in_count: int, out_count: int, quarantine_count: int,
           elapsed: float, status: str = "done", note: str = "") -> dict:
    return {"stage": stage, "in_count": in_count, "out_count": out_count,
            "quarantine_count": quarantine_count, "status": status,
            "elapsed_ms": int(elapsed * 1000), "note": note}


def _count_stage(rows, stage: str) -> int:
    return sum(1 for r in rows if r["stage"] == stage)


def run(settings: config.Settings, store: Store, dataset: str = "main",
        zero_mode: str | None = None) -> dict:
    mode = zero_mode or settings.zero_mode
    if mode not in config.ZERO_MODES:
        mode = config.ZERO_MODE_SHIFT
    total_start = time.perf_counter()
    started_at = now_iso()

    loaded = ingest_service.load(settings, dataset)
    digests = [source["sha256"] for source in loaded["sources"]]
    batch_key = manifest.digest_parts(
        [dataset, mode, settings.rule_version] + digests)

    existing = store.find_done_by_key(batch_key)
    if existing is not None:
        return {
            "batch": existing,
            "replay": True,
            "note": REPLAY_NOTE,
            "batch_key": batch_key,
            "counters": store.totals(existing["batch_id"]),
        }

    timings: list[tuple[str, int, int, int, float]] = []
    mark = time.perf_counter()
    promo_records = loaded["files"]["promo"].rows
    control_records = loaded["files"]["control"].rows
    sales_records = loaded["files"]["sales"].rows
    timings.append(("ingest", sales_records + promo_records + control_records,
                    sales_records, _count_stage([], "ingest"),
                    time.perf_counter() - mark))

    mark = time.perf_counter()
    normalized = price_service.normalize(loaded)
    price_q = _count_stage(normalized["quarantine"], "price")
    timings.append(("price", sales_records, len(normalized["sales"]), price_q,
                    time.perf_counter() - mark))

    mark = time.perf_counter()
    assessed = quality_service.assess(normalized)
    quality_q = _count_stage(assessed["quarantine"], "quality")
    timings.append(("quality", len(assessed["sales"]), len(assessed["sales"]), quality_q,
                    time.perf_counter() - mark))

    mark = time.perf_counter()
    elastic = elastic_service.run(assessed["sales"], mode)
    elastic_q = len(elastic["quarantine"])
    timings.append(("elastic", len(assessed["sales"]), len(elastic["rows"]),
                    elastic_q, time.perf_counter() - mark))

    mark = time.perf_counter()
    lift = compute_lift(assessed, elastic["rows"])
    lift_q = len(lift["quarantine"])
    timings.append(("lift", len(assessed["accounts"]), len(lift["rows"]),
                    lift_q, time.perf_counter() - mark))

    extra = quarantine_mod.enrich(lift["quarantine"] + elastic["quarantine"])
    all_quarantine = assessed["quarantine"] + extra
    counts = quarantine_mod.level_counts(all_quarantine)

    mark = time.perf_counter()
    totals = build_totals(loaded, assessed, lift, all_quarantine, counts)
    timings.append(("report", len(lift["rows"]), len(all_quarantine), 0,
                    time.perf_counter() - mark))

    elapsed_ms = int((time.perf_counter() - total_start) * 1000)
    finished_at = now_iso()
    run_id = manifest.new_run_id()
    batch_id = f"B{dt.datetime.now(config.UTC).strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}"
    stages = [_stage(*item) for item in timings]
    manifest_doc = manifest.build(
        run_id=run_id, batch_id=batch_id, batch_key=batch_key, settings=settings,
        dataset=dataset, inputs=loaded["sources"], params={"zero_mode": mode},
        started_at=started_at, finished_at=finished_at, elapsed_ms=elapsed_ms,
        stages=stages, counters=totals, totals=totals,
    )

    payload = {
        "sources": loaded["sources"],
        "sales": [
            {"line_no": row.line_no, "sale_date": row.sale_date.isoformat(),
             "channel": row.channel, "sku": row.sku, "sku_name": row.sku_name,
             "price_incl": str(row.price_incl), "price_unit": row.price_unit,
             "tax_rate": str(row.tax_rate), "price_excl": str(row.price_excl),
             "qty": row.qty, "promo_id": row.promo_id,
             "amount_excl": str(row.amount_excl), "flags": row.flags}
            for row in assessed["sales"]
        ],
        "promos": [
            {"promo_id": version.promo_id, "sku": version.sku,
             "channel": version.channel, "version": version.version,
             "start_date": version.start.isoformat() if version.start else "",
             "end_date": version.end.isoformat() if version.end else "",
             "activity_price": str(version.price_incl) if version.price_incl else "",
             "status": version.status, "effective": 1 if version.effective else 0,
             "overlap": 1 if version.overlap else 0, "note": version.note,
             "row_no": version.row_no}
            for version in assessed["versions"]
        ],
        "promo_windows": price_service.window_rows(
            assessed["windows"], assessed["base_prices_excl"]),
        "quarantine": [
            {"code": row["code"], "stage": row["stage"], "level": row["level"],
             "source": row["source"], "line_no": row["line_no"], "field": row["field"],
             "raw_value": row["raw_value"], "detail": row["detail"]}
            for row in all_quarantine
        ],
        "elasticity": elastic["rows"],
        "lift": lift["rows"],
        "spread": lift["spread"],
        "stages": stages,
        "totals": totals,
        "manifest": manifest_doc,
    }
    store.upsert_batch({
        "batch_id": batch_id, "batch_key": batch_key, "run_id": run_id,
        "dataset": dataset, "status": "done", "created_at": started_at,
        "finished_at": finished_at, "input_digest": manifest_doc["input_digest"],
        "params": {"zero_mode": mode}, "note": loaded["state_label"],
    })
    store.write_batch(batch_id, payload)
    return {
        "batch": store.get_batch(batch_id),
        "replay": False,
        "note": "新批次",
        "batch_key": batch_key,
        "counters": totals,
        "state": loaded["state"],
        "state_label": loaded["state_label"],
    }


def build_totals(loaded, assessed, lift, all_quarantine, counts) -> dict:
    total_records = loaded["rows"]["sales"]
    rows_ok = len(assessed["sales"])
    promos_total = len(assessed["promo_versions"])
    measured = len(lift["rows"])
    pending = len(assessed["pending_promos"])
    blocked = promos_total - measured - pending
    score = assessed["score"]
    actual_gmv = sum((Decimal(r["actual_gmv"]) for r in lift["rows"]), Decimal("0"))
    base_gmv = sum((Decimal(r["base_gmv"]) for r in lift["rows"]), Decimal("0"))
    lift_gmv = sum((Decimal(r["lift_gmv"]) for r in lift["rows"]), Decimal("0"))
    return {
        "records_total": total_records,
        "rows_ok": rows_ok,
        "rows_bad": total_records - rows_ok,
        "dup_header": loaded["dup_header"]["sales"],
        "promos_total": promos_total,
        "promos_measured": measured,
        "promos_blocked": blocked,
        "promos_pending": pending,
        "days_expected": score["expected_days"],
        "days_valid": score["valid_days"],
        "days_missing": score["missing_days"],
        "days_dropped": score["dropped_days"],
        "actual_gmv": str(actual_gmv),
        "base_gmv": str(base_gmv),
        "lift_gmv": str(lift_gmv),
        "actual_qty": str(sum((Decimal(r["actual_qty"]) for r in lift["rows"]),
                              Decimal("0"))),
        "base_qty": str(sum((Decimal(r["base_qty"]) for r in lift["rows"]),
                            Decimal("0"))),
        "lift_qty": str(sum((Decimal(r["lift_qty"]) for r in lift["rows"]),
                            Decimal("0"))),
        "spread_total": str(sum((Decimal(r["lift_gmv"]) for r in lift["spread"]),
                                Decimal("0"))),
        "quarantine_total": len(all_quarantine),
        "quarantine_error": counts[reasons.ERROR],
        "quarantine_warn": counts[reasons.WARN],
        "quarantine_info": counts[reasons.INFO],
        "coverage": score["coverage"],
        "grade": score["grade"],
    }


def latest_or_none(store: Store) -> dict | None:
    return store.latest_batch()
