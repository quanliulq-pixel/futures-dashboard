# -*- coding: utf-8 -*-
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import math
import numpy as np
import pandas as pd
import pytest
import gold_put_quant as q

ROOT = Path(__file__).resolve().parents[1]
CST = timezone(timedelta(hours=8))


def panel(n=400, start="2018-01-02"):
    rng = np.random.default_rng(42)
    idx = pd.bdate_range(start, periods=n)
    # mild upward drift so regime filters can pass
    c = 300 * np.exp(np.cumsum(0.0008 + rng.normal(0, 0.008, n)))
    p = pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c * 1.001}, index=idx)
    return p


def test_bs_fut_put_sanity_and_delta():
    price, delta = q.bs_fut_put(100, 100, 1, .2, rate=0.03)
    # Black-76 ATM put is discounted; should be close to disc * equity BS with q=r
    assert price > 0
    assert -1 < delta < 0
    assert q.bs_fut_put(80, 100, 0, .2, rate=0) == (20, -1)
    assert q.bs_fut_put(120, 100, 0, .2, rate=0) == (0, 0)
    assert q.bs_fut_put(100, 100, 1, 0, rate=0)[0] == 0
    assert q.bs_fut_put(100, 110, 1, .4)[0] > q.bs_fut_put(100, 110, 1, .2)[0]
    with pytest.raises(ValueError):
        q.bs_fut_put(-1, 100, 1, .2)


def test_implied_vol_roundtrip_and_no_invent():
    fut, k, t, vol = 800, 760, 30 / 365, 0.22
    mid, _ = q.bs_fut_put(fut, k, t, vol, 0.03)
    iv = q.implied_put_vol(fut, k, t, mid, 0.03)
    assert iv == pytest.approx(vol, rel=1e-3)
    # mid below discounted intrinsic → None, never invent
    assert q.implied_put_vol(800, 900, t, 0.01, 0.03) is None
    assert q.implied_put_vol(800, 760, t, -1, 0.03) is None


def test_strike_margin_and_risk_size():
    k = q.strike_model(800, .2)
    assert 0 < k < 800
    assert q.strike_model(800, .4) < k
    # official-style margin: premium*1000 + max(fut_margin - 0.5*otm, 0.5*fut_margin)
    m = q.seller_margin(800, 760, 10, fut_rate=0.10)
    fut = 800 * 1000 * 0.10
    otm = (800 - 760) * 1000
    prem = 10 * 1000
    assert m == pytest.approx(max(prem + fut - 0.5 * otm, prem + 0.5 * fut))
    w = q.collateral_weight(800, 760, 10)
    assert 0 < w <= 0.20
    margin = q.seller_margin(800, 760, 10)
    risk = ((2.5 - 1) * 10 + 2 * q.unit_fee(10)) * 1000 / margin
    assert w * risk <= 0.005 + 1e-12


def test_option_expiry_fifth_last_weekday():
    # au2701 → futures Jan 2027 → option expiry = 5th last session of Dec 2026
    exp = q.option_expiry("au2701")
    assert exp == date(2026, 12, 25)
    assert q.option_expiry("au2612") == date(2026, 11, 24)
    assert q.option_expiry("bad") is None


def test_exit_rules():
    base = dict(mark=1, credit=2, spot=800, strike=760, dte=25, regime_ok=True)
    assert q.exit_reason(**base) == "take_profit"
    assert q.exit_reason(**{**base, "mark": 5}) == "premium_stop"
    assert q.exit_reason(**{**base, "mark": 2, "spot": 750}) == "strike_breach"
    assert q.exit_reason(**{**base, "mark": 2, "dte": 7}) == "expiry_guard"
    assert q.exit_reason(**{**base, "mark": 2, "dte": 0}) == "expiry"
    assert q.exit_reason(**{**base, "mark": 2, "regime_ok": False}) == "regime_exit"
    assert q.exit_reason(**{**base, "mark": 2}) is None


def test_regime_uses_trend_and_ma():
    p = q.features(panel())
    r = p.iloc[-1]
    # fixture has upward drift; may or may not pass T>0 and MA stack. Force a pass then break.
    r = r.copy()
    r["close"], r["ma20"], r["ma60"], r["mom"], r["hv"], r["hv60"], r["T"] = 800, 790, 780, 0.02, 0.15, 0.14, 0.4
    assert q.eligible(r)[0]
    r["T"] = 0
    assert not q.eligible(r)[0]
    r["T"] = 0.4
    r["mom"] = -0.01
    assert not q.eligible(r)[0]
    r["mom"] = 0.02
    r["hv"] = 0.41
    assert not q.eligible(r)[0]
    r["hv"] = 0.30
    r["hv60"] = 0.10
    assert not q.eligible(r)[0]


def test_no_lookahead_features_and_equity():
    raw = panel()
    p = q.features(raw)
    other = raw.copy()
    other.iloc[300:] *= 1.2
    b = q.features(other)
    pd.testing.assert_frame_equal(p.iloc[:300], b.iloc[:300])
    start, end = str(p.index[260].date()), str(p.index[-1].date())
    a, z = q.backtest(p, start, end), q.backtest(b, start, end)
    assert a["metrics"]["strategy"]["trade_count"] >= 0
    # curve before the mutated region must match
    cut = str(p.index[300].date())
    assert [x for x in a["curve"] if x["date"] < cut] == [x for x in z["curve"] if x["date"] < cut]
    assert [t for t in a["trades"] if t["exit"] < cut] == [t for t in z["trades"] if t["exit"] < cut]


def test_no_same_day_reentry_and_worst_trade():
    p = q.features(panel(n=500))
    d = q.backtest(p, str(p.index[260].date()), str(p.index[-1].date()))
    trades = d["trades"]
    for a, b in zip(trades, trades[1:]):
        assert a["exit"] < b["entry"]
    if trades:
        assert d["metrics"]["strategy"]["worst_trade"] == min(t["collateral_return"] for t in trades)


def _puts(spot=800, today=date(2026, 11, 18), expiry=date(2026, 12, 25), iv=0.22, oi=50):
    years = (expiry - today).days / 365
    k = 760
    mid, _ = q.bs_fut_put(spot, k, years, iv, 0.03)
    bid, ask = mid * 0.97, mid * 1.03
    return [dict(contract="au2701P760", strike=k, bid=bid, ask=ask, mid=(bid + ask) / 2,
                 last=mid, open_interest=oi)]


def test_real_chain_selection_and_no_synthetic_fallback():
    today, expiry = date(2026, 11, 18), date(2026, 12, 25)
    rows = _puts()
    selected = q.select_contract(rows, 800, 0.15, expiry, today)
    assert selected and selected["strike"] == 760 and selected["bid"] > 0
    assert -0.30 <= selected["delta"] <= -0.20
    assert selected["margin_per_lot"] > 0
    assert selected["annualized_yield"] is not None
    # bid=0, wide spread, low OI, stale DTE, low IV vs HV
    for change in [
        dict(bid=0, ask=1, mid=0.5),
        dict(bid=1, ask=2, mid=1.5),  # 67% spread
        dict(open_interest=2),
    ]:
        bad = [{**rows[0], **change}]
        if "bid" in change and change["bid"] == 1:
            bad[0]["ask"] = 2
            bad[0]["mid"] = 1.5
        assert q.select_contract(bad, 800, 0.15, expiry, today) is None
    assert q.select_contract(rows, 800, 0.15, today + timedelta(days=10), today) is None
    assert q.select_contract([], 800, 0.15, expiry, today) is None
    # IV below 1.10 * HV
    cheap = _puts(iv=0.10)
    assert q.select_contract(cheap, 800, 0.15, expiry, today) is None


def test_chain_error_is_no_data_not_bs_quote():
    class Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("unavailable")

    live = q.fetch_live(panel(), http_get=Boom)
    assert all(x["action"] == "不卖" and x["quote"] is None for x in live)
    assert all("无数据" in x["chain_status"] for x in live)


def test_endpoint_shape_and_nonblocking(monkeypatch):
    monkeypatch.setenv("SKIP_BG_WARMUP", "1")
    import app
    seed = {
        "schema_version": 1, "status": "ok", "updated_at": "2026-11-18T10:00:00+08:00",
        "label": "模型定价回测，非真实期权成交", "disclaimer": "研究参考，不能下单，不构成投资建议",
        "asof_session": "2026-11-17", "live": [],
        "in_sample": {"start": "2020-01-02", "end": "2023-12-29",
                      "metrics": {"strategy": {"assignment_count": 0}, "AU0": {}}},
        "out_of_sample": {"start": "2024-01-02", "end": "2026-11-17",
                          "metrics": {"strategy": {"assignment_count": 0}, "AU0": {}}},
    }
    monkeypatch.setattr(q, "snapshot", lambda: seed.copy())
    monkeypatch.setattr(q, "needs_refresh", lambda _: True)
    calls = []
    monkeypatch.setattr(app, "_spawn_bg_refresh", lambda *a, **kw: calls.append((a, kw)) or "queued")
    monkeypatch.setattr(q, "refresh", lambda **kw: pytest.fail("network in request"))
    d = app.app.test_client().get("/api/gold/put").get_json()
    assert d["refresh_status"] == "queued" and "模型定价回测" in d["label"]
    assert set(d["in_sample"]["metrics"]) == {"strategy", "AU0"}
    assert "assignment_count" in d["out_of_sample"]["metrics"]["strategy"]
    assert calls[0][0][0] == "gold-put"
    assert "研究参考，不能下单" in d["disclaimer"]


def test_stale_chain_and_no_data(monkeypatch, tmp_path):
    value = {
        "schema_version": 1, "status": "ok", "updated_at": "2000-01-01T00:00:00+08:00",
        "live": [{"action": "不卖", "quote": {"strike": 1}, "chain_sample": [1], "chain_status": "ok"}],
    }
    monkeypatch.setattr(q, "_cache", value)
    monkeypatch.setattr(q, "RUNTIME_PATH", tmp_path / "none")
    monkeypatch.setattr(q, "SEED_PATHS", (tmp_path / "none",))
    d = q.snapshot()
    assert d["stale"] and all(x["action"] == "不卖" and x["quote"] is None for x in d["live"])
    monkeypatch.setattr(q, "_cache", None)
    assert q.snapshot()["status"] == "no_data"


def test_parse_helpers_and_months():
    html = '<li data-value="au2612"></li><li data-value="au2701"></li>'
    assert q.list_months(html) == ["au2612", "au2701"]
    puts = q.parse_put_row(["1", "2.4", "2.6", "2.7", "1", "32", 1.1, "au2701P656"])
    assert puts["strike"] == 656 and puts["bid"] == 2.4 and puts["open_interest"] == 32
    text = 'var hq_str_nf_AU2701="黄金2701,103107,831,838,817,0,819,820,820.08,0,833.24,4,1,1,1,沪,黄金,2026-11-18,0";'
    hq = q.parse_nf_last(text)
    assert hq["AU2701"]["last"] == pytest.approx(820.08)
    assert hq["AU2701"]["asof"] == "2026-11-18"


def test_mirrors_tabs_and_no_restored_put_tab():
    for f in ("templates/index.html", "docs/index.html"):
        text = (ROOT / f).read_text(encoding="utf-8")
        assert "gold-put-content" in text and "gold_put_view.js" in text
        nav = text.split('id="view-tabs"', 1)[1].split("</nav>", 1)[0]
        assert ">卖Put<" not in nav and ">卖Call<" not in nav
        assert 'data-view="put"' not in nav
    assert (ROOT / "static" / "gold_put_view.js").read_bytes() == (ROOT / "docs" / "static" / "gold_put_view.js").read_bytes()
    # no push2 in the new module
    src = (ROOT / "gold_put_quant.py").read_text(encoding="utf-8")
    assert "push2" not in src.lower() or "不准" in src or "不用" in src
    assert "push2his" not in src
    assert "push2." not in src and "push2his" not in src
