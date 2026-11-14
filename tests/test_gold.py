# -*- coding: utf-8 -*-
"""黄金页：解析器、指标、溢价/比价、快照回退、信号与接口（全部 mock，无网络）。"""
from __future__ import annotations

import json
import math
import os
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

os.environ.setdefault("SKIP_BG_WARMUP", "1")

import gold  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 13, 16, 0, tzinfo=gold.CST)


def _days(n: int, end: date = date(2026, 11, 12)) -> list[date]:
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


def _series(n: int, start: float, step: float, end: date = date(2026, 11, 12), wiggle: float = 0.0):
    return [(d, start + i * step + (wiggle * math.sin(i) if wiggle else 0.0)) for i, d in enumerate(_days(n, end))]


def _raw(**over):
    gc = _series(600, 3000.0, 1.5, wiggle=8)
    raw = {
        "yahoo": {
            "GC=F": gc,
            "SI=F": [(d, v / 80.0) for d, v in gc],
            "USDCNY=X": [(d, 7.0) for d, _ in gc],
            "^TNX": _series(600, 4.0, 0.001),
            "DX-Y.NYB": _series(600, 105.0, -0.01),
            "^VIX": _series(600, 15.0, 0.0, wiggle=1),
        },
        "sina_xau": [(d, v - 5) for d, v in gc],
        "au0": [(d, (v - 5) * 7.0 / gold.OZ_G + 2.0) for d, v in gc],
        "sge": [(d, (v - 5) * 7.0 / gold.OZ_G + 1.0) for d, v in gc],
        "fred": {"DFII10": _series(600, 2.5, -0.001), "DGS10": _series(600, 4.5, -0.0005)},
        "cftc": [{"date": d, "long": 200000 + i * 100, "short": 40000, "oi": 400000, "net": 160000 + i * 100}
                 for i, d in enumerate(_days(160)[::5])],
        "spdr": _series(260, 900.0, 0.5),
        "pboc": [{"month": f"2026-{m:02d}", "date": date(2026, m, 1), "value": 3000.0 + m, "mom_pct": 0.1, "yoy_pct": 3.0}
                 for m in range(1, 11)],
        "cn_etfs": [
            {"status": "ok", "code": "518880", "name": "华安黄金ETF", "asof": "2026-11-12", "shares_yi": 117.98,
             "net_1d": 1.5, "net_5d": -3.89, "net_20d": 12.0},
            {"status": "missing", "code": "518800", "name": "国泰黄金ETF", "reason": "上交所无该日份额"},
        ],
        "inst": {"status": "ok", "code": "518880", "asof": "2026-06-30", "inst_pct": 58.37},
        "realtime": {
            "gc": {"last": 3900.0, "prev": 3880.0, "chg_pct": 0.52, "time": "2026-11-13 15:54:49"},
            "xau": {"last": 3897.0, "prev": 3877.0, "chg_pct": 0.52, "time": "2026-11-13 15:54:00"},
            "si": None,
            "au0": {"last": 839.0, "prev": 860.46, "chg_pct": -2.49, "time": "2026-11-13 15:00:00"},
            "usdcny": {"last": 6.6709, "prev": 6.6745, "chg_pct": -0.05, "time": "2026-11-13 15:54:01"},
            "etf518880": None,
        },
    }
    raw.update(over)
    return raw


# ---------------------------------------------------------------- parsers
def test_parse_sina_hq_realtime_fields():
    text = (
        'var hq_str_hf_GC="3899.666,,3901.400,3901.700,3927.200,3879.100,15:45:34,3885.300,3883.300,0,1,3,2026-11-13,纽约黄金,0";\n'
        'var hq_str_hf_XAU="3897.42,3877.430,3897.42,3897.77,3922.99,3874.93,15:45:00,3877.43,3877.21,0,0,0,2026-11-13,伦敦金（现货黄金）";\n'
        'var hq_str_nf_AU0="黄金连续,150000,845.660,848.860,829.600,839.000,839.000,839.180,839.000,838.200,860.460,7,7,123911.000,332909,沪,黄金,2026-11-13,1";\n'
        'var hq_str_fx_susdcny="15:44:01,6.6709000000,6.6711000000,6.6745000000,72.0000000000,6.6700000000,6.6744000000,6.6672000000,6.6708000000,在岸人民币,-0.0554,-0.0037,0.0072,此行情由新浪财经计算得出,0.0000,0.0000,,2026-11-13";\n'
        'var hq_str_sh518880="黄金ETF华安,7.992,8.085,7.954,8.012,7.938,7.953,7.954,586087445,4676144668.000,363500,7.953,890600,7.952,293400,7.951,521400,7.950,452300,7.949,349600,7.954,126400,7.955,117300,7.956,43600,7.957,136200,7.958,2026-11-13,15:34:59,00";\n'
        'var hq_str_hf_SI="";\n'
    )
    q = gold.realtime_from_hq(gold.parse_sina_hq(text))
    assert q["gc"]["last"] == pytest.approx(3899.666) and q["gc"]["prev"] == 3885.3
    assert q["xau"]["time"] == "2026-11-13 15:45:00"
    assert q["au0"] == {"last": 839.0, "prev": 860.46, "chg_pct": -2.49, "time": "2026-11-13 15:00:00"}
    assert q["usdcny"]["last"] == pytest.approx(6.6708)
    assert q["etf518880"]["last"] == 7.954 and q["etf518880"]["prev"] == 8.085
    assert q["si"] is None  # empty upstream -> None, never 0


def test_parse_sina_jsonp_and_sge_and_fred_and_cftc_and_jin10_and_pboc():
    inner = 'var _AU0=([{"d":"2026-11-11","o":"876.1","h":"876.9","l":"863.5","c":"873.300"},{"d":"2026-11-12","c":"852.500"}]);'
    assert gold.parse_sina_jsonp_kline(inner, "d", "c") == [(date(2026, 11, 11), 873.3), (date(2026, 11, 12), 852.5)]
    glob = 'var _XAU=([{"date":"2026-11-12","close":"3877.430"},{"date":"bad","close":"1"}]);'
    assert gold.parse_sina_jsonp_kline(glob, "date", "close") == [(date(2026, 11, 12), 3877.43)]
    assert gold.parse_sina_jsonp_kline("garbage", "d", "c") == []
    assert gold.parse_sge_daily({"time": [["2026-11-12", 869.0, 851.33, 850.02, 871.4]]}) == [(date(2026, 11, 12), 851.33)]
    assert gold.parse_fred_csv("observation_date,DFII10\n2026-11-09,2.66\n2026-11-10,.\n2026-11-11,2.78\n") == [
        (date(2026, 11, 9), 2.66), (date(2026, 11, 11), 2.78)]
    rows = gold.parse_cftc([{"report_date_as_yyyy_mm_dd": "2026-11-03T00:00:00.000", "noncomm_positions_long_all": "220530",
                             "noncomm_positions_short_all": "40982", "open_interest_all": "399169"},
                            {"report_date_as_yyyy_mm_dd": "2026-10-27T00:00:00.000", "noncomm_positions_long_all": "x"}])
    assert len(rows) == 1 and rows[0]["net"] == 179548 and rows[0]["date"] == date(2026, 11, 3)
    assert gold.parse_jin10_etf({"data": {"values": [["2026-11-12", 1040.703, -0.38, 1.29e11]]}}) == [(date(2026, 11, 12), 1040.703)]
    pb = gold.parse_pboc({"result": {"data": [{"REPORT_DATE": "2026-10-01 00:00:00", "GOLD_RESERVES": 3064.11,
                                               "GOLD_RESERVES_SEQUENTIAL": -5.28, "GOLD_RESERVES_SAME": 3.09}]}})
    assert pb[0]["month"] == "2026-10" and pb[0]["value"] == 3064.11


# ---------------------------------------------------------------- maths
def test_indicators_basic_properties():
    up = [float(i) for i in range(1, 100)]
    assert gold.rsi(up) == 100.0
    assert gold.sma([1, 2, 3, 4], 2) == [None, 1.5, 2.5, 3.5]
    flat_hv = gold.hv_series([100.0] * 40, 20)
    assert flat_hv[-1] == pytest.approx(0.0)
    assert gold.pct_rank(list(range(100)), 49) == pytest.approx(50.0)
    assert gold.pct_rank([1, 2], 1) is None  # too short -> None, not a guess


def test_intl_conversion_formula_rmb_per_gram():
    # 3897.42 USD/oz × 6.6709 / 31.1034768 = 835.9 元/克
    assert gold.intl_rmb_per_gram(3897.42, 6.6709) == pytest.approx(835.90, abs=0.01)
    assert gold.intl_rmb_per_gram(None, 6.67) is None


def test_align_prior_has_no_lookahead():
    cn = [(date(2026, 11, 10), 1.0), (date(2026, 11, 11), 1.0)]
    intl = [(date(2026, 11, 9), 10.0), (date(2026, 11, 10), 11.0), (date(2026, 11, 11), 12.0)]
    out = gold.align_prior(cn, intl)
    assert out == [(date(2026, 11, 10), 10.0, date(2026, 11, 9)), (date(2026, 11, 11), 11.0, date(2026, 11, 10))]


def test_realtime_premium_matches_formula():
    rt = gold.build_realtime(_raw()["realtime"])
    rmb = 3897.0 * 6.6709 / gold.OZ_G
    assert rt["xau_rmb_g"] == pytest.approx(round(rmb, 2))
    assert rt["premium_vs_xau"] == pytest.approx(round(839.0 - rmb, 2))
    assert rt["premium_vs_xau_pct"] == pytest.approx(round((839.0 / rmb - 1) * 100, 2))
    assert "31.1034768" in rt["formula"]
    assert gold.build_realtime({"gc": None, "xau": None, "au0": None})["status"] == "missing"


# ---------------------------------------------------------------- payload
def test_build_payload_schema_and_values():
    p = gold.build_payload(_raw(), {}, None, now=NOW)
    assert p["status"] == "ok" and p["schema_version"] == 1
    for key in ("realtime", "prices", "premium", "ratio", "macro", "technicals", "positioning", "chart", "signal", "sources"):
        assert key in p, key
    # 构造的沪金 = 伦敦金折算 + 2 元/克 → 溢价应≈ +2（用前一交易日海外价，序列线性，误差可控）
    assert p["premium"]["status"] == "ok" and p["premium"]["unit"] == "元/克"
    assert 0 < p["premium"]["latest"] < 3
    assert p["premium"]["sge"]["latest"] < p["premium"]["latest"]
    assert p["ratio"]["latest"] == pytest.approx(80.0)
    assert p["macro"]["breakeven10y"]["last"] == pytest.approx(
        round(_raw()["fred"]["DGS10"][-1][1] - _raw()["fred"]["DFII10"][-1][1], 2))
    t = p["technicals"]["GC"]
    assert t["status"] == "ok" and t["trend"] in ("多头排列", "空头排列", "震荡") and 0 <= t["pos_52w"] <= 100
    pos = p["positioning"]
    assert pos["spdr"]["tonnes"] == pytest.approx(900 + 259 * 0.5)
    assert pos["cftc"]["status"] == "ok" and pos["cftc"]["net_pct_oi"] > 0
    assert pos["cn_etfs"]["net_20d"] == 12.0 and pos["cn_etfs"]["items"][1]["status"] == "missing"
    assert pos["pboc"]["month"] == "2026-10" and pos["pboc"]["unit"] == "亿美元"
    c = p["chart"]
    assert len(c["dates"]) == len(c["gc"]) == len(c["real10y"]) == gold.CHART_DAYS
    assert len(c["au_dates"]) == len(c["prem_pct"])
    sig = p["signal"]
    assert sig["regime"] in ("偏多", "震荡", "偏空") and sig["disclaimer"].startswith("研究参考")
    assert {f["key"] for f in sig["factors"]} == {"trend", "macro", "flows", "premium"}
    assert all(-2 <= f["score"] <= 2 for f in sig["factors"])
    json.dumps(p, ensure_ascii=False, allow_nan=False, default=str)


def test_failed_block_falls_back_to_recent_snapshot_with_label():
    prev = gold.build_payload(_raw(), {}, None, now=NOW - timedelta(days=1))
    prev = json.loads(json.dumps(prev, ensure_ascii=False, default=str))
    raw = _raw(spdr=None, cftc=None)
    raw.pop("realtime")
    p = gold.build_payload(raw, {"spdr": "ReadTimeout", "cftc": "HTTP 503", "realtime": "x"}, prev, now=NOW)
    sp = p["positioning"]["spdr"]
    assert sp["status"] == "ok" and sp["snapshot"] is True and sp["snapshot_date"] == "2026-11-12"
    assert p["realtime"]["snapshot"] is True
    assert "positioning.spdr" in p["snapshot_blocks"] and "positioning.cftc" in p["snapshot_blocks"]
    assert p["errors"]["spdr"] == "ReadTimeout"


def test_too_old_snapshot_is_not_used_shows_no_data():
    prev = gold.build_payload(_raw(), {}, None, now=NOW - timedelta(days=30))
    prev = json.loads(json.dumps(prev, ensure_ascii=False, default=str))
    raw = _raw(spdr=None)
    p = gold.build_payload(raw, {}, prev, now=NOW + timedelta(days=30))
    assert p["positioning"]["spdr"]["status"] == "missing"
    assert not p["positioning"]["spdr"].get("snapshot")


def test_everything_down_is_no_data_not_fabricated():
    p = gold.build_payload({}, {"yahoo": "down"}, None, now=NOW)
    assert p["status"] == "no_data" and p["error"] == "无数据"
    assert p["premium"]["status"] == "missing" and p["technicals"]["GC"]["status"] == "missing"
    assert all(f["score"] == 0 for f in p["signal"]["factors"])
    assert p["signal"]["regime"] == "震荡"


def test_yahoo_down_uses_sina_gc_fallback_series():
    raw = _raw()
    gc = raw["yahoo"]["GC=F"]
    raw["yahoo"] = {}
    raw["sina_gc"] = gc
    raw["sina_si"] = [(d, v / 80.0) for d, v in gc]
    p = gold.build_payload(raw, {"yahoo": "down"}, None, now=NOW)
    assert p["prices"]["rows"]["GC"]["source"] == "新浪 GC 日线"
    assert p["technicals"]["GC"]["status"] == "ok" and p["ratio"]["latest"] == pytest.approx(80.0)
    assert p["macro"]["dxy"]["status"] == "missing"


def test_signal_regimes():
    base = {"technicals": {"GC": {"status": "ok", "last": 110, "ma20": 105, "ma60": 100, "vs_ma60_pct": 10, "rsi14": 60, "hv_rank_1y": 80}},
            "macro": {"real10y": {"status": "ok", "last": 1.5, "chg_20d_bp": -25}, "dxy": {"status": "ok", "last": 99, "chg_20d_pct": -2}},
            "positioning": {"spdr": {"status": "ok", "tonnes": 900, "chg_20d": 12}, "cftc": {"status": "ok", "rank_3y": 50},
                            "cn_etfs": {"status": "ok", "net_20d": 30}},
            "premium": {"status": "ok", "rank_1y": 50}}
    s = gold.build_signal(base)
    assert s["regime"] == "偏多" and s["score"] >= 3 and "卖虚值Put" in s["hint"]
    bear = json.loads(json.dumps(base))
    bear["technicals"]["GC"].update(last=90, ma20=95, rsi14=40)
    bear["macro"]["real10y"]["chg_20d_bp"] = 30
    bear["macro"]["dxy"]["chg_20d_pct"] = 2
    bear["positioning"]["spdr"]["chg_20d"] = -12
    bear["positioning"]["cn_etfs"]["net_20d"] = -30
    assert gold.build_signal(bear)["regime"] == "偏空"
    hot = json.loads(json.dumps(base))
    hot["premium"]["rank_1y"] = 95
    assert any("溢价回落" in r for f in gold.build_signal(hot)["factors"] for r in f["reasons"])


def test_fetch_all_isolates_failures_and_uses_sina_when_yahoo_fails(monkeypatch):
    calls = []

    def boom():
        raise TimeoutError("upstream down")

    monkeypatch.setattr(gold, "fetch_sina_global_daily", lambda s: calls.append(s) or [(date(2026, 11, 12), 1.0)])
    raw, errors = gold.fetch_all({"yahoo": boom, "au0": lambda: [(date(2026, 11, 12), 850.0)],
                                  "fred_real": boom, "fred_nominal": lambda: [(date(2026, 11, 10), 4.9)]})
    assert "yahoo" in errors and "fred_real" in errors and "au0" not in errors
    assert raw["au0"] == [(date(2026, 11, 12), 850.0)]
    assert raw["fred"] == {"DFII10": [], "DGS10": [(date(2026, 11, 10), 4.9)]}
    assert calls == ["GC", "SI"] and raw["sina_gc"]


def test_no_push2_or_dead_spdr_csv_in_gold_sources():
    src = (ROOT / "gold.py").read_text(encoding="utf-8")
    assert "push2" not in src
    assert "GLD_US_archive_EN.csv" not in src


# ---------------------------------------------------------------- snapshot / refresh / endpoints
def test_snapshot_prefers_newest_and_needs_refresh(monkeypatch, tmp_path):
    old = gold.build_payload(_raw(), {}, None, now=NOW - timedelta(hours=10))
    new = gold.build_payload(_raw(), {}, None, now=NOW - timedelta(hours=1))
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps(old, default=str), encoding="utf-8")
    b.write_text(json.dumps(new, default=str), encoding="utf-8")
    monkeypatch.setattr(gold, "_cache", None)
    monkeypatch.setattr(gold, "RUNTIME_PATH", tmp_path / "none.json")
    monkeypatch.setattr(gold, "SEED_PATHS", (a, b))
    monkeypatch.setattr(gold, "_now", lambda: NOW)
    snap = gold.snapshot()
    assert snap["updated_at"] == new["updated_at"] and snap["age_hours"] == pytest.approx(1.0)
    assert gold.needs_refresh(snap) is False
    assert gold.needs_refresh(json.loads(json.dumps(old, default=str))) is True
    assert gold.needs_refresh({"status": "no_data"}) is True


def test_snapshot_without_any_seed_is_honest(monkeypatch, tmp_path):
    monkeypatch.setattr(gold, "_cache", None)
    monkeypatch.setattr(gold, "RUNTIME_PATH", tmp_path / "x.json")
    monkeypatch.setattr(gold, "SEED_PATHS", (tmp_path / "y.json",))
    s = gold.snapshot()
    assert s["status"] == "no_data" and s["error"] == "无数据"


def test_refresh_keeps_previous_snapshot_when_everything_fails(monkeypatch, tmp_path):
    prev = gold.build_payload(_raw(), {}, None, now=NOW - timedelta(hours=5))
    seed = tmp_path / "seed.json"
    seed.write_text(json.dumps(prev, default=str), encoding="utf-8")
    monkeypatch.setattr(gold, "_cache", None)
    monkeypatch.setattr(gold, "RUNTIME_PATH", tmp_path / "rt.json")
    monkeypatch.setattr(gold, "SEED_PATHS", (seed,))
    monkeypatch.setattr(gold, "fetch_sina_global_daily", lambda s: (_ for _ in ()).throw(TimeoutError("x")))

    def boom():
        raise TimeoutError("down")

    out = gold.refresh({"yahoo": boom, "au0": boom}, use_remote=False)
    assert out["status"] == "ok" and out["from_snapshot"] is True and "上次快照" in out["note"]
    assert out["updated_at"] == prev["updated_at"]


def test_refresh_writes_runtime_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(gold, "_cache", None)
    monkeypatch.setattr(gold, "RUNTIME_PATH", tmp_path / "rt.json")
    monkeypatch.setattr(gold, "SEED_PATHS", (tmp_path / "none.json",))
    raw = _raw()
    fetchers = {k: (lambda v=v: v) for k, v in raw.items() if k not in ("fred", "yahoo")}
    fetchers["yahoo"] = lambda: raw["yahoo"]
    fetchers["fred_real"] = lambda: raw["fred"]["DFII10"]
    fetchers["fred_nominal"] = lambda: raw["fred"]["DGS10"]
    out = gold.refresh(fetchers, use_remote=False)
    assert out["status"] == "ok" and out["errors"] == {}
    assert json.loads((tmp_path / "rt.json").read_text(encoding="utf-8"))["status"] == "ok"


def test_committed_seed_schema():
    for path in gold.SEED_PATHS:
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["status"] == "ok" and data["schema_version"] == 1
        assert data["realtime"]["status"] == "ok" and "premium_vs_xau" in data["realtime"]
        assert data["premium"]["unit"] == "元/克" and data["technicals"]["GC"]["status"] == "ok"
        assert data["signal"]["disclaimer"].startswith("研究参考")
        assert data["chart"]["dates"]


def test_gold_endpoints_are_seed_first_and_non_blocking(monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    import app as app_mod
    import bg_tasks

    started = []
    monkeypatch.setattr(bg_tasks, "try_start", lambda key, fn, **kw: started.append((key, kw)) or "started")
    seed = gold.build_payload(_raw(), {}, None, now=NOW - timedelta(hours=10))
    seed = json.loads(json.dumps(seed, default=str))
    monkeypatch.setattr(gold, "snapshot", lambda: dict(seed))
    c = app_mod.app.test_client()
    r = c.get("/api/gold")
    assert r.status_code == 200
    body = r.get_json()
    assert body["status"] == "ok" and body["refresh_status"] == "started"
    assert started[-1][0] == "gold"
    r = c.get("/api/gold/refresh")
    body = r.get_json()
    assert r.status_code == 200 and body["refresh"] == {"status": "started", "queued": True}
    assert "后台黄金数据刷新" in body["note"]


def test_gold_endpoint_fresh_snapshot_does_not_spawn(monkeypatch):
    import app as app_mod
    import bg_tasks

    started = []
    monkeypatch.setattr(bg_tasks, "try_start", lambda key, fn, **kw: started.append(key) or "started")
    fresh = {"status": "ok", "updated_at": datetime.now(gold.CST).isoformat(timespec="seconds")}
    monkeypatch.setattr(gold, "snapshot", lambda: dict(fresh))
    body = app_mod.app.test_client().get("/api/gold").get_json()
    assert "refresh_status" not in body and started == []


def test_gold_tab_wired_in_template_docs_and_export():
    for name in ("templates/index.html", "docs/index.html"):
        html = (ROOT / name).read_text(encoding="utf-8")
        assert '<button type="button" data-view="gold" class="tab-gold">黄金</button>' in html
        assert 'id="view-gold"' in html and 'id="gold-content"' in html and "gold_view.js?v=" in html
    assert "20261114etf1" in (ROOT / "docs/index.html").read_text(encoding="utf-8")
    exp = (ROOT / "export_static_pages.py").read_text(encoding="utf-8")
    assert 'API / "gold.json"' in exp and "gold.refresh(use_remote=False)" in exp
    wf = (ROOT / ".github/workflows/refresh_pages.yml").read_text(encoding="utf-8")
    assert '"gold.py"' in wf
    js = (ROOT / "static/app.js").read_text(encoding="utf-8")
    assert 'name === "gold"' in js and "'/api/gold': './api/gold.json'" in js
