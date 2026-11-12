"""A股指数资金进出：解析夹具、窗口合计、ETF 申赎估算、两融按日历、缺失=无数据、接口与渲染不联网。"""
import json
import os
import socket
from datetime import date, timedelta
from pathlib import Path

import pytest

import cn_index_flow as F

FIX = Path(__file__).resolve().parent / "fixtures"
TODAY = date(2026, 11, 12)


def _load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def test_parse_fflow_fixture_units_and_order():
    rows = F.parse_fflow(_load("cn_flow_fflow_csi300.json"))
    assert [r["date"] for r in rows] == [date(2026, 11, 9), date(2026, 11, 10), date(2026, 11, 11)]
    last = rows[-1]
    assert last["main"] == pytest.approx(11012780032.0)
    # 主力 = 超大单 + 大单
    assert last["super"] + last["large"] == pytest.approx(last["main"], rel=1e-6)
    assert last["main_pct"] == pytest.approx(2.49)


def test_parse_fflow_bad_payloads():
    assert F.parse_fflow(None) == []
    assert F.parse_fflow({"data": None}) == []
    assert F.parse_fflow({"data": {"klines": ["garbage", "2026-11-11,-,1,2,3,4,5"]}}) == []


def test_main_flow_summary_windows_need_full_length():
    rows = F.parse_fflow(_load("cn_flow_fflow_csi300.json"))
    s = F.main_flow_summary(rows, TODAY)
    assert s["status"] == "ok" and s["asof"] == "2026-11-11" and s["unit"] == "亿元"
    assert s["net_1d"] == pytest.approx(110.13, abs=0.01)
    assert s["net_5d"] is None and s["net_20d"] is None  # 只有 3 天：不拿短窗口冒充


def test_main_flow_summary_sums_and_stale():
    rows = [{"date": TODAY - timedelta(days=40 - i), "main": 1e8 * (1 if i % 2 else -2), "super": None,
             "large": None, "main_pct": None} for i in range(25)]
    s = F.main_flow_summary(rows, TODAY)
    assert s["status"] == "missing" and "过期" in s["reason"]
    rows = [dict(r, date=r["date"] + timedelta(days=14)) for r in rows]
    s = F.main_flow_summary(rows, TODAY)
    assert s["status"] == "ok"
    assert s["net_5d"] == pytest.approx(sum(r["main"] for r in rows[-5:]) / 1e8)
    assert s["net_20d"] == pytest.approx(sum(r["main"] for r in rows[-20:]) / 1e8)


def test_parse_sse_etf_scale_units():
    out = F.parse_sse_etf_scale(_load("cn_flow_sse_etf_scale.json"))
    assert set(out) == {"510300", "510050"}  # "-" 行丢弃
    d, shares = out["510300"]
    assert d == date(2026, 11, 11)
    assert shares == pytest.approx(4610478.77 * 10000)


def test_etf_flow_estimate_and_missing_base():
    cal = [date(2026, 11, 9), date(2026, 11, 10), date(2026, 11, 11)]
    prices = F.parse_sina_kline(_load("cn_flow_sina_kline.json"))
    shares = {
        date(2026, 11, 11): {"510300": (date(2026, 11, 11), 46.0e8)},
        date(2026, 11, 10): {"510300": (date(2026, 11, 10), 47.0e8)},
    }
    it = F.etf_flow("510300", "x", shares, cal, prices)
    assert it["status"] == "ok" and it["asof"] == "2026-11-11"
    assert it["shares_chg_1d"] == pytest.approx(-1.0)
    assert it["net_1d"] == pytest.approx(-1.0 * 4.150, abs=0.01)  # 1 亿份 × 区间均价 4.15
    assert it["net_5d"] is None and it["net_20d"] is None
    miss = F.etf_flow("510050", "y", shares, cal, prices)
    assert miss["status"] == "missing"


def test_margin_summary_calendar_lookup_no_neighbor_substitution():
    cal = [date(2026, 10, 1) + timedelta(days=i) for i in range(30)]
    sparse = [(cal[-1], 100.0), (cal[-2], 99.0), (cal[-6], 90.0)]  # T-20 缺
    s = F.margin_summary(sparse, cal[-1] + timedelta(days=1), "margin_sz", cal)
    assert s["status"] == "ok" and s["balance"] == 100.0
    assert s["chg_1d"] == 1.0 and s["chg_5d"] == 10.0 and s["chg_20d"] is None
    assert F.margin_summary([], TODAY, "margin_sh")["status"] == "missing"


def _raw_fixture():
    rows = F.parse_fflow(_load("cn_flow_fflow_csi300.json"))
    cal = [r["date"] for r in rows]
    return {
        "main:csi300": rows,
        "calendar": cal,
        "etf_shares": {cal[-1]: {"510300": (cal[-1], 46.0e8)}, cal[-2]: {"510300": (cal[-2], 45.0e8)}},
        "etf_prices": {"510300": F.parse_sina_kline(_load("cn_flow_sina_kline.json"))},
        "margin_sh": [(d, 11700.0 + i) for i, d in enumerate(cal)],
        "margin_sz": [(d, 10800.0 + i) for i, d in enumerate(cal)],
    }


def test_build_bundle_missing_is_no_data_and_display_only():
    b = F.build_flow_bundle(_raw_fixture(), {"main:sse50": "SourceRateLimited: x"}, TODAY)
    assert b["in_score"] is False and b["unit"] == "亿元"
    assert b["indices"]["csi300"]["main"]["status"] == "ok"
    assert b["indices"]["csi300"]["main"]["source_url"].startswith("https://")
    assert b["indices"]["sse50"]["main"]["status"] == "missing"
    assert "SourceRateLimited" in b["indices"]["sse50"]["main"]["reason"]
    etf = b["indices"]["csi300"]["etf"]
    assert etf["status"] == "ok" and etf["coverage"] == "1/3 只有数据"
    assert etf["net_1d"] == pytest.approx(4.15, abs=0.01)
    assert b["indices"]["chinext"]["etf"]["status"] == "missing"
    mk = b["market"]
    assert mk["northbound"]["status"] == "missing"
    assert mk["main"]["status"] == "missing"
    assert mk["margin_total"]["status"] == "ok" and mk["margin_total"]["balance"] == pytest.approx(22504.0)
    assert mk["margin_total"]["chg_1d"] == pytest.approx(2.0)


def test_provider_with_injected_fetchers_caches_and_records_errors():
    rows = F.parse_fflow(_load("cn_flow_fflow_csi300.json"))

    def boom():
        raise RuntimeError("down")

    p = F.IndexFlowProvider(fetchers={"main_all": lambda: {"main:csi300": rows, "errors": {"main:market": "x"}},
                                      "margin_sh": boom})
    assert p.get_cached() is None
    b = p.refresh()
    assert b["indices"]["csi300"]["main"]["status"] == "ok"
    assert "margin_sh" in b["errors"] and b["market"]["margin_sh"]["status"] == "missing"
    assert p.get_cached() is b and p.refresh() is b


def test_pick_flow_bundle_prefers_fresh():
    old = {"fetched_at": "2026-11-01T09:00:00+08:00"}
    new = {"fetched_at": "2026-11-11T09:00:00+08:00"}
    assert F.pick_flow_bundle(old, None, new) is new
    assert F.pick_flow_bundle(None, {}) is None


def test_cn_indices_endpoint_attaches_flows_without_network(monkeypatch):
    os.environ["SKIP_BG_WARMUP"] = "1"
    import app as app_mod

    def deny(*a, **k):
        raise OSError("network disabled")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("CN_INDEX_FLOW_AUTO", raising=False)
    bundle = F.build_flow_bundle(_raw_fixture(), {}, TODAY)
    monkeypatch.setattr(F.DEFAULT_FLOW_PROVIDER, "_cache", (__import__("time").time(), bundle))
    r = app_mod.app.test_client().get("/api/cn-indices")
    assert r.status_code == 200
    data = r.get_json()
    assert data.get("index_flows") is not None
    assert "csi300" in data["index_flows"]["indices"]
    holders = data["index_flows"]["indices"]["csi300"].get("holders") or {}
    assert holders.get("status") == "ok" and holders.get("report_date") == "2026-06-30"
    assert data["index_flows"]["indices"]["csi300"]["etf"]["status"] == "ok"
    # 资金流不进评分：建议对象里没有资金流字段
    for row in data.get("indices") or []:
        assert "flow" not in json.dumps(row.get("advice") or {}, ensure_ascii=False).lower()
