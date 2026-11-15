"""运行清单导出：JSON 与单行键值 CSV，字段可复算（run_id + 输入 sha256 + 参数）。"""
from __future__ import annotations

import csv
import io
import json
from decimal import Decimal

BOM = "\ufeff"


def json_safe(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return value


def to_json(doc: dict) -> str:
    return json.dumps(json_safe(doc), ensure_ascii=False, indent=2)


def to_csv(doc: dict) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(("字段", "值"))
    for key in ("run_id", "batch_id", "batch_key", "dataset", "rule_version",
                "zero_mode", "input_digest", "started_at", "finished_at",
                "elapsed_ms", "timezone", "decimals"):
        writer.writerow((key, doc.get(key, "")))
    for item in doc.get("inputs", []):
        writer.writerow((f"input:{item['name']}",
                         f"{item['sha256']} ({item['bytes']} B, {item['encoding']})"))
    for key, value in (doc.get("counters") or {}).items():
        writer.writerow((f"counter:{key}", value))
    for stage in doc.get("stages", []):
        writer.writerow((f"stage:{stage['stage']}",
                         f"进 {stage['in_count']} / 出 {stage['out_count']} / "
                         f"隔离 {stage['quarantine_count']} / {stage['elapsed_ms']} ms"))
    return BOM + buf.getvalue()
