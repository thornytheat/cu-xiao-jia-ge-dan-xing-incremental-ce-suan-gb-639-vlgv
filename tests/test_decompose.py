"""M5 同品类促销重叠：自身增量/挤占/残差三分、三层对账与整版切换。

覆盖需求硬约束：
- 各商品保留按自身价格变化可解释的增量（own），不被改数；
- 重叠客流只在同品类同渠道重叠日识别，赢家/输家按观测缺口匹配为挤占，
  品类净额恒为 0；匹配不掉的净缺口留品类残差，绝不用固定比例摊回商品；
- 商品、渠道、品类三层对账全平才整版切换；失败保留旧版；
- 同一 batch_key 重试复用批次，不新增版本。
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app import config
from app.modules.m5_lift import (cutover, decompose, reconcile,
                                 service as lift_service)
from fixtures import dataset as fx

OVERLAP_SKUS = ("MY-0012", "MY-0013")


@pytest.fixture()
def overlap_settings(tmp_path):
    # MY-0013 与 MY-0012 同属酸奶品类、同渠道，促销窗完全重叠
    config.CONTROL_MAP["MY-0013"] = "MY-0019"
    config.CATEGORY_MAP["MY-0013"] = "CAT-YOGURT"
    sales: list[list] = []
    control: list[list] = []
    for day in range(26, 31):
        d = f"2026-09-{day}"
        sales += [fx.sale(d, "电商", "MY-0012", "12.80", 100),
                  fx.sale(d, "电商", "MY-0013", "11.00", 80)]
        control.append(fx.control(d, "MY-0019", "电商", "13.40", 90))
    for day in range(1, 10):
        d = f"2026-10-{day:02d}"
        sales += [fx.sale(d, "电商", "MY-0012", "12.80", 100),
                  fx.sale(d, "电商", "MY-0013", "11.00", 80)]
        control.append(fx.control(d, "MY-0019", "电商", "13.40", 90))
    # 促销期：A 降价放量（赢家），B 降价但销量低于自身弹性预期（输家，被挤占）
    for day in range(10, 15):
        d = f"2026-10-{day:02d}"
        sales += [fx.sale(d, "电商", "MY-0012", "9.90", 150, promo="P1"),
                  fx.sale(d, "电商", "MY-0013", "8.90", 60, promo="P2")]
        control.append(fx.control(d, "MY-0019", "电商", "13.40", 90))
    promos = [
        fx.promo("P1", "MY-0012", "电商", 1, "2026-10-10", "2026-10-15", "9.90"),
        fx.promo("P2", "MY-0013", "电商", 1, "2026-10-10", "2026-10-15", "8.90"),
    ]
    settings = fx.write_dataset(
        tmp_path / "overlap", sales_rows=sales, promo_rows=promos,
        control_rows=control)
    return settings


def _run(settings):
    from app.store import Store
    store = Store(settings.db_path)
    result = lift_service.run(settings=settings, store=store, dataset="main")
    return store, result


def test_three_way_identity_per_promo(overlap_settings):
    store, result = _run(overlap_settings)
    batch_id = result["batch"]["batch_id"]
    for row in store.lift_decomp(batch_id):
        composed_qty = (Decimal(row["own_qty"]) + Decimal(row["cannibal_qty"])
                        + Decimal(row["residual_qty"]))
        assert composed_qty == Decimal(row["lift_qty"])
        composed_gmv = (Decimal(row["own_gmv"]) + Decimal(row["cannibal_gmv"])
                        + Decimal(row["residual_gmv"]))
        assert composed_gmv == Decimal(row["lift_gmv"])


def test_own_lift_is_kept_not_rewritten(overlap_settings):
    # 自身价格解释量必须保留：赢家 own 为正且来自弹性反事实，不被重叠调减
    store, result = _run(overlap_settings)
    rows = {r["promo_id"]: r for r in store.lift_decomp(result["batch"]["batch_id"])}
    assert Decimal(rows["P1"]["own_qty"]) > 0
    assert Decimal(rows["P2"]["own_qty"]) < 0
    # 两个促销都识别出 5 个重叠日
    assert all(int(r["overlap_days"]) == 5 for r in rows.values())


def test_cannibalization_matches_observed_gap_and_nets_zero(overlap_settings):
    store, result = _run(overlap_settings)
    batch_id = result["batch"]["batch_id"]
    rows = {r["promo_id"]: r for r in store.lift_decomp(batch_id)}
    # 赢家承接挤占为正，输家被挤占为负，销量额两两相等（按观测缺口匹配，非固定比例）
    assert Decimal(rows["P1"]["cannibal_qty"]) > 0
    assert Decimal(rows["P2"]["cannibal_qty"]) < 0
    assert (Decimal(rows["P1"]["cannibal_qty"])
            + Decimal(rows["P2"]["cannibal_qty"])) == Decimal("0.0000")
    # 品类层挤占金额净额必须为 0
    category = store.lift_category(batch_id)[0]
    assert Decimal(category["cannibal_gmv"]) == Decimal("0.00")


def test_residual_never_allocated_back_by_fixed_ratio(overlap_settings):
    # 输家偏差被全部匹配时其残差为 0；赢家未匹配上的超额留残差，
    # 品类残差只在品类层挂账，不存在按比例摊回输家的动作
    store, result = _run(overlap_settings)
    batch_id = result["batch"]["batch_id"]
    rows = {r["promo_id"]: r for r in store.lift_decomp(batch_id)}
    assert Decimal(rows["P2"]["residual_qty"]) == Decimal("0.0000")
    assert Decimal(rows["P1"]["residual_qty"]) >= 0
    overlap = store.lift_overlap(batch_id)
    assert len(overlap) == 5
    for row in overlap:
        assert set(row["promo_ids"]) == {"P1", "P2"}
        assert Decimal(row["matched_qty"]) > 0


def test_category_counts_common_growth_once(overlap_settings):
    store, result = _run(overlap_settings)
    batch_id = result["batch"]["batch_id"]
    category = store.lift_category(batch_id)[0]
    # 品类共同增长 = Σ自身 + Σ残差；重复计入额恒为 0（挤占是内部转移）
    assert Decimal(category["category_lift_gmv"]) == (
        Decimal(category["own_gmv"]) + Decimal(category["residual_gmv"]))
    assert Decimal(category["double_count_gmv"]) == Decimal("0.00")
    assert Decimal(category["lift_gmv"]) == Decimal(category["category_lift_gmv"])


def test_three_layer_reconcile_all_balances_and_publishes(overlap_settings):
    store, result = _run(overlap_settings)
    batch_id = result["batch"]["batch_id"]
    checks = store.reconcile_checks(batch_id)
    layers = {row["layer"] for row in checks}
    assert layers == {"商品层", "渠道层", "品类层"}
    assert all(row["ok"] for row in checks)
    published = store.get_published(batch_id)
    assert published["status"] == config.PUBLISH_ACTIVE
    assert store.active_published("main")["batch_id"] == batch_id


def test_channel_components_reassemble_to_measured_lift(overlap_settings):
    store, result = _run(overlap_settings)
    batch_id = result["batch"]["batch_id"]
    grouped: dict[tuple, Decimal] = {}
    measured: dict[tuple, Decimal] = {}
    for row in store.lift_channel_component(batch_id):
        key = (row["promo_id"], row["channel"])
        grouped[key] = grouped.get(key, Decimal("0")) + Decimal(row["lift_gmv"])
        measured[key] = Decimal(row["measured_gmv"])
    assert grouped == measured


def test_failed_switch_keeps_old_version(overlap_settings, monkeypatch):
    store, first = _run(overlap_settings)
    old_id = first["batch"]["batch_id"]
    assert store.active_published("main")["batch_id"] == old_id

    # 强制三层对账失败：新批次不得激活，旧版保持 active
    real_run_all = reconcile.run_all

    def boom(decomp_, lift_rows_, channel_components_):
        report = real_run_all(decomp_, lift_rows_, channel_components_)
        report["ok"] = False
        report["failed"] = [report["checks"][0]]
        return report
    monkeypatch.setattr(reconcile, "run_all", boom)
    # 换一个 0 销量处理方式以产生不同 batch_key 的新批次
    failed = lift_service.run(settings=overlap_settings, store=store,
                              dataset="main", zero_mode="drop")
    failed_id = failed["batch"]["batch_id"]
    assert failed_id != old_id
    assert store.get_published(failed_id)["status"] == config.PUBLISH_FAILED
    # 旧版仍是生效版
    assert store.active_published("main")["batch_id"] == old_id
    # 失败原因进了隔离区
    codes = {q["code"] for q in store.quarantine(failed_id)}
    assert "A_RECONCILE_FAILED" in codes


def test_retry_same_batch_key_adds_no_version(overlap_settings):
    store, first = _run(overlap_settings)
    again = lift_service.run(settings=overlap_settings, store=store, dataset="main")
    assert again["replay"] is True
    assert again["batch"]["batch_id"] == first["batch"]["batch_id"]
    assert len(store.list_batches()) == 1
    assert len([p for p in store.list_published()
                if p["status"] == config.PUBLISH_ACTIVE]) == 1


def test_failed_batch_keeps_failed_record_on_replay(overlap_settings, monkeypatch):
    # 失败批次同键重跑走幂等复用：不新增批次、不动发布记录，旧版始终生效
    store, first = _run(overlap_settings)
    old_id = first["batch"]["batch_id"]
    real_run_all = reconcile.run_all

    def boom(decomp_, lift_rows_, channel_components_):
        report = real_run_all(decomp_, lift_rows_, channel_components_)
        report["ok"] = False
        report["failed"] = [report["checks"][0]]
        return report
    monkeypatch.setattr(reconcile, "run_all", boom)
    failed = lift_service.run(settings=overlap_settings, store=store,
                              dataset="main", zero_mode="drop")
    drop_id = failed["batch"]["batch_id"]
    assert store.get_published(drop_id)["status"] == config.PUBLISH_FAILED
    assert store.active_published("main")["batch_id"] == old_id

    # 同键重跑：幂等复用，批次与发布记录数量不变，旧版仍是 active
    replay = lift_service.run(settings=overlap_settings, store=store,
                              dataset="main", zero_mode="drop")
    assert replay["replay"] is True
    assert replay["batch"]["batch_id"] == drop_id
    assert len(store.list_batches()) == 2
    assert store.get_published(drop_id)["status"] == config.PUBLISH_FAILED
    assert store.active_published("main")["batch_id"] == old_id
    # 修复后换键（shift 之外的新输入）才会产生新版本并接替——这里验证 active 指针机制
    monkeypatch.undo()
    fixed = lift_service.run(settings=overlap_settings, store=store, dataset="main")
    assert fixed["batch"]["batch_id"] == old_id  # shift 键本来就成功，直接复用
    assert store.active_published("main")["batch_id"] == old_id


def test_no_overlap_promo_has_zero_cannibalization(settings, store, batch_id):
    # 真实样例两个促销不同品类不同渠道，不存在重叠日：挤占必须为 0
    for row in store.lift_decomp(batch_id):
        assert int(row["overlap_days"]) == 0
        assert Decimal(row["cannibal_gmv"]) == Decimal("0.00")
        assert Decimal(row["cannibal_qty"]) == Decimal("0.0000")
    assert store.lift_overlap(batch_id) == []
    # 三分仍恒等，残差承接全部未解释偏差
    for row in store.lift_decomp(batch_id):
        assert (Decimal(row["own_gmv"]) + Decimal(row["residual_gmv"])
                == Decimal(row["lift_gmv"]))
    # 既有合计口径不变
    totals = store.totals(batch_id)
    assert Decimal(totals["lift_gmv"]) == Decimal(fx.EXPECTED_LIFT_GMV)
    assert Decimal(totals["category_lift_gmv"]) == Decimal(totals["lift_gmv"])
    assert Decimal(totals["double_count_gmv"]) == Decimal("0.00")
    # 照样整版切换成功
    assert store.get_published(batch_id)["status"] == config.PUBLISH_ACTIVE
