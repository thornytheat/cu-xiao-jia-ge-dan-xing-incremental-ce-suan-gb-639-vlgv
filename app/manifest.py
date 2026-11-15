"""运行清单：run_id、输入摘要 sha256、规则版本、参数、耗时，可复算。

run_id = 前缀 + 时间戳 + 短随机；batch_key = 输入全量摘要，同 key 重跑返回同一批次。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import uuid

from . import config


def new_run_id(prefix: str = "PL") -> str:
    stamp = dt.datetime.now(config.UTC).strftime("%Y%m%d%H%M%S")
    return f"{prefix}-{stamp}-{uuid.uuid4().hex[:4]}"


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest_parts(parts: list[str]) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def build(*, run_id: str, batch_id: str, batch_key: str, settings, dataset: str,
          inputs: list[dict], params: dict, started_at: str, finished_at: str,
          elapsed_ms: int, stages: list[dict], counters: dict, totals: dict) -> dict:
    return {
        "run_id": run_id,
        "batch_id": batch_id,
        "batch_key": batch_key,
        "dataset": dataset,
        "rule_version": settings.rule_version,
        "zero_mode": settings.zero_mode,
        "decimals": "金额 Decimal 2 位；销量整数；单价 Decimal 4 位",
        "money_unit": config.MONEY_UNIT,
        "qty_unit": config.QTY_UNIT,
        "price_unit": config.PRICE_UNIT_BASE,
        "input_digest": digest_parts([i["sha256"] for i in inputs]),
        "inputs": [
            {"name": i["name"], "bytes": i["bytes"], "sha256": i["sha256"],
             "encoding": i["encoding"], "bom_stripped": i["bom_stripped"]}
            for i in inputs
        ],
        "params": params,
        "started_at": started_at,
        "finished_at": finished_at,
        "elapsed_ms": elapsed_ms,
        "stages": stages,
        "counters": counters,
        "totals": totals,
        "replayable": "同 batch_key 重跑复用已有结果，不新增行",
    }
