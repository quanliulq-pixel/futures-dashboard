# -*- coding: utf-8 -*-
"""黄金量化纸面跟踪：无未来函数、成本/融资计算、账本只追加、抓取失败回退、端点秒回。全部离线。"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

os.environ.setdefault("SKIP_BG_WARMUP", "1")
import gold_quant as gq  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 14, 16, 0, tzinfo=gq.CST)


def _ohlc(n=3000, seed=1, end="2026-11-13", drift=0.0004):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end=end, periods=n)
    r = rng.normal(drift, 0.011, n)
    close = 300 * np.exp(np.cumsum(r))
    open_ = close * np.exp(rng.normal(0, 0.003, n))
    return pd.DataFrame({"open": open_, "high": np.maximum(open_, close) * 1.004, "low": np.minimum(open_, close) * 0.996,
                         "close": close}, index=idx)


def _rates(start="2005-01-01"):
    idx = pd.date_range(start, "2026-08-01", freq="MS")
    return pd.Series(2.0, index=idx)


def test_signal_at_t_unchanged_when_future_data_perturbed():
    df = _ohlc()
    cfg = gq.STRATEGIES["c1"]
    base = gq.target_weights(df["close"], cfg["target_vol"], cfg["cap"], cfg["band"])
    t = df.index[1000]
    shocked = df.copy()
    shocked.loc[shocked.index > t, ["open", "high", "low", "close"]] *= np.linspace(0.3, 3.0, (shocked.index > t).sum())[:, None]
    alt = gq.target_weights(shocked["close"], cfg["target_vol"], cfg["cap"], cfg["band"])
    pd.testing.assert_frame_equal(base.loc[:t], alt.loc[:t])
    # P&L through t is also unaffected by data after t
    a = gq.simulate(df, base["w"], 4e-4).loc[:t]
    b = gq.simulate(shocked, alt["w"], 4e-4).loc[:t]
    pd.testing.assert_frame_equal(a, b)


def test_votes_and_score_definition():
    df = _ohlc()
    v = gq.trend_votes(df["close"])
    assert list(v.columns) == ["mom21", "mom63", "mom126", "mom252", "ema50_200"]
    assert v.iloc[:252].isna().all().all()
    T = gq.trend_score(df["close"])
    assert np.allclose(T.dropna(), v.dropna().sum(axis=1) / 5)
    assert set(np.unique(T.dropna() * 5)).issubset({-5, -3, -1, 1, 3, 5})


def test_weights_respect_long_only_cap_and_band():
    df = _ohlc(drift=0.002)
    for key in ("c1", "robust"):
        cfg = gq.STRATEGIES[key]
        tw = gq.target_weights(df["close"], cfg["target_vol"], cfg["cap"], cfg["band"])
        assert (tw["w"] >= 0).all() and (tw["w"] <= cfg["cap"] + 1e-12).all()
        ch = tw["w"].diff().abs()
        changed = ch[ch > 0]
        assert ((changed > cfg["band"] - 1e-12) | (tw["w"].loc[changed.index] == 0) | (tw["w"].shift().loc[changed.index] == 0)).all()


def test_execution_timing_cost_and_financing_math():
    idx = pd.bdate_range("2026-01-05", periods=5)
    ohlc = pd.DataFrame({"open": [100, 102, 99, 101, 100.0], "high": [103] * 5, "low": [97] * 5,
                         "close": [101, 100, 100, 102, 101.0]}, index=idx)
    w = pd.Series([0, 1.5, 1.5, 0, 0.0], index=idx)       # decided at close
    rate = pd.Series(0.03, index=idx)
    sim = gq.simulate(ohlc, w, 4e-4, rate)
    d2 = sim.loc[idx[2]]                                  # first day executed: open fill at w=1.5, flat overnight
    assert d2["w"] == 1.5 and d2["w_on"] == 0
    exp_gross = (1 + 0 * (99 / 100 - 1)) * (1 + 1.5 * (100 / 99 - 1)) - 1
    assert d2["gross"] == pytest.approx(exp_gross)
    assert d2["tc"] == pytest.approx(4e-4 * 1.5)
    assert d2["fin"] == pytest.approx(0.5 * 0.03 / 252)
    assert d2["net"] == pytest.approx(exp_gross - 4e-4 * 1.5 - 0.5 * 0.03 / 252)
    d4 = sim.loc[idx[4]]                                  # overnight at w[2]=1.5, exit at open per w[3]=0
    assert d4["w_on"] == 1.5 and d4["w"] == 0 and d4["tc"] == pytest.approx(4e-4 * 1.5) and d4["fin"] == 0
    d3 = sim.loc[idx[3]]
    assert d3["w_on"] == 1.5 and d3["w"] == 1.5 and d3["tc"] == 0
    # unlevered weights pay no financing
    assert (gq.simulate(ohlc, w.clip(upper=1), 4e-4, rate)["fin"] == 0).all()


def test_financing_rate_is_lagged_monthly():
    idx = pd.bdate_range("2026-01-01", "2026-04-30")
    m = pd.Series({pd.Timestamp("2026-01-01"): 1.5, pd.Timestamp("2026-02-01"): 2.5})
    r = gq.financing_rate(idx, m)
    # January value is usable only from Jan-31 + 45 days (2026-03-17) plus two sessions
    assert r.loc[:"2026-03-18"].isna().all()
    assert r.loc["2026-03-20"] == pytest.approx(0.015)
    assert r.loc["2026-04-10"] == pytest.approx(0.015)
    # February value: Feb-28 + 45 days = 2026-04-14, plus two sessions
    assert r.loc["2026-04-15"] == pytest.approx(0.015) and r.loc["2026-04-30"] == pytest.approx(0.025)


def test_completed_bars_drops_forming_bars():
    df = _ohlc(n=300, end="2026-11-16")
    morning = datetime(2026, 11, 16, 10, 0, tzinfo=gq.CST)
    assert gq.completed_bars(df, morning).index[-1] == pd.Timestamp("2026-11-13")
    after = datetime(2026, 11, 16, 15, 30, tzinfo=gq.CST)
    assert gq.completed_bars(df, after).index[-1] == pd.Timestamp("2026-11-16")
    friday_night = datetime(2026, 11, 13, 22, 0, tzinfo=gq.CST)  # night session bar dated next trading day
    assert gq.completed_bars(df, friday_night).index[-1] == pd.Timestamp("2026-11-13")


def test_parsers():
    js = 'var _AU0=([{"d":"2026-11-12","o":"850","h":"860","l":"840","c":"845","v":"1"},{"d":"2026-11-13","o":"845","h":"849","l":"829","c":"839"}]);'
    f = gq.parse_sina_futures_jsonp(js)
    assert list(f.index.strftime("%Y-%m-%d")) == ["2026-11-12", "2026-11-13"] and f["close"].iloc[-1] == 839
    assert gq.parse_sina_futures_jsonp("garbage").empty
    k = gq.parse_sina_stock_kline([{"day": "2026-11-13", "open": "7.99", "high": "8.01", "low": "7.93", "close": "7.954"},
                                   {"day": "bad", "open": "x"}])
    assert len(k) == 1 and k["close"].iloc[0] == pytest.approx(7.954)
    s = gq.parse_fred_monthly("observation_date,X\n2026-07-01,1.54\n2026-08-01,.\n")
    assert len(s) == 1 and s.iloc[0] == 1.54


def _raw():
    return {"au0": _ohlc(seed=3), "etf518880": _ohlc(seed=4), "cn_rate": _rates()}


def test_payload_schema_signal_and_ledger_start():
    p = gq.build_payload(_raw(), {}, None, now=NOW)
    json.dumps(p, allow_nan=False)
    assert p["status"] == "ok" and p["research_only"] is True and p["order_interface"] is False
    assert p["disclaimer"].startswith("研究参考，不能下单")
    for key in ("c1", "robust"):
        b = p["strategies"][key]
        assert b["status"] == "ok" and b["asof"] == "2026-11-13"
        sg = b["signal"]
        assert len(sg["votes"]) == 5 and 0 <= sg["target_weight"] <= gq.STRATEGIES[key]["cap"]
        for per in ("full", "is", "oos"):
            assert b["backtest"]["strategy"][per]["status"] == "ok" and b["backtest"]["buy_hold"][per]["status"] == "ok"
        assert b["paper"]["ledger"][0] == {**b["paper"]["ledger"][0], "date": "2026-11-13", "held_weight": 0.0, "nav": 1.0}
        assert b["curves"]["dates"][-1] == "2026-11-13" and b["yearly"]
        assert b["caveats"]
    c1 = p["strategies"]["c1"]
    assert any("依赖杠杆" in c for c in c1["caveats"]) and any("起点敏感" in c for c in c1["caveats"])
    assert any("统计不显著" in c for c in c1["caveats"]) and any("1 手名义约" in c for c in c1["caveats"])
    assert c1["signal"]["lot_notional_wan"] > 0


def test_ledger_is_append_only_and_uses_recorded_weights():
    raw = _raw()
    p0 = gq.build_payload(raw, {}, None, now=NOW)
    # tamper with recorded history: it must be preserved verbatim
    prev = json.loads(json.dumps(p0))
    prev["strategies"]["robust"]["paper"]["ledger"][0]["target_weight"] = 1.0
    prev["strategies"]["robust"]["paper"]["ledger"][0]["note"] = "recorded"
    more = {k: v for k, v in raw.items()}
    ext = _ohlc(n=3002, seed=4, end="2026-11-17")
    more["etf518880"] = ext
    p1 = gq.build_payload(more, {}, prev, now=datetime(2026, 11, 17, 16, 0, tzinfo=gq.CST))
    led = p1["strategies"]["robust"]["paper"]["ledger"]
    assert led[0]["note"] == "recorded" and led[0]["target_weight"] == 1.0
    assert [r["date"] for r in led] == ["2026-11-13", "2026-11-16", "2026-11-17"]
    d = pd.Timestamp("2026-11-16")
    o, c, pc = ext["open"].loc[d], ext["close"].loc[d], ext["close"].loc[pd.Timestamp("2026-11-13")]
    exp = (1 + 0.0 * (o / pc - 1)) * (1 + 1.0 * (c / o - 1)) - 1 - 3e-4 * 1.0
    assert led[1]["held_weight"] == 1.0 and led[1]["daily_return"] == pytest.approx(exp, abs=1e-6)
    assert led[2]["nav"] == pytest.approx(led[1]["nav"] * (1 + led[2]["daily_return"]), abs=1e-5)


def test_failed_fetch_falls_back_to_labeled_snapshot_or_no_data():
    p0 = gq.build_payload(_raw(), {}, None, now=NOW)
    raw = _raw()
    raw.pop("au0")
    p1 = gq.build_payload(raw, {"au0": "HTTPError: 503"}, p0, now=NOW + timedelta(days=2))
    c1 = p1["strategies"]["c1"]
    assert c1["snapshot"] is True and c1["snapshot_date"] == "2026-11-13" and "503" in c1["snapshot_reason"]
    assert p1["strategies"]["robust"].get("snapshot") is None
    p2 = gq.build_payload(raw, {"au0": "HTTPError: 503"}, p0, now=NOW + timedelta(days=30))
    assert p2["strategies"]["c1"] == {"status": "missing", "reason": "HTTPError: 503"}
    # FRED down: financing comes from the previous snapshot's real monthly rates, labeled
    raw = _raw()
    raw.pop("cn_rate")
    p3 = gq.build_payload(raw, {"cn_rate": "timeout"}, p0, now=NOW)
    assert p3["strategies"]["c1"]["status"] == "ok" and p3["financing"]["snapshot"] is True
    # nothing anywhere -> honest no_data
    p4 = gq.build_payload({}, {"au0": "x", "etf518880": "y", "cn_rate": "z"}, None, now=NOW)
    assert p4["status"] == "no_data" and all(b["status"] == "missing" for b in p4["strategies"].values())


def test_refresh_keeps_snapshot_when_everything_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(gq, "RUNTIME_PATH", tmp_path / "rt.json")
    seed = gq.build_payload(_raw(), {}, None, now=NOW)
    monkeypatch.setattr(gq, "SEED_PATHS", (tmp_path / "seed.json",))
    (tmp_path / "seed.json").write_text(json.dumps(seed), encoding="utf-8")
    monkeypatch.setattr(gq, "_cache", None)

    def boom():
        raise RuntimeError("down")

    out = gq.refresh({"au0": boom, "etf518880": boom, "cn_rate": boom}, use_remote=False, now=NOW)
    assert out["from_snapshot"] is True and out["updated_at"] == seed["updated_at"]
    assert not (tmp_path / "rt.json").exists()


def test_no_push2_and_no_order_interface_in_module():
    src = (ROOT / "gold_quant.py").read_text(encoding="utf-8")
    assert "push2" not in src
    for bad in ("place_order", "submit_order", "broker", "下单接口\""):
        assert bad not in src


def test_committed_seed_matches_research_rules():
    p = json.loads((ROOT / "docs/api/gold_quant.json").read_text(encoding="utf-8"))
    assert p["status"] == "ok" and p["order_interface"] is False
    c1, rb = p["strategies"]["c1"], p["strategies"]["robust"]
    assert c1["params"] == {"target_vol": 0.2, "cap": 1.5, "band": 0.1, "cost_per_side": 0.0004, "financing": True, "eval_warmup": 504}
    assert rb["params"] == {"target_vol": 0.12, "cap": 1.0, "band": 0.1, "cost_per_side": 0.0003, "financing": False, "eval_warmup": 252}
    for b in (c1, rb):
        assert b["paper"]["start"] == "2026-11-13" and b["paper"]["ledger"][0]["date"] == "2026-11-13"
        assert b["paper"]["ledger"][0]["held_weight"] == 0.0
    assert json.loads((ROOT / "data/gold_quant.json").read_text(encoding="utf-8"))["strategies"]["c1"]["params"] == c1["params"]


def test_endpoints_seed_first_and_non_blocking(monkeypatch):
    import app as app_mod
    import bg_tasks

    started = []
    monkeypatch.setattr(bg_tasks, "try_start", lambda key, fn, **kw: started.append(key) or "started")
    stale = gq.build_payload(_raw(), {}, None, now=NOW - timedelta(hours=30))
    stale = json.loads(json.dumps(stale))
    monkeypatch.setattr(gq, "snapshot", lambda: dict(stale))
    c = app_mod.app.test_client()
    r = c.get("/api/gold/quant")
    assert r.status_code == 200
    body = r.get_json()
    assert body["status"] == "ok" and body["refresh_status"] == "started" and started == ["gold-quant"]
    r = c.get("/api/gold/quant/refresh")
    body = r.get_json()
    assert r.status_code == 200 and body["refresh"] == {"status": "started", "queued": True}
    assert "后台量化纸面跟踪刷新" in body["note"]
    fresh = {"status": "ok", "updated_at": datetime.now(gq.CST).isoformat(timespec="seconds")}
    monkeypatch.setattr(gq, "snapshot", lambda: dict(fresh))
    started.clear()
    body = c.get("/api/gold/quant").get_json()
    assert "refresh_status" not in body and started == []


def test_wired_into_gold_tab_pages_and_export():
    for name in ("templates/index.html", "docs/index.html"):
        html = (ROOT / name).read_text(encoding="utf-8")
        assert 'id="gold-quant-content"' in html
        assert html.index('id="gold-quant-content"') > html.index('id="view-gold"')
    exp = (ROOT / "export_static_pages.py").read_text(encoding="utf-8")
    assert 'API / "gold_quant.json"' in exp and "gold_quant.refresh(use_remote=False)" in exp
    assert '"gold_quant.py"' in (ROOT / ".github/workflows/refresh_pages.yml").read_text(encoding="utf-8")
    js = (ROOT / "static/app.js").read_text(encoding="utf-8")
    assert "'/api/gold/quant': './api/gold_quant.json'" in js
