"""质量指标：有效天数占比、隔离密度、等级结论。

评分只用可复算的三个数：有效天数、应覆盖天数、严重级隔离条数。
等级不参与任何金额计算，只用于页面上的严重度结论。
"""
from __future__ import annotations

from decimal import Decimal


def coverage(accounts) -> Decimal:
    expected = sum(a["days"] for a in accounts)
    valid = sum(a["days_valid"] for a in accounts)
    if expected <= 0:
        return Decimal("0")
    return (Decimal(valid) / Decimal(expected)).quantize(Decimal("0.0001"))


DATA_STAGES = ("ingest", "price", "quality")


def score(accounts, rows, records_total: int = 0) -> dict:
    expected = sum(a["days"] for a in accounts)
    valid = sum(a["days_valid"] for a in accounts)
    missing = sum(a["days_missing"] for a in accounts)
    dropped = sum(a["days_dropped"] for a in accounts)
    errors = sum(1 for r in rows if r["level"] == "error")
    warns = sum(1 for r in rows if r["level"] == "warn")
    data_errors = sum(1 for r in rows
                      if r["level"] == "error" and r["stage"] in DATA_STAGES)
    bad_rate = (Decimal(data_errors) / Decimal(records_total)
                if records_total else Decimal("0"))
    ratio = coverage(accounts)
    if ratio >= Decimal("0.98") and bad_rate <= Decimal("0.005"):
        grade, label = "A", "可用"
    elif ratio >= Decimal("0.90") and bad_rate <= Decimal("0.06"):
        grade, label = "B", "需复核"
    else:
        grade, label = "C", "先隔离再复核"
    return {
        "expected_days": expected,
        "valid_days": valid,
        "missing_days": missing,
        "dropped_days": dropped,
        "coverage": str(ratio),
        "error_count": errors,
        "warn_count": warns,
        "grade": grade,
        "grade_label": label,
        "data_errors": data_errors,
        "bad_rate": str(bad_rate.quantize(Decimal("0.0001"))),
        "records_total": records_total,
        "balanced": valid + missing + dropped == expected,
    }


def grade_rows(accounts) -> list[dict]:
    out = []
    for account in accounts:
        out.append({
            "promo_id": account["promo_id"],
            "sku": account["sku"],
            "channel": account["channel"],
            "days": account["days"],
            "days_valid": account["days_valid"],
            "days_missing": account["days_missing"],
            "days_dropped": account["days_dropped"],
            "coverage": str(account["coverage"]),
            "level": "error" if account["coverage"] < Decimal("0.70") else (
                "warn" if account["coverage"] < Decimal("0.95") else "info"),
        })
    return out
