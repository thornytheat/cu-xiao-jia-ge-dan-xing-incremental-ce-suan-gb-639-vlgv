"""表头识别与列映射：中英文别名、必需列校验、分隔符探查。

朴素实现按固定中文列名取列：门店系统导出的英文表头直接 KeyError；
列顺序一换，价格和销量就串了。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ... import config


@dataclass
class HeaderInfo:
    raw: list[str]
    mapping: dict[int, str] = field(default_factory=dict)
    canonical: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)
    delimiter: str = ","

    @property
    def ok(self) -> bool:
        return not self.missing


SEPARATORS = (",", "\t", ";", "|")


def sniff_delimiter(text: str) -> str:
    first = text.split("\n", 1)[0]
    best, best_count = ",", 0
    for sep in SEPARATORS:
        count = first.count(sep)
        if count > best_count:
            best, best_count = sep, count
    return best


def clean_header(name: str) -> str:
    return name.replace("\ufeff", "").strip().strip('"').strip()


def resolve(name: str) -> str | None:
    key = clean_header(name)
    if not key:
        return None
    return config.COLUMN_ALIASES.get(key.lower(), config.COLUMN_ALIASES.get(key))


def build_header(raw: list[str], delimiter: str = ",",
                 required: tuple[str, ...] = config.REQUIRED_COLUMNS) -> HeaderInfo:
    info = HeaderInfo(raw=[clean_header(x) for x in raw], delimiter=delimiter)
    seen: set[str] = set()
    for index, name in enumerate(info.raw):
        canonical = resolve(name)
        if canonical is None:
            if name:
                info.ignored.append(name)
            continue
        if canonical in seen:
            # 同名列重复：只认第一列，其余忽略（多来源拼接常见）
            info.ignored.append(name)
            continue
        seen.add(canonical)
        info.mapping[index] = canonical
        info.canonical.append(canonical)
    info.missing = [c for c in required if c not in seen]
    return info


def row_to_dict(fields: list[str], header: HeaderInfo) -> dict[str, str]:
    out: dict[str, str] = {}
    for index, canonical in header.mapping.items():
        out[canonical] = fields[index] if index < len(fields) else ""
    return out


def header_signature(fields: list[str]) -> tuple[str, ...]:
    return tuple(clean_header(x).lower() for x in fields)
