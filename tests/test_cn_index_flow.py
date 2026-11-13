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


def test_provider_with_injected_fetchers_caches_and_records_errors(monkeypatch):
    # 隔离打包快照：这里只验证本轮错误记录（快照回退另有专门测试）
    monkeypatch.setattr(F, "load_bundled_flow_seed", lambda: None)
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

def _ok_main(net=110.13, asof="2026-11-11"):
    return {"status": "ok", "asof": asof, "net_1d": net, "net_5d": None, "net_20d": None, "unit": "亿元"}


def _miss_main(reason="东方财富 push2 断开连接（疑似限流），本轮停止请求"):
    return {"status": "missing", "reason": reason, "asof": None}


def test_pick_flow_bundle_prefers_usable_main_over_newer_missing():
    seed = {
        "fetched_at": "2026-11-12T06:00:00+08:00",
        "asof": "2026-11-11",
        "indices": {"csi300": {"name": "沪深300", "main": _ok_main(), "etf": {"status": "ok", "asof": "2026-11-11"}}},
        "market": {"main": _ok_main(198.47), "margin_sh": {"status": "ok"}},
    }
    cache = {
        "fetched_at": "2026-11-12T14:48:23+08:00",
        "asof": None,
        "indices": {"csi300": {"name": "沪深300", "main": _miss_main(), "etf": {"status": "ok", "asof": "2026-11-11"}}},
        "market": {"main": _miss_main(), "margin_sh": {"status": "ok"}},
    }
    assert F.pick_flow_bundle(cache, seed) is seed
    # 两者都有可用主力时，覆盖相当则取较新
    live = {
        "fetched_at": "2026-11-12T15:00:00+08:00",
        "asof": "2026-11-12",
        "indices": {"csi300": {"name": "沪深300", "main": _ok_main(50.0, "2026-11-12"), "etf": {"status": "ok"}}},
        "market": {"main": _ok_main(80.0, "2026-11-12"), "margin_sh": {"status": "ok"}},
    }
    assert F.pick_flow_bundle(live, seed) is live


def test_pick_flow_bundle_stale_main_does_not_beat_empty_when_unreasonably_old():
    stale = {
        "fetched_at": "2026-08-01T09:00:00+08:00",
        "asof": "2026-08-01",
        "indices": {"csi300": {"main": _ok_main(1.0, "2026-08-01")}},
        "market": {"main": _ok_main(1.0, "2026-08-01")},
    }
    empty = {
        "fetched_at": "2026-11-12T14:48:23+08:00",
        "asof": None,
        "indices": {"csi300": {"main": _miss_main()}},
        "market": {"main": _miss_main()},
    }
    now = __import__("datetime").datetime(2026, 11, 12, 14, 50, tzinfo=F.CST)
    # 主力 asof 已超过宽限窗口，退化为按覆盖/新旧挑选，空的新缓存可以胜出
    assert F.pick_flow_bundle(empty, stale, now=now) is empty


def test_merge_main_from_fallback_keeps_live_etf_and_flags_snapshot():
    raw = _raw_fixture()
    for k in [k for k in raw if str(k).startswith("main:")]:
        raw.pop(k)
    errs = {f"main:{m['id']}": "限流" for m in F.FLOW_INDICES}
    errs["main:market"] = "限流"
    live = F.build_flow_bundle(raw, errs, TODAY)
    assert live["indices"]["csi300"]["main"]["status"] == "missing"
    seed = F.build_flow_bundle(_raw_fixture(), {}, TODAY)
    merged = F.merge_main_from_fallback(live, seed, today=TODAY)
    m = merged["indices"]["csi300"]["main"]
    assert m["status"] == "ok" and m["from_snapshot"] is True
    assert m["net_1d"] == pytest.approx(110.13, abs=0.01)
    assert m["asof"] == "2026-11-11"
    assert merged["main_from_snapshot"] is True
    assert merged["main_snapshot_asof"] == "2026-11-11"
    assert merged["indices"]["csi300"]["etf"]["status"] == "ok"
    assert merged["market"]["northbound"]["status"] == "missing"
    assert "2024年8月" in merged["market"]["northbound"]["reason"]
    assert merged["indices"]["chinext"]["etf"]["status"] == "missing"
    assert "深交所" in (merged["indices"]["chinext"]["etf"].get("reason") or "")
    # 不改写种子对象
    assert "from_snapshot" not in seed["indices"]["csi300"]["main"]
    # 无可用回退时不编造数字
    blank = F.merge_main_from_fallback(live, {"indices": {}}, today=TODAY)
    assert blank["indices"]["csi300"]["main"]["status"] == "missing"
    assert blank["indices"]["csi300"]["main"].get("net_1d") is None


def test_refresh_failing_fflow_keeps_previous_main(monkeypatch):
    monkeypatch.setattr(F, "load_bundled_flow_seed", lambda: None)
    monkeypatch.setattr(F, "fetch_remote_flow_seed", lambda *a, **k: None)
    raw = _raw_fixture()
    raw["main:market"] = list(raw["main:csi300"])
    good = F.build_flow_bundle(raw, {}, TODAY)
    limited = {"errors": {f"main:{m['id']}": "东方财富 push2 断开连接（疑似限流），本轮停止请求"
                          for m in F.FLOW_INDICES}}
    limited["errors"]["main:market"] = "东方财富 push2 断开连接（疑似限流），本轮停止请求"
    p = F.IndexFlowProvider(fetchers={
        "main_all": lambda: limited,
        "cal": lambda: [date(2026, 11, 9), date(2026, 11, 10), date(2026, 11, 11)],
        "etf_prices": lambda: {"510300": F.parse_sina_kline(_load("cn_flow_sina_kline.json"))},
        "etf_shares": lambda: {
            date(2026, 11, 11): {"510300": (date(2026, 11, 11), 46.0e8)},
            date(2026, 11, 10): {"510300": (date(2026, 11, 10), 45.0e8)},
        },
        "margin_sh": lambda: [(date(2026, 11, 11), 11700.0)],
        "margin_sz": lambda: [(date(2026, 11, 11), 10800.0)],
    })
    p._cache = (__import__("time").time(), good)
    b = p.refresh(force=True)
    assert b["indices"]["csi300"]["main"]["status"] == "ok"
    assert b["indices"]["csi300"]["main"]["from_snapshot"] is True
    assert b["indices"]["csi300"]["main"]["net_1d"] == pytest.approx(110.13, abs=0.01)
    assert b["market"]["main"]["status"] == "ok"
    assert b["market"]["northbound"]["status"] == "missing"
    assert "2024年8月" in b["market"]["northbound"]["reason"]
    assert b["indices"]["chinext"]["etf"]["status"] == "missing"


def test_refresh_skipped_fflow_merges_bundled_seed(monkeypatch):
    seed = F.build_flow_bundle(_raw_fixture(), {}, TODAY)
    monkeypatch.setattr(F, "load_bundled_flow_seed", lambda: seed)
    monkeypatch.setattr(F, "fetch_remote_flow_seed", lambda *a, **k: None)
    monkeypatch.setattr(F, "skip_push2", lambda: True)
    p = F.IndexFlowProvider(fetchers={
        "cal": lambda: [date(2026, 11, 9), date(2026, 11, 10), date(2026, 11, 11)],
        "etf_prices": lambda: {"510300": F.parse_sina_kline(_load("cn_flow_sina_kline.json"))},
        "etf_shares": lambda: {
            date(2026, 11, 11): {"510300": (date(2026, 11, 11), 46.0e8)},
            date(2026, 11, 10): {"510300": (date(2026, 11, 10), 45.0e8)},
        },
        "margin_sh": lambda: [(date(2026, 11, 11), 11700.0)],
        "margin_sz": lambda: [(date(2026, 11, 11), 10800.0)],
    })
    b = p.refresh(force=True)
    assert b["indices"]["csi300"]["main"]["status"] == "ok"
    assert b["indices"]["csi300"]["main"]["from_snapshot"] is True
    assert b["indices"]["csi300"]["main"]["net_1d"] == pytest.approx(110.13, abs=0.01)
    assert b["indices"]["csi300"]["etf"]["status"] == "ok"
    assert b["market"]["northbound"]["status"] == "missing"
    assert b["indices"]["chinext"]["etf"]["status"] == "missing"
    assert "深交所" in (b["indices"]["chinext"]["etf"].get("reason") or "")


def test_default_fetchers_skip_push2_on_render_or_flag(monkeypatch):
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("SKIP_PUSH2", raising=False)
    assert "main_all" in F.default_fetchers()
    monkeypatch.setenv("SKIP_PUSH2", "1")
    f = F.default_fetchers()
    assert "main_all" not in f
    assert "cal" in f and "etf_prices" in f and "margin_sh" in f
    monkeypatch.delenv("SKIP_PUSH2")
    monkeypatch.setenv("RENDER", "true")
    assert "main_all" not in F.default_fetchers()


def test_load_bundled_flow_seed_has_ok_main():
    seed = F._load_flow_seed_from_paths(F.FLOW_SEED_PATHS)
    assert seed is not None
    assert F.flow_main_coverage(seed) >= 6
    assert seed["market"]["main"]["status"] == "ok"
    # GitHub Actions 每次刷新快照都会前移 asof：只校验是真实日期，不钉死某一天
    assert F._to_date(seed["market"]["main"]["asof"]) is not None
    assert seed["market"]["northbound"]["status"] == "missing"
    assert seed["indices"]["chinext"]["etf"]["status"] == "missing"


def test_with_cn_flows_does_not_serve_wiped_cache_over_seed(monkeypatch):
    os.environ["SKIP_BG_WARMUP"] = "1"
    import app as app_mod

    def deny(*a, **k):
        raise OSError("network disabled")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("CN_INDEX_FLOW_AUTO", raising=False)
    raw = _raw_fixture()
    raw["main:market"] = list(raw["main:csi300"])
    seed = F.build_flow_bundle(raw, {}, TODAY)
    wiped = {
        "fetched_at": "2026-11-12T14:48:23+08:00",
        "asof": None,
        "indices": {m["id"]: {"id": m["id"], "name": m["name"], "tracked": m["tracked"],
                              "main": _miss_main(),
                              "etf": {"status": "missing", "reason": "未刷新"}}
                    for m in F.FLOW_INDICES},
        "market": {"main": _miss_main(),
                   "northbound": {"status": "missing", "reason": F.NORTHBOUND_REASON},
                   "margin_sh": {"status": "ok", "asof": "2026-11-11", "balance": 11700.0},
                   "margin_sz": {"status": "ok", "asof": "2026-11-11", "balance": 10800.0},
                   "margin_total": {"status": "ok", "asof": "2026-11-11", "balance": 22500.0}},
        "notes": [],
        "errors": {"main:csi300": "限流"},
    }
    # 给创业板保留「深交所不提供历史份额」以便断言
    wiped["indices"]["chinext"]["etf"] = {"status": "missing", "reason": "创业板ETF在深交所上市，深交所不提供历史份额，无法计算申赎"}
    monkeypatch.setattr(F.DEFAULT_FLOW_PROVIDER, "_cache", (__import__("time").time(), wiped))
    payload = {"index_flows": seed, "indices": []}
    out = app_mod._with_cn_flows(payload, kick=False)
    flows = out["index_flows"]
    assert flows["indices"]["csi300"]["main"]["status"] == "ok"
    assert flows["indices"]["csi300"]["main"]["net_1d"] == pytest.approx(110.13, abs=0.01)
    assert flows["indices"]["csi300"]["main"].get("from_snapshot") is True
    assert flows["market"]["main"]["status"] == "ok"
    # 直播两融保留
    assert flows["market"]["margin_sh"]["status"] == "ok"
    assert flows["market"]["northbound"]["status"] == "missing"
    assert "2024年8月" in flows["market"]["northbound"]["reason"]
    assert flows["indices"]["chinext"]["etf"]["status"] == "missing"
    assert "深交所" in (flows["indices"]["chinext"]["etf"].get("reason") or "")
