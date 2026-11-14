# -*- coding: utf-8 -*-
"""美股ETF页：指标口径、RSI、趋势、轮动象限、风险规则、快照回退、端点秒回与前端接线。全部离线。"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

os.environ.setdefault("SKIP_BG_WARMUP", "1")
import us_etf  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ASSET_V = "20261114etf1"
NOW = datetime(2026, 11, 14, 16, 0, tzinfo=us_etf.CST)  # = 2026-11-14 03:00 ET（周六）


def _ohlcv(n=520, seed=1, drift=0.0004, end="2026-11-13", vol=1e6):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end=end, periods=n)
    c = 100 * np.exp(np.cumsum(rng.normal(drift, 0.01, n)))
    return pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c, "Volume": np.full(n, vol)}, index=idx)


def _prices(**overrides):
    out = {}
    for i, s in enumerate(us_etf.all_symbols()):
        out[s] = _ohlcv(seed=i + 1)
    out["^VIX"]["Close"] = 15.0
    out["^VIX3M"]["Close"] = 18.0
    out.update(overrides)
    return {k: v for k, v in out.items() if v is not None}


def test_universe_is_mainstream_and_grouped():
    syms = [s for _, _, items in us_etf.GROUPS for s, _ in items]
    assert 25 <= len(syms) <= 40 and len(set(syms)) == len(syms)
    for s in ("SPY", "QQQ", "IWM", "XLK", "XLE", "SMH", "TLT", "HYG", "LQD", "GLD"):
        assert s in syms
    assert "^VIX" in us_etf.all_symbols() and "^VIX3M" in us_etf.all_symbols()


def test_returns_ytd_and_52w_metrics_exact():
    idx = pd.bdate_range("2025-01-01", periods=300)
    c = pd.Series(np.linspace(100, 199.7, 300), index=idx)
    df = pd.DataFrame({"Close": c, "Volume": 1000.0})
    df.iloc[-1, df.columns.get_loc("Volume")] = 2000.0
    m = us_etf.etf_metrics(df, c * 0 + 50)
    last = c.iloc[-1]
    assert m["r1d"] == pytest.approx(last / c.iloc[-2] - 1, abs=1e-4)
    assert m["r1m"] == pytest.approx(last / c.iloc[-22] - 1, abs=1e-4)
    assert m["r1y"] == pytest.approx(last / c.iloc[-253] - 1, abs=1e-4)
    prior = c[c.index.year < c.index[-1].year].iloc[-1]
    assert m["ytd"] == pytest.approx(last / prior - 1, abs=1e-4)
    assert m["from_high"] == 0 and m["pos_52w"] == 100
    assert m["trend"] == "多头排列"
    assert m["vol_ratio"] == 2.0
    # benchmark flat → relative strength = own return
    assert m["rs_3m"] == pytest.approx(m["r3m"], abs=1e-4)
    assert m["rsi14"] == 100.0


def test_rsi_wilder_reference_and_trend_states():
    up_down = pd.Series([100 + (1 if i % 2 else -1) * 0.5 for i in range(60)])
    assert 40 <= us_etf.rsi_wilder(up_down) <= 60
    assert us_etf.rsi_wilder(pd.Series(range(5), dtype=float)) is None
    assert us_etf.trend_state(10, 9, 8) == "多头排列"
    assert us_etf.trend_state(7, 8, 9) == "空头排列"
    assert us_etf.trend_state(9.5, 10, 9) == "偏多"
    assert us_etf.trend_state(8.5, 8, 9) == "偏空"
    assert us_etf.trend_state(10, None, 9) == "数据不足"


def test_short_history_has_no_fabricated_long_metrics():
    df = _ohlcv(n=80)
    m = us_etf.etf_metrics(df, None)
    assert m["r1y"] is None and m["ma200"] is None and m["trend"] == "数据不足"
    with pytest.raises(ValueError):
        us_etf.etf_metrics(_ohlcv(n=10), None)


def test_rrg_quadrants_and_points():
    assert us_etf.quadrant(0.1, 0.1) == "领先"
    assert us_etf.quadrant(0.1, -0.1) == "转弱"
    assert us_etf.quadrant(-0.1, -0.1) == "落后"
    assert us_etf.quadrant(-0.1, 0.1) == "改善"
    assert us_etf.quadrant(None, 0.1) is None
    idx = pd.bdate_range(end="2026-11-13", periods=300)
    bench = pd.Series(100.0, index=idx)
    strong = pd.Series(np.exp(np.linspace(0, 0.3, 300)) * 100, index=idx)
    pts = us_etf.rrg_points(strong, bench)
    assert len(pts) == us_etf.RRG_WEEKS and pts[-1]["d"] == "2026-11-13"
    assert pts[-1]["x"] > 0 and pts[-1]["y"] > 0
    assert us_etf.rrg_points(strong.iloc[-50:], bench.iloc[-50:]) == []


def test_risk_pair_and_vix_rules():
    idx = pd.bdate_range(end="2026-11-13", periods=120)
    up = pd.Series(np.linspace(1, 2, 120), index=idx)
    one = pd.Series(1.0, index=idx)
    assert us_etf.risk_pair(up, one)["score"] == 1
    assert us_etf.risk_pair(one, up)["score"] == -1
    assert us_etf.risk_pair(up.iloc[:30], one.iloc[:30])["status"] == "missing"
    calm = us_etf.vix_block(pd.Series(14.0, index=idx), pd.Series(17.0, index=idx))
    assert calm["score"] == 1 and calm["term_ratio"] == pytest.approx(14 / 17, abs=1e-3)
    inverted = us_etf.vix_block(pd.Series(22.0, index=idx), pd.Series(20.0, index=idx))
    assert inverted["score"] == -1
    assert us_etf.vix_block(pd.Series(30.0, index=idx), None)["score"] == -1
    assert us_etf.vix_block(None, None)["status"] == "missing"


def test_build_payload_shape_regime_and_no_nan():
    p = us_etf.build_payload(_prices(), now=NOW)
    assert p["status"] == "ok" and p["asof"] == "2026-11-13" and p["intraday"] is False
    assert p["disclaimer"] == "研究参考，不构成投资建议"
    assert [g["id"] for g in p["groups"]][:2] == ["broad", "sector"]
    assert all(r["status"] == "ok" for g in p["groups"] for r in g["items"])
    assert {r["symbol"] for r in p["rotation"]["items"]} <= {s for s, _ in dict((g[0], g[2]) for g in us_etf.GROUPS)["sector"]}
    assert p["risk"]["max_score"] == len(us_etf.RISK_PAIRS) + 1
    assert p["risk"]["regime"] in ("偏 Risk-on", "偏 Risk-off", "中性/分化")
    assert len(p["risk"]["chart"]["series"]) == len(us_etf.RISK_PAIRS)
    assert any("资金流/AUM" in n for n in p["notes"])
    assert p["summary"]["lines"]
    json.dumps(p, allow_nan=False)


def test_missing_symbol_falls_back_to_labeled_snapshot_or_no_data():
    prev = us_etf.build_payload(_prices(), now=NOW)
    p = us_etf.build_payload(_prices(QQQ=None, XLK=None), errors={}, prev=prev, now=NOW)
    rows = {r["symbol"]: r for g in p["groups"] for r in g["items"]}
    assert rows["QQQ"]["snapshot"] is True and rows["QQQ"]["snapshot_date"] == "2026-11-13"
    assert rows["QQQ"]["close"] == {r["symbol"]: r for g in prev["groups"] for r in g["items"]}["QQQ"]["close"]
    p2 = us_etf.build_payload(_prices(QQQ=None), prev=None, now=NOW)
    q = {r["symbol"]: r for g in p2["groups"] for r in g["items"]}["QQQ"]
    assert q["status"] == "missing" and "close" not in q
    # too-old snapshot is not reused
    old = datetime(2026, 12, 20, 16, 0, tzinfo=us_etf.CST)
    p3 = us_etf.build_payload(_prices(QQQ=None), prev=prev, now=old)
    assert {r["symbol"]: r for g in p3["groups"] for r in g["items"]}["QQQ"]["status"] == "missing"


def test_missing_vix_does_not_score():
    p = us_etf.build_payload(_prices(**{"^VIX": None, "^VIX3M": None}), now=NOW)
    assert p["risk"]["vix"]["status"] == "missing"
    assert p["risk"]["max_score"] == len(us_etf.RISK_PAIRS)


def test_intraday_flag():
    et_mid = datetime(2026, 11, 13, 12, 0, tzinfo=us_etf.ET)
    assert us_etf._is_intraday("2026-11-13", et_mid)
    assert not us_etf._is_intraday("2026-11-13", datetime(2026, 11, 13, 17, 0, tzinfo=us_etf.ET))
    assert not us_etf._is_intraday("2026-11-12", et_mid)


def test_refresh_failure_returns_previous_snapshot(monkeypatch, tmp_path):
    seed = us_etf.build_payload(_prices(), now=NOW)
    a = tmp_path / "a.json"
    a.write_text(json.dumps(seed), encoding="utf-8")
    monkeypatch.setattr(us_etf, "_cache", None)
    monkeypatch.setattr(us_etf, "SEED_PATHS", (a,))
    monkeypatch.setattr(us_etf, "RUNTIME_PATH", tmp_path / "rt.json")

    def boom():
        raise RuntimeError("yahoo down")

    out = us_etf.refresh({"yahoo": boom}, use_remote=False, now=NOW)
    assert out["from_snapshot"] is True and "yahoo" in out["errors"] and out["asof"] == "2026-11-13"
    monkeypatch.setattr(us_etf, "SEED_PATHS", (tmp_path / "none.json",))
    out2 = us_etf.refresh({"yahoo": boom}, use_remote=False, now=NOW)
    assert out2["status"] == "no_data"
    ok = us_etf.refresh({"yahoo": lambda: _prices()}, use_remote=False, now=NOW)
    assert ok["status"] == "ok" and (tmp_path / "rt.json").exists()


def test_snapshot_and_needs_refresh(monkeypatch, tmp_path):
    monkeypatch.setattr(us_etf, "_cache", None)
    monkeypatch.setattr(us_etf, "RUNTIME_PATH", tmp_path / "none.json")
    monkeypatch.setattr(us_etf, "SEED_PATHS", (tmp_path / "x.json",))
    s = us_etf.snapshot()
    assert s["status"] == "no_data" and us_etf.needs_refresh(s)
    a = tmp_path / "a.json"
    a.write_text(json.dumps({"status": "ok", "updated_at": "2026-11-14T15:00:00+08:00"}), encoding="utf-8")
    monkeypatch.setattr(us_etf, "SEED_PATHS", (a,))
    monkeypatch.setattr(us_etf, "_now", lambda: NOW)
    s = us_etf.snapshot()
    assert s["age_hours"] == 1.0 and not us_etf.needs_refresh(s)
    monkeypatch.setattr(us_etf, "_now", lambda: datetime(2026, 11, 14, 20, 0, tzinfo=us_etf.CST))
    assert us_etf.needs_refresh(us_etf.snapshot())


def test_seed_files_valid_and_identical():
    a = (ROOT / "data" / "us_etf.json").read_bytes()
    b = (ROOT / "docs" / "api" / "us_etf.json").read_bytes()
    assert a == b
    p = json.loads(a)
    assert p["status"] == "ok" and p["groups"] and p["risk"]["pairs"]
    json.dumps(p, allow_nan=False)


def test_endpoint_serves_seed_without_blocking(monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    import app as app_mod

    calls = []
    monkeypatch.setattr(us_etf, "snapshot", lambda: {"status": "ok", "updated_at": "2020-01-01T00:00:00+08:00", "groups": []})
    monkeypatch.setattr(app_mod, "_spawn_bg_refresh", lambda key, fn, **kw: calls.append((key, kw)) or "started")
    c = app_mod.app.test_client()
    r = c.get("/api/us-etf")
    assert r.status_code == 200 and r.get_json()["refresh_status"] == "started"
    assert calls[-1][0] == "us-etf" and calls[-1][1]["provider"] == "yahoo"
    r = c.get("/api/us-etf/refresh")
    assert r.status_code == 200 and r.get_json()["refresh_status"] == "started"


def test_frontend_and_static_wiring():
    tpl = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    assert '<button type="button" data-view="usetf" class="tab-usetf">美股ETF</button>' in tpl
    assert 'id="view-usetf"' in tpl and "/static/us_etf_view.js?v={{ asset_v }}" in tpl
    docs = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    assert f"./static/us_etf_view.js?v={ASSET_V}" in docs
    js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    assert "'/api/us-etf': './api/us_etf.json'" in js and 'name === "usetf"' in js
    assert (ROOT / "static" / "us_etf_view.js").read_bytes() == (ROOT / "docs" / "static" / "us_etf_view.js").read_bytes()
    exp = (ROOT / "export_static_pages.py").read_text(encoding="utf-8")
    assert 'API / "us_etf.json"' in exp
    assert '"us_etf.py"' in (ROOT / ".github" / "workflows" / "refresh_pages.yml").read_text(encoding="utf-8")
    assert "data/us_etf_latest.json" in (ROOT / ".gitignore").read_text(encoding="utf-8")
    src = (ROOT / "us_etf.py").read_text(encoding="utf-8")
    assert "push2" not in src and "eastmoney" not in src.lower()
