"""M7 入口：设计 API 的成功与失败输入、页面渲染、导航可达。"""
from __future__ import annotations

PAGE = "/design"


def test_design_api_returns_plan(client_run):
    response = client_run.get("/api/design")
    assert response.status_code == 200
    body = response.json()
    plan = body["plan"]
    assert plan["sku"] == "MY-0012"
    assert plan["channel"] == "电商"
    assert plan["min_days"] == 30
    assert plan["budget_days"] == 42
    assert len(plan["impute_rows"]) == 3
    assert [row["mode"] for row in plan["impute_rows"]] == ["drop", "zero", "mean"]
    assert plan["control_candidates"]
    assert plan["quarantine"]
    assert body["options"][0]["sku"]
    assert body["batch_id"] == plan["batch_id"]


def test_design_api_accepts_explicit_params(client_run):
    response = client_run.post("/api/design", json={
        "sku": "MY-0021", "channel": "商超", "target_lift": "8%",
        "alpha": "0.05", "power": 0.9, "budget_days": 42, "impute": "mean",
    })
    assert response.status_code == 200
    plan = response.json()
    assert (plan["sku"], plan["channel"]) == ("MY-0021", "商超")
    assert plan["target_lift"] == "0.0800"
    assert plan["target_lift_pct"] == "8.0%"
    assert plan["impute"] == "mean"
    assert plan["impute_label"] == "缺日按均值"
    # 这条序列残差有轻微异方差，稳健方差把所需天数抬了 2 天
    assert plan["min_days_naive"] < plan["min_days"]
    assert float(plan["variance_factor"]) > 1.0


def test_design_api_rejects_bad_target(client_run):
    for payload, code in (
        ({"target_lift": "abc"}, "D_BAD_TARGET"),
        ({"target_lift": "0"}, "D_BAD_TARGET"),
        ({"target_lift": "-0.1"}, "D_BAD_TARGET"),
        ({"power": "2"}, "D_BAD_PARAM"),
        ({"alpha": "0.6"}, "D_BAD_PARAM"),
        ({"budget_days": "0"}, "D_BAD_PARAM"),
        ({"budget_days": "500"}, "D_BAD_PARAM"),
        ({"impute": "线性"}, "D_BAD_PARAM"),
        ({"sku": "MY-9999", "channel": "电商"}, "D_UNKNOWN_SERIES"),
    ):
        response = client_run.post("/api/design", json=payload)
        assert response.status_code == 422, payload
        body = response.json()
        assert body["error"] == code, payload
        assert body["level"] == "error"
        assert body["hint"]
    assert client_run.get("/api/design").status_code == 200


def test_design_api_without_batch_is_422(client):
    empty = client.get("/api/design").json()
    assert empty["batch_id"] is None
    assert empty["plan"] is None
    response = client.post("/api/design", json={})
    assert response.status_code == 422
    assert response.json()["error"] == "D_NO_BATCH"
    assert client.get("/api/batches").json()["count"] == 0


def test_design_page_renders_all_sections(client_run):
    response = client_run.get(PAGE)
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    text = response.text
    for title in ("设计输入", "本期结论", "实验窗与波动", "缺日插补", "控制组候选",
                  "设计校验"):
        assert title in text, title
    assert "预算够 · 至少 30 天" in text
    assert "MY-0019" in text
    assert "缺日按 0" in text
    assert "D_IMPUTE_SENSITIVE" in text
    # 导航里能点回来，且当前页标记为当前项
    assert 'href="/design"' in text
    assert 'aria-current="page"' in text


def test_design_page_keeps_params_and_reports_error_inline(client_run):
    ok = client_run.get("/design?sku=MY-0021&channel=商超&target_lift=0.08&impute=mean")
    assert ok.status_code == 200
    assert "MY-0021" in ok.text
    assert "缺日按均值" in ok.text
    bad = client_run.get("/design?target_lift=zzz")
    assert bad.status_code == 200
    assert "D_BAD_TARGET" in bad.text
    # 参数不合法时仍保留用户已填的输入，不整页清空
    assert 'value="zzz"' in bad.text
    assert "设计输入" in bad.text
    assert "本期结论" in bad.text
