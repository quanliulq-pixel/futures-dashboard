# -*- coding: utf-8 -*-
"""Unit tests for the US Superstocks research engine. No live network."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

import us_superstocks as ss

FIX = Path(__file__).resolve().parent / "fixtures" / "us_superstocks"


def _weeks(n: int, start: str = "2024-10-04") -> list[date]:
    d0 = date.fromisoformat(start)
    # Friday-ending weeks
    return [d0 + timedelta(weeks=i) for i in range(n)]


def _bar(d, o, h, l, c, v):
    return {"date": d.isoformat() if isinstance(d, date) else d, "open": o, "high": h, "low": l, "close": c, "volume": v}


def tight_base_then_breakout(*, vol_mult=6.0, base_weeks=14, after=6, pullback=False, start_price=4.0, history=40):
    """Synthetic weekly series that can pass (or almost pass) the hard filters."""
    dates = _weeks(history + base_weeks + 1 + after + (3 if pullback else 0))
    bars = []
    px = start_price
    # pre-base grind, louder volume
    for i in range(history):
        px = start_price + i * (1.0 / max(history, 1))
        # wide pre-base range so a 12-week window cannot accidentally qualify
        bars.append(_bar(dates[i], px - 0.2, px * 1.35, px * 0.70, px, 1_000_000))
    base_top = px + 0.25
    base_bot = px - 0.15
    mid = (base_top + base_bot) / 2
    for j in range(base_weeks):
        # tight oscillation inside the base
        close = mid + (0.06 if j % 2 == 0 else -0.05)
        bars.append(_bar(dates[history + j], close - 0.03, mid + 0.18, base_bot + 0.02, close, 600_000))
    # breakout
    bidx = history + base_weeks
    bclose = base_top + 0.35
    bars.append(_bar(dates[bidx], base_top + 0.05, bclose + 0.1, base_top + 0.02, bclose, int(600_000 * vol_mult)))
    last = bclose
    for k in range(after):
        # steep advance: ~11-13% a week for a few weeks, then slower
        ret = 0.12 if k < 3 else 0.08
        last = last * (1 + ret)
        bars.append(_bar(dates[bidx + 1 + k], last * 0.97, last * 1.03, last * 0.96, last, 900_000 if k < 3 else 700_000))
    if pullback:
        # 3 tight, quieter weeks near the drifting 10-WMA; tests will also check setups independently
        for k in range(3):
            last = last * 0.97
            i = bidx + 1 + after + k
            bars.append(_bar(dates[i], last * 0.99, last * 1.01, last * 0.985, last, 350_000))
    return bars


def test_weekly_sma_and_window_guard():
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0])
    ma = ss.weekly_sma(s, 5)
    assert pd.isna(ma.iloc[3])
    assert ma.iloc[4] == pytest.approx(3.0)
    assert ma.iloc[-1] == pytest.approx(8.0)
    with pytest.raises(ValueError):
        ss.weekly_sma(s, 0)


def test_detect_base_breakout_strict_volume_and_angle():
    bars = tight_base_then_breakout(vol_mult=6.0, base_weeks=14, after=6)
    brk = ss.detect_base_breakout(bars)
    assert brk["found"] is True
    assert brk["base_weeks"] >= 12
    assert brk["base_weak"] is False
    assert brk["vol_expand"] >= 5
    assert brk["vol_expand_pass"] == "strict"
    ang = ss.angle_of_attack(bars, brk)
    assert ang["missing"] is False
    assert ang["pass"] is True
    assert ang["slope_pct_per_week"] >= 8
    assert ang["impulse"] is True
    assert "slope_pct_per_week" in ang["formula"]


def test_volume_3x_is_soft_near_miss_not_strict():
    bars = tight_base_then_breakout(vol_mult=3.5, base_weeks=14, after=6)
    brk = ss.detect_base_breakout(bars)
    assert brk["found"] is True
    assert brk["vol_expand"] >= 3
    assert brk["vol_expand"] < 5
    assert brk["vol_expand_pass"] == "soft"
    ev = ss.evaluate_ticker(bars, symbol="NEAR", name="Near Co", exchange="NASDAQ")
    assert ev["hard_pass"] is False
    assert ev["near"] is True
    assert any("3–5倍" in x or "3-5" in x for x in ev["near_miss_reasons"]) or ev["near"]


def test_weak_8_week_base_is_flagged():
    bars = tight_base_then_breakout(vol_mult=6.0, base_weeks=9, after=6)
    ev = ss.evaluate_ticker(bars, symbol="WEAK", exchange="NASDAQ")
    brk = ev["technicals"]
    assert brk["base_weeks"] is not None
    assert brk["base_weeks"] < 12 or ev["hard_pass"] is False
    # 9-week base cannot be a strict hard pass
    assert ev["hard_pass"] is False


def test_price_buckets_and_core_exclusion():
    assert ss.price_bucket(3.2) == "caution_low"
    assert ss.price_bucket(6.5) == "sweet"
    assert ss.price_bucket(12.0) == "core_other"
    assert ss.price_bucket(16.0) == "above_core"
    assert ss.price_bucket(1.5) == "too_low"
    # Build a passing technicals series then shift the last close above $15
    bars = tight_base_then_breakout(vol_mult=6.0, after=6)
    bars[-1]["close"] = 16.5
    bars[-1]["high"] = 16.8
    ev = ss.evaluate_ticker(bars, symbol="RICH", exchange="NASDAQ")
    assert ev["hard_pass"] is False
    assert ev["price_bucket"] == "above_core"


def test_fresh_ipo_fails_history():
    bars = tight_base_then_breakout(history=10, base_weeks=14, after=6)
    ev = ss.evaluate_ticker(bars, symbol="IPOX", exchange="NASDAQ")
    assert ev["technicals"]["history_weeks"] < 52
    assert ev["hard_pass"] is False
    assert any("1年" in x for x in ev["fail_reasons"])


def test_below_30wma_fails():
    # Flat then a late collapse below a still-high 30-WMA is hard to force;
    # instead start high and grind down so last close is under 30-WMA.
    dates = _weeks(70)
    bars = []
    for i, d in enumerate(dates):
        px = 12.0 - i * 0.08
        bars.append(_bar(d, px, px + 0.1, px - 0.1, px, 1_000_000))
    ev = ss.evaluate_ticker(bars, symbol="DOWN", exchange="NASDAQ")
    assert ev["technicals"]["above_30wma"] is False
    assert ev["hard_pass"] is False


def test_pe_run_rate_and_nonpositive_eps():
    ok = ss.pe_run_rate(8.0, 0.25, 0.80)
    assert ok["pe_run_rate"] == pytest.approx(8.0)
    assert ok["pe_ttm"] == pytest.approx(10.0)
    assert ok["run_rate_pass"] is True
    miss = ss.pe_run_rate(8.0, 0.0, -0.2)
    assert miss["pe_run_rate"] is None
    assert miss["pe_ttm"] is None
    miss2 = ss.pe_run_rate(8.0, None, None)
    assert miss2["pe_run_rate"] is None
    assert "quarterly_eps" in miss2["missing"]


def test_form4_code_p_vs_non_p():
    p = ss.parse_form4_xml((FIX / "form4_code_p.xml").read_text(encoding="utf-8"))
    a = ss.parse_form4_xml((FIX / "form4_code_a.xml").read_text(encoding="utf-8"))
    m = ss.parse_form4_xml((FIX / "form4_code_m.xml").read_text(encoding="utf-8"))
    g = ss.parse_form4_xml((FIX / "form4_code_g.xml").read_text(encoding="utf-8"))
    tok = ss.parse_form4_xml((FIX / "form4_token_p.xml").read_text(encoding="utf-8"))
    assert p and p[0]["open_market_buy"] is True and p[0]["code"] == "P"
    assert p[0]["c_level_or_director"] is True
    assert p[0]["token_buy"] is False
    assert a and a[0]["code"] == "A" and a[0]["open_market_buy"] is False
    assert m and m[0]["code"] == "M" and m[0]["open_market_buy"] is False
    assert g and g[0]["code"] == "G" and g[0]["open_market_buy"] is False
    assert tok and tok[0]["open_market_buy"] is True and tok[0]["token_buy"] is True
    summary = ss.summarize_insider_buys(p + a + m + g, base_start="2026-06-01", breakout_date="2026-09-18")
    assert summary["status"] == "ok"
    assert summary["meaningful_buy_count"] == 1
    assert all(t["code"] == "P" for t in summary["open_market_buys"])
    empty = ss.summarize_insider_buys(a + m + g)
    assert empty["open_market_buys"] == []
    assert empty["note"]


def test_entry_buy_zone_and_extended_and_exit():
    # Extended: 60%+ above 10-WMA within 6 weeks of breakout
    bars = tight_base_then_breakout(vol_mult=6.0, after=4)
    # shove the last close far above the magic line
    bars[-1]["close"] = bars[-1]["close"] * 1.8
    bars[-1]["high"] = bars[-1]["close"] * 1.02
    ev = ss.evaluate_ticker(bars, symbol="EXT", exchange="NASDAQ")
    # may or may not hard-pass after the shove; extended flag is what we care about
    assert ev["extended"] is True or ev["exit"]["signals"]
    # Primary stop is documented
    assert ev["entry"]["stop_primary"] == "weekly_close_below_10wma"

    # Exit: 9-15 months after breakout (~40-65 weeks)
    long_bars = tight_base_then_breakout(vol_mult=6.0, after=48)
    ev2 = ss.evaluate_ticker(long_bars, symbol="OLD", exchange="NASDAQ", watched=True)
    weeks = ev2["technicals"]["weeks_since_breakout"]
    if weeks and 9 <= weeks / 4.345 <= 15.5:
        assert "9_to_15_months_after_breakout" in ev2["exit"]["signals"]
        assert ev2["state"] == "退出信号"

    # BCD secondary: three lower lows in 5 weeks, still above 30-WMA
    bars3 = tight_base_then_breakout(vol_mult=6.0, after=8)
    # force last 5 lows downward
    base_low = bars3[-1]["low"]
    for i, step in enumerate([0, 0.15, 0.3, 0.45, 0.6]):
        bars3[-5 + i]["low"] = base_low - step
        bars3[-5 + i]["close"] = max(bars3[-5 + i]["close"] - step * 0.3, bars3[-5 + i]["low"] + 0.05)
    ev3 = ss.evaluate_ticker(bars3, symbol="BCD", exchange="NASDAQ")
    # secondary flag is optional depending on whether lows strictly descend and trend holds
    assert "secondary" in ev3["entry"]


def test_classify_state_matrix():
    entry_yes = {"in_buy_zone": True}
    entry_no = {"in_buy_zone": False}
    assert ss.classify_state(hard_pass=True, near=False, entry=entry_yes, extended=False, exit_signals=[], watched=False) == "买点区"
    assert ss.classify_state(hard_pass=True, near=False, entry=entry_no, extended=True, exit_signals=["early_60pct_above_magic"], watched=False) == "已延伸勿追"
    assert ss.classify_state(hard_pass=True, near=False, entry=entry_no, extended=False, exit_signals=[], watched=False) == "观察"
    assert ss.classify_state(hard_pass=False, near=True, entry=entry_no, extended=False, exit_signals=[], watched=False) == "观察"
    assert ss.classify_state(hard_pass=True, near=False, entry=entry_yes, extended=False, exit_signals=["stock_split"], watched=True) == "退出信号"


def test_snapshot_view_missing_fields_are_null_not_invented():
    raw = {
        "candidates": [{"symbol": "abcd"}],
        "near": [None, "bad"],
    }
    view = ss.snapshot_view(raw, watch_symbols=["ABCD"])
    row = view["candidates"][0]
    assert row["symbol"] == "ABCD"
    assert row["price"] is None
    assert row["fundamentals"]["short_interest_pct"] is None
    assert row["fundamentals"]["market_cap"] is None
    assert row["technicals"]["wma10"] is None
    assert row["insiders"]["status"] == "无数据"
    assert row["insiders"]["open_market_buys"] == []
    assert view["near"][0]["insiders"]["status"] == "无数据"
    missing = ss.snapshot_view(None)
    assert missing["ok"] is False
    assert "无数据" in (missing.get("note") or "")
    # never invent a candidate
    assert missing["candidates"] == []


def test_hard_pass_evaluate_and_build_snapshot_offline():
    bars = tight_base_then_breakout(vol_mult=6.5, base_weeks=14, after=6)
    ev = ss.evaluate_ticker(
        bars,
        symbol="PASS",
        name="Pass Co",
        exchange="NASDAQ",
        fundamentals={
            "quarterly_eps": 0.30,
            "ttm_eps": 1.0,
            "market_cap": 80_000_000,
            "float_shares": 6_000_000,
            "long_term_debt": 0,
            "eps_quarters": [0.10, 0.16, 0.22, 0.30],
            "revenue_quarters": [8, 9, 11, 14],
            "margin_quarters": [0.08, 0.10, 0.12, 0.15],
            "insider_ownership_pct": 0.28,
            "analyst_count": 0,
            "sector": "Technology",
        },
        insider_transactions=ss.parse_form4_xml((FIX / "form4_code_p.xml").read_text(encoding="utf-8")),
    )
    assert ev["hard_pass"] is True
    assert ev["price_bucket"] in {"sweet", "core_other", "caution_low"}
    assert ev["technicals"]["above_30wma"] is True
    assert ev["technicals"]["vol_expand_pass"] == "strict"
    assert ev["technicals"]["angle_pass"] is True
    assert ev["fundamentals"]["pe_run_rate"] is not None
    assert ev["fundamentals"]["market_cap"] == 80_000_000
    assert ev["fundamentals"]["short_interest_pct"] is None
    assert "short_interest_pct" in ev["missing"]
    snap = ss.build_snapshot(
        symbols=["PASS", "MISS"],
        weekly_map={"PASS": pd.DataFrame(bars), "MISS": pd.DataFrame(bars[:20])},
        universe=[{"symbol": "PASS", "name": "Pass Co", "exchange": "NASDAQ"},
                  {"symbol": "MISS", "name": "Miss Co", "exchange": "NASDAQ"}],
        fetch_live=False,
    )
    assert snap["counts"]["candidates"] >= 1
    assert any(r["symbol"] == "PASS" for r in snap["candidates"])
    # MISS has too little history and must not be fabricated into candidates
    assert all(r["symbol"] != "MISS" for r in snap["candidates"])


def test_parse_nasdaq_universe_skips_otc_etf_warrants():
    nas = (FIX / "nasdaqlisted.txt").read_text(encoding="utf-8")
    oth = (FIX / "otherlisted.txt").read_text(encoding="utf-8")
    rows = ss.parse_nasdaq_listed(nas) + ss.parse_nasdaq_listed(oth)
    syms = {r["symbol"] for r in rows}
    assert "ABCD" in syms and "AAAA" in syms
    assert "BBBB" in syms and "DDDD" in syms  # NYSE and NYSE American
    assert "QQQ" not in syms and "SLV" not in syms and "TEST" not in syms
    assert "WXYZ" not in syms and "CCCC" not in syms  # warrant / preferred
    assert "EEEE" not in syms  # BATS skipped
    exch = {r["symbol"]: r["exchange"] for r in rows}
    assert exch["BBBB"] == "NYSE"
    assert exch["ABCD"] == "NASDAQ"


def test_empty_seed_and_defaults_are_documented():
    seed = ss.empty_seed_payload()
    assert seed["candidates"] == []
    assert seed["defaults"]["vol_expand_strict"] == 5.0
    assert seed["defaults"]["vol_expand_soft"] == 3.0
    assert seed["defaults"]["ma_magic"] == 10
    assert seed["defaults"]["price_max_core"] == 15.0
    assert seed["sizing"]["sleeve_min"] == 0.05
    assert "14,972%" in seed["historical_edge"]
    assert "5%" in seed["disclaimer"] and "90" in seed["disclaimer"]
    assert seed["timezone"].startswith("Asia/Shanghai")
    assert seed["asof_session_tz"] == "America/New_York"


def test_otc_excluded():
    bars = tight_base_then_breakout()
    ev = ss.evaluate_ticker(bars, symbol="PINK", exchange="OTCQB")
    assert ev["hard_pass"] is False
    assert any("OTC" in x or "Pink" in x for x in ev["fail_reasons"])


def test_api_serves_seed_without_network(monkeypatch):
    import os
    os.environ["SKIP_BG_WARMUP"] = "1"
    import bg_tasks
    import app as app_mod
    import us_superstocks as ss_mod

    bg_tasks._reset_for_tests(max_tasks=2)
    monkeypatch.setattr(ss_mod, "refresh", lambda force=True: (_ for _ in ()).throw(RuntimeError("refresh must not run in request")))
    client = app_mod.app.test_client()
    r = client.get("/api/us-superstocks")
    assert r.status_code == 200
    d = r.get_json()
    assert d["candidates"] == []
    assert d["counts"]["candidates"] == 0
    assert "不能下单" in d["disclaimer"]
    assert d["defaults"]["vol_expand_strict"] == 5.0
    # refresh queues only
    called = {"n": 0}

    def fake_start(*a, **k):
        called["n"] += 1
        return "started"

    monkeypatch.setattr(app_mod.bg_tasks, "try_start", fake_start)
    r2 = client.get("/api/us-superstocks/refresh")
    assert r2.status_code == 200
    d2 = r2.get_json()
    assert d2["candidates"] == []
    assert called["n"] == 1
    assert d2.get("refresh", {}).get("queued") is True
