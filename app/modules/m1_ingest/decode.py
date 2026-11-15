"""编码探测与回退链：声明只作首个候选，严格解码失败即回退下一档。

朴素实现按声明直接 `decode()`：GB18030 文件被声明成 utf-8 时整批崩；
或直接 `errors="replace"`，中文字段变成问号，后面所有匹配全错。
"""
from __future__ import annotations

import codecs
from dataclasses import dataclass, field

from ... import config


@dataclass
class DecodeResult:
    text: str
    encoding: str
    declared: str
    bom_stripped: bool
    used_fallback: bool
    tried: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def normalize_charset(value: str | None) -> str:
    if not value:
        return ""
    key = str(value).strip().strip('"').strip("'").lower()
    return config.CHARSET_ALIASES.get(key, key)


def candidates(declared: str | None) -> list[str]:
    out: list[str] = []
    head = normalize_charset(declared)
    if head:
        out.append(head)
    for enc in config.ENCODING_CHAIN:
        if enc == "utf-8-sig":
            # BOM 在 decode 前已单独剥离，这里只需普通 utf-8
            enc = "utf-8"
        if enc not in out:
            out.append(enc)
    return out


def decode(raw: bytes, declared: str | None = None) -> DecodeResult:
    bom_stripped = raw.startswith(codecs.BOM_UTF8)
    data = raw[len(codecs.BOM_UTF8):] if bom_stripped else raw
    tried: list[str] = []
    used_fallback = False
    for index, enc in enumerate(candidates(declared)):
        try:
            text = data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            tried.append(enc)
            continue
        used_fallback = index > 0
        notes = []
        if bom_stripped:
            notes.append("E_BOM_STRIPPED")
        if used_fallback:
            notes.append("E_DECODE_FALLBACK")
        return DecodeResult(text=text, encoding=enc, declared=declared or "",
                            bom_stripped=bom_stripped, used_fallback=used_fallback,
                            tried=tried, notes=notes)
    from ...errors import Unprocessable
    raise Unprocessable(
        f"回退链内所有编码都无法解码（试过 {', '.join(tried)}）",
        code="E_DECODE_FALLBACK",
        detail={"tried": tried, "declared": declared or ""},
    )
