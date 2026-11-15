"""采信编排：定位三份输入 → 逐文件诊断 → 给出可用的文件与空态结论。

空态三态分开处理，各自返回不同结论码（0 字节 / 仅表头 / 有表头但无有效行），
不能都回一句「成功导入 0 行」。
"""
from __future__ import annotations

from ... import config
from ...errors import Unprocessable
from . import reader

STATE_OK = "ok"
STATE_EMPTY_FILE = "empty_file"
STATE_HEADER_ONLY = "header_only"
STATE_NO_ROWS = "no_rows"
STATE_LABELS = {
    STATE_OK: "有数据",
    STATE_EMPTY_FILE: "0 字节",
    STATE_HEADER_ONLY: "仅表头",
    STATE_NO_ROWS: "无有效行",
}
STATE_CODES = {
    STATE_OK: None,
    STATE_EMPTY_FILE: "E_EMPTY_FILE",
    STATE_HEADER_ONLY: "E_HEADER_ONLY",
    STATE_NO_ROWS: "E_COLUMN_COUNT",
}


def _state_of(parsed: reader.ParsedFile) -> str:
    if parsed.empty_file:
        return STATE_EMPTY_FILE
    if parsed.header_only:
        return STATE_HEADER_ONLY
    if not parsed.records:
        return STATE_NO_ROWS
    # 有行但每一行列数都不对：等于没有一行能入模，结论必须与「有数据」区分
    if all(record.code for record in parsed.records):
        return STATE_NO_ROWS
    return STATE_OK


def load(settings: config.Settings, dataset: str) -> dict:
    files = reader.read_dataset(settings, dataset)

    for role, parsed in files.items():
        if parsed.truncated:
            raise Unprocessable(
                f"{parsed.name} 末行不完整，已拒绝整批导入",
                code="E_TRUNCATED_TAIL",
                detail={"file": parsed.name, "role": role,
                        "line_no": parsed.truncated_line,
                        "hint": "重新导出完整文件后再导入"},
            )
        if parsed.header is None and not (parsed.empty_file or parsed.header_only):
            raise Unprocessable(
                f"{parsed.name} 表头缺少必需列",
                code="E_HEADER_UNKNOWN",
                detail={"file": parsed.name, "note": parsed.note},
            )
        if parsed.header is not None and not parsed.header.ok:
            raise Unprocessable(
                f"{parsed.name} 表头缺少必需列：{'、'.join(parsed.header.missing)}",
                code="E_HEADER_UNKNOWN",
                detail={"file": parsed.name, "missing": parsed.header.missing},
            )

    sales = files["sales"]
    state = _state_of(sales)
    codes = []
    for parsed in files.values():
        codes.extend(parsed.codes)
    return {
        "dataset": dataset,
        "files": files,
        "sources": [files[r].summary() for r in ("sales", "promo", "control")],
        "state": state,
        "state_label": STATE_LABELS[state],
        "state_code": STATE_CODES[state],
        "codes": list(dict.fromkeys(codes)),
        "rows": {role: parsed.rows for role, parsed in files.items()},
        "dup_header": {role: parsed.dup_header for role, parsed in files.items()},
    }


def describe(settings: config.Settings) -> list[dict]:
    out = []
    for key, spec in config.DATASETS.items():
        path = settings.sample_path(spec["sales"])
        out.append({
            "key": key,
            "label": spec["label"],
            "sales": spec["sales"],
            "exists": path.is_file(),
            "bytes": path.stat().st_size if path.is_file() else 0,
        })
    return out
