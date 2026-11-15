"""M1 采信：编码回退、BOM、引号内换行、重复表头、列数、截断、空态三态。"""
from __future__ import annotations

import pytest

from app.errors import Unprocessable
from app.modules.m1_ingest import decode, reader, service, sniff
from fixtures import dataset as fx


def test_gb18030_falls_back_and_keeps_chinese_header(read_sales):
    # TRAP-01：文件声明是 UTF-8，实际字节是 GB18030；朴素 decode 直接崩
    parsed = read_sales()
    assert parsed.encoding == fx.SALES_ENCODING
    assert parsed.used_fallback is True
    assert "E_DECODE_FALLBACK" in parsed.codes
    assert parsed.header.raw[0] == "日期"
    assert parsed.header.raw[4] == "含税单价"
    assert parsed.sha256 == fx.SALES_SHA256
    assert parsed.bytes == fx.SALES_BYTES


def test_bom_stripped_and_duplicate_header_counted(read_sales):
    # TRAP-02：BOM 留在首列名上，按列名取值全空
    # TRAP-04：分段导出会重复表头，朴素实现把表头当数据
    parsed = read_sales(fx.BOM_FILE)
    assert parsed.bom_stripped is True
    assert "E_BOM_STRIPPED" in parsed.codes
    assert parsed.header.raw[0] == "日期"
    assert parsed.dup_header == fx.BOM_DUP_HEADER
    assert parsed.rows == fx.BOM_RECORDS


def test_quoted_newline_keeps_one_record(read_sales, sample_dir):
    # TRAP-03：引号内含换行，splitlines 会把一条记录切成两条
    parsed = read_sales(fx.BOM_FILE)
    raw = (sample_dir / fx.BOM_FILE).read_text(encoding="utf-8-sig")
    naive_lines = len(raw.splitlines()) - 1 - fx.BOM_DUP_HEADER
    assert parsed.rows == fx.BOM_RECORDS
    assert naive_lines > parsed.rows
    joined = [record.data["商品名称"] for record in parsed.records]
    assert any("\n" in name for name in joined)


def test_column_count_mismatch_flagged(read_sales):
    # TRAP-05：末尾多一个分隔符，朴素实现静默补 None 导致错列
    parsed = read_sales()
    width = len(parsed.header.raw)
    bad = [record for record in parsed.records if record.code == "E_COLUMN_COUNT"]
    assert len(bad) == 1
    assert bad[0].extra == 1
    assert len(bad[0].raw_fields) == width + 1
    assert bad[0].data["销量"] == "123"


def test_truncated_tail_rejected_with_code(settings):
    # TRAP-16：末行半行，朴素实现当有效数据凭空多一行
    with pytest.raises(Unprocessable) as error:
        service.load(settings, "truncated")
    assert error.value.code == "E_TRUNCATED_TAIL"
    assert error.value.status_code == 422
    assert error.value.detail["line_no"] > 1


def test_empty_three_states_are_distinct(settings):
    # TRAP-18：0 字节 / 仅表头 / 有行但一行都不合法，三种结论必须分开
    empty = service.load(settings, "empty")
    header_only = service.load(settings, "header_only")
    main = service.load(settings, "main")
    assert empty["state"] == service.STATE_EMPTY_FILE
    assert header_only["state"] == service.STATE_HEADER_ONLY
    assert main["state"] == service.STATE_OK
    assert empty["state_code"] != header_only["state_code"]
    assert header_only["state_code"] != main["state_code"]


def test_no_rows_state_when_every_row_is_malformed(tmp_path):
    rows = [["2026-09-01", "电商", "MY-0012", "甲", "12.80", "元/件", "0.13", "9",
             "", "EXTRA"] for _ in range(3)]
    settings = fx.write_dataset(tmp_path, sales_rows=rows)
    loaded = service.load(settings, "main")
    assert loaded["state"] == service.STATE_NO_ROWS
    assert loaded["state_code"] == "E_COLUMN_COUNT"


def test_missing_required_column_rejected(tmp_path):
    header = ["日期", "渠道", "商品编码", "销量"]
    settings = fx.write_dataset(tmp_path, sales_rows=[["2026-09-01", "电商", "X", "1"]],
                                sales_header=header)
    with pytest.raises(Unprocessable) as error:
        service.load(settings, "main")
    assert error.value.code == "E_HEADER_UNKNOWN"
    assert "含税单价" in error.value.detail["missing"]


def test_naive_splitlines_breaks_record_count(sample_dir):
    # 反例：朴素实现按物理行数当记录数，比真实记录数多
    raw = (sample_dir / fx.BOM_FILE).read_bytes()
    text = decode.decode(raw).text.replace("\r\n", "\n")
    physical = [line for line in text.split("\n") if line.strip()]
    parsed = reader.read_file(sample_dir / fx.BOM_FILE, "sales")
    # 物理行 = 表头 1 + 重复表头 1 + 记录 15 + 被引号内换行拆出的 1
    assert len(physical) == fx.BOM_PHYSICAL_LINES
    assert parsed.rows + 3 == fx.BOM_PHYSICAL_LINES
    assert parsed.rows < len(physical)


def test_header_alias_maps_english_columns():
    info = sniff.build_header(["date", "channel", "sku", "price_incl", "qty"], ",")
    assert info.ok is True
    assert info.mapping[3] == "含税单价"
    assert info.mapping[4] == "销量"
