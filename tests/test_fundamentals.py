# -*- coding: utf-8 -*-
"""Offline tests for fundamental scoring (CN futures + US stocks) and combined ranking."""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("SKIP_BG_WARMUP", "1")

import numpy as np
import pandas as pd
import pytest

import cn_option_candidates as coc
import fundamentals as fu

ROOT = Path(__file__).resolve().parents[1]


# ------------------------------------------------------------------ fixtures

def _inv_df(values, start="2026-08-03"):
    idx = pd.bdate_range(start, periods=len(values))
    return pd.DataFrame({"日期": idx.date, "库存": values, "增减": [None] + list(np.diff(values))})


def _season_series(years=12, up=True):
    idx = pd.bdate_range(end="2026-11-09", periods=260 * years)
    t = np.arange(len(idx))
    doy = idx.day_of_year.values
    # rising strongly in Nov–Dec when up=True, falling otherwise
    seasonal = np.where((doy >= 300) & (doy <= 350), 0.004 if up else -0.004, 0.0)
    px = 100 * np.cumprod(1 + seasonal + 0.0 * t)
    return pd.Series(px, index=idx)


# ------------------------------------------------------------------ CN factor scoring

def test_inventory_falling_is_bullish_rising_is_bearish():
    falling = fu.cn_inventory_factor(_inv_df([1000] * 10 + list(np.linspace(1000, 700, 21))))
    rising = fu.cn_inventory_factor(_inv_df([1000] * 10 + list(np.linspace(1000, 1300, 21))))
    assert falling["status"] == "ok" and falling["signal"] == pytest.approx(1.0)
    assert rising["signal"] == pytest.approx(-1.0)
    assert falling["asof"] and "去化" in falling["value_text"]
    assert falling["source_url"].startswith("https://")


def test_inventory_missing_or_short_is_no_data_not_imputed():
    assert fu.cn_inventory_factor(None)["status"] == "missing"
    short = fu.cn_inventory_factor(_inv_df([1, 2, 3]))
    assert short["status"] == "missing" and short["signal"] is None and short["value"] is None
    zero_base = fu.cn_inventory_factor(_inv_df([0] * 21 + [50]))
    assert zero_base["status"] == "missing"


def test_basis_spot_premium_bullish_and_discount_bearish():
    prem = fu.cn_basis_factor({"date": "20261109", "spot_price": 105, "dominant_contract_price": 100,
                               "dominant_contract": "cu2612"})
    disc = fu.cn_basis_factor({"date": "20261109", "spot_price": 99, "dominant_contract_price": 100})
    assert prem["signal"] == pytest.approx(1.0) and prem["asof"] == "2026-11-09"
    assert disc["signal"] == pytest.approx(-0.2)
    assert fu.cn_basis_factor(None)["status"] == "missing"
    assert fu.cn_basis_factor({"spot_price": 0, "dominant_contract_price": 100})["status"] == "missing"
    big = fu.cn_basis_factor({"date": "20261109", "spot_price": 120, "dominant_contract_price": 100})
    assert "请核对" in big["value_text"]


def test_term_structure_uses_top_two_open_interest_contracts():
    recs = [
        {"symbol": "MA2701", "presettlement": 3247, "position": 900000, "tradedate": "2026-11-09"},
        {"symbol": "MA2703", "presettlement": 3008, "position": 350000, "tradedate": "2026-11-09"},
        {"symbol": "MA2611", "presettlement": 9999, "position": 10, "tradedate": "2026-11-09"},
    ]
    f = fu.cn_term_factor(recs, "2026-11-09")
    assert f["status"] == "ok" and f["signal"] == pytest.approx(1.0)  # ~ +3.97%/month backwardation
    assert "MA2701" in f["value_text"] and "MA2611" not in f["value_text"]
    contango = fu.cn_term_factor([
        {"symbol": "V2701", "presettlement": 4952, "position": 9},
        {"symbol": "V2705", "presettlement": 5143, "position": 8},
    ])
    assert contango["signal"] < -0.9
    assert fu.cn_term_factor([{"symbol": "X2701", "presettlement": 1, "position": 1}])["status"] == "missing"


def test_seasonality_sign_and_min_years():
    up = fu.cn_seasonality_factor(_season_series(up=True))
    down = fu.cn_seasonality_factor(_season_series(up=False))
    assert up["status"] == "ok" and up["signal"] > 0.5
    assert down["signal"] < -0.5
    short = fu.cn_seasonality_factor(_season_series(years=3))
    assert short["status"] == "missing"


# ------------------------------------------------------------------ weighting / combining

def _ok(key, sig):
    return fu.factor(key, key, value=sig, value_text="x", asof="2026-11-09", signal=sig)


def test_reweighting_over_available_factors_and_side_sign():
    facs = [_ok("inventory", 1.0), fu.factor("basis", "basis", reason="none"), _ok("term", 0.0),
            fu.factor("seasonality", "s", reason="none")]
    put = fu.score_factors(facs, fu.CN_FACTOR_WEIGHTS, "put")
    call = fu.score_factors(facs, fu.CN_FACTOR_WEIGHTS, "call")
    w_inv = 0.30 / (0.30 + 0.25)
    assert put["fund_score"] == pytest.approx(50 + 50 * w_inv, abs=0.06)
    assert call["fund_score"] == pytest.approx(50 - 50 * w_inv, abs=0.06)
    assert put["fund_status"] == "partial" and set(put["fund_missing"]) == {"basis", "s"}
    contribs = [f["contribution"] for f in put["fund_factors"] if f["contribution"] is not None]
    assert sum(contribs) == pytest.approx(put["fund_score"] - 50, abs=0.2)
    missing = [f for f in put["fund_factors"] if f["status"] == "missing"]
    assert all(f["signal_text"] == "无数据" and f["value"] is None for f in missing)


def test_all_missing_gives_none_and_combined_equals_tech():
    facs = [fu.factor(k, k, reason="none") for k in fu.CN_FACTOR_WEIGHTS]
    res = fu.score_factors(facs, fu.CN_FACTOR_WEIGHTS, "put")
    assert res["fund_score"] is None and res["fund_status_cn"] == "基本面无数据"
    row = fu.apply_fund_to_row({"score": 33.3}, res)
    assert row["combined_score"] == 33.3 and row["combined_basis"] == "tech_only"
    assert row["tech_score"] == 33.3 and row["score"] == 33.3


def test_combined_formula_and_clipping():
    assert fu.combine_scores(40, 80)["combined_score"] == pytest.approx(0.6 * 40 + 0.4 * 80)
    assert fu.combine_scores(-10, 50)["combined_score"] == pytest.approx(0.4 * 50)
    assert fu.combine_scores(140, 50)["combined_score"] == pytest.approx(0.6 * 100 + 20)
    assert fu.TECH_WEIGHT + fu.FUND_WEIGHT == pytest.approx(1.0)


# ------------------------------------------------------------------ CN build integration

class _FakeCnProvider:
    def __init__(self, snap):
        self.snap = snap
        self.calls = 0

    def snapshot(self, symbols):
        self.calls += 1
        return self.snap


def _synthetic_close(n=300, seed=1, drift=-0.001):
    rng = np.random.default_rng(seed)
    px = 100 * np.cumprod(1 + drift + 0.03 * rng.standard_normal(n))
    return pd.Series(px, index=pd.bdate_range("2025-06-01", periods=n))


def test_cn_build_gate_on_tech_rank_by_combined():
    series = {"CU0": _synthetic_close(seed=1), "AU0": _synthetic_close(seed=1)}

    def loader(iid):
        s = series.get(iid.removeprefix("CN:"), _synthetic_close(seed=1))
        return pd.DataFrame({"date": s.index, "close": s.values})

    bull = _inv_df([1000] * 10 + list(np.linspace(1000, 600, 21)))
    snap = {
        "inventory": {"AU0": bull},
        "spot": {"AU": {"date": "20261109", "spot_price": 110, "dominant_contract_price": 100}},
        "term": {},
        "errors": {"inventory:CU0": "TypeError: boom"},
    }
    out = coc.build_cn_direction_candidates("put", history_loader=loader, min_score=0, top_n=50,
                                            fund_provider=_FakeCnProvider(snap))
    rows = {r["symbol"]: r for r in out["all"]}
    # identical price history ⇒ identical tech score; fundamentals break the tie
    assert rows["AU0"]["tech_score"] == rows["CU0"]["tech_score"]
    assert rows["AU0"]["combined_score"] > rows["CU0"]["combined_score"]
    order = [r["symbol"] for r in out["all"]]
    assert order.index("AU0") < order.index("CU0")
    cu_inv = [f for f in rows["CU0"]["fund_factors"] if f["key"] == "inventory"][0]
    assert cu_inv["status"] == "missing" and "取数失败" in cu_inv["reason"]
    # still direction-only, no option fields invented
    assert rows["AU0"]["put"] is None and rows["AU0"]["call"] is None
    assert out["fund_meta"]["weights"] == fu.CN_FACTOR_WEIGHTS

    # threshold gate stays on the technical score
    hi = coc.build_cn_direction_candidates("put", history_loader=loader, min_score=1e6,
                                           fund_provider=_FakeCnProvider(snap))
    assert hi["candidates"] == []


def test_cn_build_provider_error_is_non_fatal():
    class Boom:
        def snapshot(self, symbols):
            raise RuntimeError("network down")

    def loader(iid):
        s = _synthetic_close(seed=3)
        return pd.DataFrame({"date": s.index, "close": s.values})

    out = coc.build_cn_direction_candidates("call", history_loader=loader, min_score=0, fund_provider=Boom())
    assert out["fund_meta"]["status"] == "error"
    assert out["all"], "rows survive a fundamentals failure"
    for r in out["all"]:
        assert r["combined_score"] is not None
        inv = [f for f in r["fund_factors"] if f["key"] == "inventory"][0]
        assert inv["status"] == "missing" and "network down" in inv["reason"]


def test_cn_disabled_provider_marks_not_computed():
    def loader(iid):
        s = _synthetic_close(seed=5)
        return pd.DataFrame({"date": s.index, "close": s.values})

    out = coc.build_cn_direction_candidates("put", history_loader=loader, min_score=0, fund_provider=False)
    for r in out["all"]:
        assert r["fund_score"] is None and r["fund_status"] == "not_computed"
        assert r["combined_score"] == max(0.0, min(100.0, r["score"]))


def test_old_seed_rows_are_not_backfilled():
    seed = {"picks": [], "cn_candidates": [{"symbol": "MA0", "score": 36.7, "kind": "cn_fut",
                                            "market": "cn", "scanned_at": "2026-11-09T18:25:00+08:00"}],
            "cn_candidate_meta": {"status": "ok"}}
    out = coc.ensure_cn_candidate_fields(seed, side="call")
    row = out["cn_candidates"][0]
    for k in ("fund_score", "combined_score", "tech_score", "fund_factors"):
        assert k not in row


# ------------------------------------------------------------------ US factor scoring

INFO_GOOD = {"quoteType": "EQUITY", "forwardPE": 11.7, "trailingPE": 23.6, "profitMargins": 0.63,
             "returnOnEquity": 1.17, "freeCashflow": 4e10, "debtToEquity": 17.0, "revenueGrowth": 1.06,
             "earningsGrowth": 1.28, "recommendationMean": 1.29, "targetMeanPrice": 327.0,
             "numberOfAnalystOpinions": 58, "currentPrice": 186.6, "mostRecentQuarter": 1785024000}
INFO_BAD = {"quoteType": "EQUITY", "forwardPE": -7.7, "trailingPE": None, "profitMargins": -0.46,
            "returnOnEquity": -0.55, "freeCashflow": -2.2e9, "debtToEquity": 250.0, "revenueGrowth": -0.2,
            "earningsGrowth": None, "recommendationMean": 3.4, "targetMeanPrice": 10.0,
            "numberOfAnalystOpinions": 25, "currentPrice": 13.6}


def test_us_factor_signals_good_vs_bad():
    good = {f["key"]: f for f in fu.us_factors_from_info(INFO_GOOD, "NVDA", "2026-11-09")}
    bad = {f["key"]: f for f in fu.us_factors_from_info(INFO_BAD, "RIVN", "2026-11-09")}
    for k in ("valuation", "quality", "growth", "analyst"):
        assert good[k]["status"] == "ok" and bad[k]["status"] == "ok", k
        assert good[k]["signal"] > bad[k]["signal"], k
    assert good["quality"]["asof"].startswith("最近财季 2026-")
    assert good["valuation"]["source_url"] == "https://finance.yahoo.com/quote/NVDA/key-statistics"
    assert "亏损" in bad["valuation"]["value_text"]


def test_us_factor_missing_fields_are_no_data():
    facs = {f["key"]: f for f in fu.us_factors_from_info({"numberOfAnalystOpinions": 1}, "X", "2026-11-09")}
    assert all(f["status"] == "missing" for f in facs.values())
    # absurd forward PE (e.g. ETF -4096) is not treated as a valuation
    v = fu.us_valuation_factor({"forwardPE": -4096.0}, "TLT", "2026-11-09")
    assert v["status"] == "ok" and v["signal"] == pytest.approx(-0.6) and "亏损" in v["value_text"]


def test_earnings_window_flag():
    ew = fu.earnings_window({"date": "2026-11-18", "days": 9}, {"dte": 30, "expiry": "2026-12-09"})
    assert ew["in_window"] and "2026-11-18" in ew["text"]
    out = fu.earnings_window({"date": "2027-01-20", "days": 72}, {"dte": 30, "expiry": "2026-12-09"})
    assert not out["in_window"]
    no_opt = fu.earnings_window({"date": "2026-12-10", "days": 31}, None)
    assert no_opt["in_window"] and no_opt["window_days"] == fu.EARNINGS_DEFAULT_WINDOW_DAYS
    assert fu.earnings_window(None, {"dte": 30}) is None


class _FakeUs:
    def __init__(self, infos):
        self.infos = infos
        self.requested = []

    def batch(self, symbols):
        self.requested.extend(symbols)
        return {s: (self.infos.get(s), "2026-11-09", None if s in self.infos else "HTTPError") for s in symbols}


def _us_row(sym, score, kind="stock", put=True, earn_days=None):
    return {"symbol": sym, "kind": kind, "score": score, "price": 100.0,
            "put": {"dte": 30, "expiry": "2026-12-09"} if put else None,
            "earnings": {"date": "2026-11-20", "days": earn_days} if earn_days is not None else None}


def test_us_apply_top_n_cap_etf_and_earnings_penalty():
    rows = [_us_row("GOOD", 40), _us_row("BAD", 41), _us_row("EARN", 39, earn_days=11),
            _us_row("SPY", 45, kind="etf"), _us_row("NOOPT", 99, put=False), _us_row("FAIL", 38),
            _us_row("LOW", 1)]
    prov = _FakeUs({"GOOD": INFO_GOOD, "BAD": INFO_BAD, "EARN": INFO_GOOD, "LOW": INFO_GOOD})
    meta = fu.apply_us_fundamentals(rows, "put", option_key="put", top_n=4, provider=prov)
    by = {r["symbol"]: r for r in rows}
    assert "SPY" not in prov.requested and "NOOPT" not in prov.requested and "LOW" not in prov.requested
    assert by["NOOPT"]["fund_status"] == "not_computed" and by["LOW"]["fund_status"] == "not_computed"
    assert by["SPY"]["fund_score"] is None and "ETF" in by["SPY"]["fund_status_cn"]
    assert by["SPY"]["combined_score"] == 45
    assert by["GOOD"]["fund_score"] > by["BAD"]["fund_score"]
    # same info, earnings inside option window ⇒ exactly the penalty lower
    assert by["EARN"]["earnings_window"]["in_window"]
    assert by["GOOD"]["fund_score"] - by["EARN"]["fund_score"] == pytest.approx(fu.EARNINGS_IN_WINDOW_PENALTY, abs=0.11)
    assert by["EARN"]["fund_penalty"]["points"] == -fu.EARNINGS_IN_WINDOW_PENALTY
    assert by["FAIL"]["fund_score"] is None
    assert all("HTTPError" in f["reason"] for f in by["FAIL"]["fund_factors"])
    assert meta["info_failed"] == 1 and meta["top_n"] == 4
    ranked = sorted(rows, key=fu.rank_key, reverse=True)
    assert ranked.index(by["GOOD"]) < ranked.index(by["BAD"])  # tech 40 vs 41, fundamentals flip it


def test_us_call_side_prefers_weak_fundamentals():
    rows = [_us_row("GOOD", 40), _us_row("BAD", 40)]
    for r in rows:
        r["call"] = r.pop("put")
    fu.apply_us_fundamentals(rows, "call", option_key="call", provider=_FakeUs({"GOOD": INFO_GOOD, "BAD": INFO_BAD}))
    by = {r["symbol"]: r for r in rows}
    assert by["BAD"]["fund_score"] > by["GOOD"]["fund_score"]


def test_screens_wire_fundamentals_and_keep_tech_gate():
    for mod in ("short_put_screen.py", "short_call_screen.py"):
        src = (ROOT / mod).read_text(encoding="utf-8")
        assert "_apply_fundamentals(rows," in src
        assert "key=_fund_rank_key" in src
        assert '(r.get("score") or 0) >= 22' in src  # inclusion gate unchanged


def test_us_apply_failure_is_non_fatal(monkeypatch):
    import short_put_screen as sps

    def boom(*a, **k):
        raise RuntimeError("yahoo down")

    monkeypatch.setattr(fu, "apply_us_fundamentals", boom)
    rows = [_us_row("A", 30)]
    meta = sps._apply_fundamentals(rows, "put")
    assert meta["status"] == "error"
    assert rows[0]["combined_score"] == 30 and rows[0]["fund_score"] is None


# ------------------------------------------------------------------ frontend

def test_removed_option_tab_views_are_gone_and_docs_in_sync():
    """卖Put/卖Call tabs were removed: their browser-only views are gone from both surfaces."""
    for name in ("fund_score_view.js", "cn_option_candidates_view.js"):
        assert not (ROOT / "static" / name).exists(), name
        assert not (ROOT / "docs" / "static" / name).exists(), name
    for name in ("app.js", "style.css"):
        assert (ROOT / "static" / name).read_bytes() == (ROOT / "docs" / "static" / name).read_bytes(), name
    for html in (ROOT / "templates" / "index.html", ROOT / "docs" / "index.html"):
        t = html.read_text(encoding="utf-8")
        assert "fund_score_view.js" not in t and "cn_option_candidates_view.js" not in t
        assert "app.js" in t
    assert 'asset_v = "20261114etf1"' in (ROOT / "app.py").read_text(encoding="utf-8")
