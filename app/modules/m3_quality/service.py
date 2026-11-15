"""质量编排：去重 → 缺日 → 覆盖判定 → 窗口越界 → 原因码补全与分布。"""
from __future__ import annotations

from ... import config
from . import metrics, quarantine, rules


def drop_days_of(dup_rows, source_rows) -> list[dict]:
    """被去重掉的那一天计入「剔除」，不当缺日。"""
    by_line = {row.line_no: row for row in source_rows}
    out: list[dict] = []
    for item in dup_rows:
        row = by_line.get(item.get("line_no"))
        if row is None:
            continue
        out.append({"sale_date": row.sale_date, "sku": row.sku,
                    "channel": row.channel, "code": item["code"],
                    "line_no": row.line_no})
    return out


def assess(normalized: dict) -> dict:
    rows = normalized["sales"]
    control = normalized["control"]
    windows = normalized["windows"]

    deduped, dup_q = rules.split_duplicates(rows)
    dropped = list(normalized.get("dropped", [])) + drop_days_of(dup_q, rows)

    accounts = rules.assess_promo(windows, deduped, dropped)
    known_pairs = {(row.sku, row.channel) for row in deduped}
    extra_q: list[dict] = []
    extra_q.extend(rules.missing_day_quarantine(accounts, known_pairs))
    extra_q.extend(rules.low_coverage_quarantine(accounts))
    extra_q.extend(rules.unknown_sku_quarantine(windows, known_pairs))
    extra_q.extend(rules.out_of_window(deduped, control, rules.window_bounds(deduped)))

    all_rows = quarantine.enrich(normalized["quarantine"] + dup_q + extra_q)
    return {
        "sales": deduped,
        "control": control,
        "windows": windows,
        "versions": normalized["versions"],
        "base_prices": normalized["base_prices"],
        "base_prices_excl": normalized["base_prices_excl"],
        "control_base_prices": normalized["control_base_prices"],
        "control_base_prices_excl": normalized["control_base_prices_excl"],
        "quarantine": all_rows,
        "distribution": quarantine.distribution(all_rows),
        "level_counts": quarantine.level_counts(all_rows),
        "accounts": accounts,
        "score": metrics.score(accounts, all_rows,
                               normalized.get("records_total", 0)),
        "overlap_days": normalized["overlap_days"],
        "gap_days": normalized["gap_days"],
        "active_promos": normalized["active_promos"],
        "inactive_promos": normalized["inactive_promos"],
        "pending_promos": normalized["pending_promos"],
        "blocked_promos": normalized["blocked_promos"],
        "promo_versions": normalized["promo_versions"],
        "dropped": dropped,
        "grade_rows": metrics.grade_rows(accounts),
    }


def measured_accounts(accounts) -> list[dict]:
    """覆盖率达到门槛的促销才进入增量测算。"""
    return [a for a in accounts if a["coverage"] >= config.MIN_COVERAGE_RATIO]
