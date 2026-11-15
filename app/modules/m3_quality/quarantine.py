"""隔离区：原因码补全、分布统计、CSV 导出（原值 + 行号 + 原因码）。

隔离行不能只打一条日志：人工复核要看到「哪一行、原值是什么、按哪条规则拦下的」。
"""
from __future__ import annotations

import csv
import io

from ... import reasons

CSV_HEADER = ("原因码", "级别", "阶段", "来源", "行号", "字段", "原值", "说明")


def enrich(rows) -> list[dict]:
    out = []
    for row in rows:
        code = row["code"]
        reason = reasons.get(code)
        out.append({
            "code": code,
            "level": reason.level,
            "level_label": reasons.LEVEL_LABELS[reason.level],
            "stage": row.get("stage") or reason.stage,
            "title": reason.title,
            "hint": reason.hint,
            "source": row.get("source") or "",
            "line_no": row.get("line_no"),
            "field": row.get("field") or "",
            "raw_value": ("" if row.get("raw_value") is None
                          else str(row.get("raw_value")))[:180],
            "detail": row.get("detail") or "",
        })
    out.sort(key=lambda r: (reasons.LEVEL_ORDER[r["level"]], r["stage"], r["code"],
                            r["line_no"] if r["line_no"] is not None else 10 ** 9))
    return out


def distribution(rows) -> list[dict]:
    buckets: dict[str, dict] = {}
    for row in rows:
        item = buckets.get(row["code"])
        if item is None:
            buckets[row["code"]] = {
                "code": row["code"], "level": row["level"],
                "level_label": row["level_label"], "stage": row["stage"],
                "title": row["title"], "hint": row["hint"], "count": 1,
            }
        else:
            item["count"] += 1
    out = list(buckets.values())
    out.sort(key=lambda r: (reasons.LEVEL_ORDER[r["level"]], -r["count"], r["code"]))
    return out


def level_counts(rows) -> dict:
    counts = {reasons.ERROR: 0, reasons.WARN: 0, reasons.INFO: 0}
    for row in rows:
        counts[row["level"]] = counts.get(row["level"], 0) + 1
    return counts


def to_csv(rows) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(CSV_HEADER)
    for row in rows:
        writer.writerow([row["code"], row["level_label"], row["stage"],
                         row["source"], row["line_no"] if row["line_no"] is not None else "",
                         row["field"], row["raw_value"], row["detail"]])
    return buf.getvalue()


def filter_rows(rows, *, level: str | None = None, stage: str | None = None,
                code: str | None = None) -> list[dict]:
    out = rows
    if level:
        out = [r for r in out if r["level"] == level]
    if stage:
        out = [r for r in out if r["stage"] == stage]
    if code:
        out = [r for r in out if r["code"] == code]
    return out
