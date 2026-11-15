"""样例数据的事实常量与合成样例集构造工具。

事实常量来自 sample_data 目录本身（字节数 / sha256 / 记录数），用来断言流水线
确实读了这份文件；合成样例集用来构造边界输入，不替代真实样例。
"""
from __future__ import annotations

import csv
import io
import json
import pathlib

from app import config

FIXTURE_DIR = pathlib.Path(__file__).resolve().parent
FACTS = json.loads((FIXTURE_DIR / "expected.json").read_text(encoding="utf-8"))
CASES = json.loads((FIXTURE_DIR / "elasticity_cases.json").read_text(encoding="utf-8"))
SAMPLE = FACTS["sample_data"]
PIPELINE = FACTS["pipeline"]

SALES_FILE = "daily_sales_gb18030.csv"
SALES_BYTES = SAMPLE[SALES_FILE]["bytes"]
SALES_SHA256 = SAMPLE[SALES_FILE]["sha256"]
SALES_RECORDS = SAMPLE[SALES_FILE]["records"]
SALES_ENCODING = SAMPLE[SALES_FILE]["encoding"]

BOM_FILE = "daily_sales_utf8bom.csv"
BOM_BYTES = SAMPLE[BOM_FILE]["bytes"]
BOM_SHA256 = SAMPLE[BOM_FILE]["sha256"]
BOM_RECORDS = SAMPLE[BOM_FILE]["records"]
BOM_DUP_HEADER = SAMPLE[BOM_FILE]["dup_header"]
BOM_PHYSICAL_LINES = SAMPLE[BOM_FILE]["physical_lines"]

PROMO_FILE = "promo_calendar.csv"
PROMO_BYTES = SAMPLE[PROMO_FILE]["bytes"]
PROMO_SHA256 = SAMPLE[PROMO_FILE]["sha256"]
PROMO_VERSIONS = SAMPLE[PROMO_FILE]["versions"]
PROMO_IDS = SAMPLE[PROMO_FILE]["promo_ids"]

CONTROL_FILE = "control_group.csv"
CONTROL_SHA256 = SAMPLE[CONTROL_FILE]["sha256"]
TRUNCATED_FILE = "truncated_tail.csv"
TRUNCATED_SHA256 = SAMPLE[TRUNCATED_FILE]["sha256"]

EXPECTED_ROWS_OK = PIPELINE["rows_ok"]
EXPECTED_ROWS_BAD = PIPELINE["rows_bad"]
EXPECTED_PROMOS_TOTAL = PIPELINE["promos_total"]
EXPECTED_PROMOS_MEASURED = PIPELINE["promos_measured"]
EXPECTED_PROMOS_BLOCKED = PIPELINE["promos_blocked"]
EXPECTED_PROMOS_PENDING = PIPELINE["promos_pending"]
EXPECTED_DAYS = PIPELINE["days_expected"]
EXPECTED_DAYS_VALID = PIPELINE["days_valid"]
EXPECTED_DAYS_MISSING = PIPELINE["days_missing"]
EXPECTED_DAYS_DROPPED = PIPELINE["days_dropped"]
EXPECTED_LIFT_GMV = PIPELINE["lift_gmv"]
EXPECTED_GRADE = PIPELINE["grade"]
EXPECTED_COVERAGE = PIPELINE["coverage"]
EXPECTED_NO_PRICE_VARIATION = PIPELINE["no_price_variation_groups"]
MISSING_DAY = PIPELINE["missing_day"]
DUP_DAY = PIPELINE["dup_day"]

SALES_HEADER = ["日期", "渠道", "商品编码", "商品名称", "含税单价",
                "单价单位", "税率", "销量", "促销编号"]
PROMO_HEADER = ["促销编号", "商品编码", "渠道", "版本", "开始日期",
                "结束日期", "活动价", "状态", "备注"]
CONTROL_HEADER = ["日期", "商品编码", "渠道", "单价", "销量"]


def render_csv(header, rows, *, encoding="utf-8", bom=False, newline="\r\n") -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator=newline)
    writer.writerow(header)
    for row in rows:
        writer.writerow(row)
    text = buf.getvalue()
    if bom:
        text = "\ufeff" + text
    return text.encode(encoding)


def sale(date, channel, sku, price, qty, promo="", *, unit="元/件", tax="0.13",
         name="测试商品"):
    return [date, channel, sku, name, price, unit, tax, str(qty), promo]


def promo(promo_id, sku, channel, version, start, end, price, status="生效", note=""):
    return [promo_id, sku, channel, str(version), start, end, price, status, note]


def control(date, sku, channel, price, qty):
    return [date, sku, channel, price, str(qty)]


def write_dataset(directory, *, sales_rows, sales_header=None, promo_rows=(),
                  promo_header=None, control_rows=(), control_header=None,
                  encoding="utf-8", bom=False, trailing_newline=True) -> config.Settings:
    """在 directory 下写三份输入并返回指向它的 Settings。"""
    directory = pathlib.Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    plans = (
        ("daily_sales_gb18030.csv", sales_header or SALES_HEADER, sales_rows),
        ("promo_calendar.csv", promo_header or PROMO_HEADER, promo_rows),
        ("control_group.csv", control_header or CONTROL_HEADER, control_rows),
    )
    for name, header, rows in plans:
        payload = render_csv(header, rows, encoding=encoding, bom=bom)
        if not trailing_newline:
            payload = payload.rstrip(b"\r\n")
        (directory / name).write_bytes(payload)
    return config.Settings(db_path=str(directory / "mini.sqlite"),
                           sample_dir=str(directory),
                           export_dir=str(directory / "export"))


def flat_days(count, start_day=1, channel="电商", sku="MY-0012", price="12.80",
              qty=100, promo=""):
    rows = []
    for offset in range(count):
        day = start_day + offset
        rows.append(sale(f"2026-09-{day:02d}", channel, sku, price, qty, promo))
    return rows


def as_records(header, rows, start_line=2):
    """把若干行转成 reader.Record，供规则函数直接吃。"""
    from app.modules.m1_ingest import reader, sniff
    info = sniff.build_header(header, ",", required=())
    out = []
    for index, row in enumerate(rows):
        out.append(reader.Record(index=index + 1, line_no=start_line + index,
                                 data=dict(zip(header, row)),
                                 raw_fields=list(row)))
    return info, out
