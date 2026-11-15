"""流式读取：保留原始物理行号、识别重复表头、列数校验、截断识别。

朴素实现用 `splitlines()` 切行：引号内含换行的一条记录被切成两条，列错位；
末行半行被当成一条有效数据，凭空多出一行销量。
"""
from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass, field
from pathlib import Path

from ... import config
from ...errors import Unprocessable
from . import decode, sniff


@dataclass
class Record:
    index: int
    line_no: int
    data: dict[str, str]
    raw_fields: list[str]
    extra: int = 0
    short: int = 0

    @property
    def code(self) -> str | None:
        if self.extra or self.short:
            return "E_COLUMN_COUNT"
        return None


@dataclass
class ParsedFile:
    name: str
    role: str
    encoding: str = ""
    declared: str = ""
    bom_stripped: bool = False
    used_fallback: bool = False
    bytes: int = 0
    sha256: str = ""
    delimiter: str = ","
    header: sniff.HeaderInfo | None = None
    records: list[Record] = field(default_factory=list)
    dup_header: int = 0
    empty_file: bool = False
    header_only: bool = False
    truncated: bool = False
    truncated_line: int | None = None
    codes: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def rows(self) -> int:
        return len(self.records)

    def summary(self) -> dict:
        return {
            "name": self.name,
            "role": self.role,
            "encoding": self.encoding,
            "declared": self.declared,
            "bom_stripped": self.bom_stripped,
            "bytes": self.bytes,
            "sha256": self.sha256,
            "records": len(self.records) + self.dup_header,
            "rows": self.rows,
            "code": self.codes[0] if self.codes else None,
            "note": self.note,
        }


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def read_file(path: Path, role: str, declared: str | None = None,
              required: tuple[str, ...] | None = None) -> ParsedFile:
    path = Path(path)
    if not path.is_file():
        raise Unprocessable(f"样例文件不存在：{path.name}", code="E_FILE_MISSING",
                            detail={"path": str(path)})
    raw = path.read_bytes()
    parsed = ParsedFile(name=path.name, role=role, bytes=len(raw),
                        sha256=hashlib.sha256(raw).hexdigest())
    if not raw:
        parsed.empty_file = True
        parsed.codes.append("E_EMPTY_FILE")
        parsed.note = "0 字节"
        return parsed

    decoded = decode.decode(raw, declared)
    parsed.encoding = decoded.encoding
    parsed.declared = decoded.declared
    parsed.bom_stripped = decoded.bom_stripped
    parsed.used_fallback = decoded.used_fallback
    parsed.codes.extend(decoded.notes)

    text = _normalize_newlines(decoded.text)
    parsed.delimiter = sniff.sniff_delimiter(text)

    reader = csv.reader(io.StringIO(text), delimiter=parsed.delimiter)
    try:
        header_fields = next(reader)
    except StopIteration:
        parsed.header_only = True
        parsed.codes.append("E_HEADER_ONLY")
        parsed.note = "只有表头"
        return parsed

    header = sniff.build_header(header_fields, parsed.delimiter,
                                required or config.REQUIRED_COLUMNS)
    parsed.header = header
    if not header.ok:
        parsed.codes.append("E_HEADER_UNKNOWN")
        parsed.note = "缺列：" + "、".join(header.missing)
        return parsed

    signature = sniff.header_signature(header_fields)
    width = len(header_fields)
    last_line = 0
    index = 0
    prev_blank = False
    for fields in reader:
        line_no = reader.line_num
        if not fields or all(not str(x).strip() for x in fields):
            prev_blank = True
            continue
        if sniff.header_signature(fields) == signature:
            parsed.dup_header += 1
            continue
        index += 1
        extra = max(0, len(fields) - width)
        short = max(0, width - len(fields))
        parsed.records.append(Record(
            index=index, line_no=line_no,
            data=sniff.row_to_dict(fields, header),
            raw_fields=list(fields), extra=extra, short=short))
        last_line = line_no
        prev_blank = False

    if parsed.dup_header:
        parsed.codes.append("E_DUP_HEADER")
    if not parsed.records:
        parsed.header_only = True
        parsed.codes.append("E_HEADER_ONLY")
        parsed.note = "只有表头"
        return parsed

    unterminated = text.count('"') % 2 == 1
    tail_short = bool(parsed.records[-1].short or parsed.records[-1].extra)
    if (unterminated or (tail_short and not text.endswith("\n"))) and not prev_blank:
        parsed.truncated = True
        parsed.truncated_line = last_line
        parsed.codes.append("E_TRUNCATED_TAIL")
        parsed.note = f"末行不完整，行号 {last_line}"
    parsed.note = parsed.note or f"记录 {len(parsed.records)} 条，表头 {width} 列"
    return parsed


def read_dataset(settings: config.Settings, dataset: str) -> dict[str, ParsedFile]:
    spec = config.DATASETS.get(dataset)
    if not spec:
        raise Unprocessable(f"未知样例集：{dataset}", code="E_BAD_REQUEST",
                            detail={"datasets": sorted(config.DATASETS)})
    out: dict[str, ParsedFile] = {}
    required = {"sales": config.REQUIRED_COLUMNS,
                "promo": config.PROMO_REQUIRED_COLUMNS,
                "control": config.REQUIRED_COLUMNS}
    for role in ("sales", "promo", "control"):
        out[role] = read_file(settings.sample_path(spec[role]), role,
                              required=required[role])
    return out
