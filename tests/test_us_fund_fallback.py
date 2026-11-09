# -*- coding: utf-8 -*-
"""Offline tests: degraded Yahoo Ticker.info (no crumb) → empty detection, negative cache,
statement fallback, snapshot reuse (≤48h, labelled) and honest fund_meta. No network."""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

os.environ.setdefault("SKIP_BG_WARMUP", "1")
os.environ["FUND_SNAPSHOT_REMOTE"] = "0"

import pandas as pd
import pytest

import fundamentals as fu

ROOT = Path(__file__).resolve().parents[1]

# What yfinance 1.8 returns from Ticker.info when quoteSummary + v7 quote get HTTP 401 "Invalid Crumb"
# (reproduced locally by forcing the crumb fetch to fail): only the crumb-free timeseries key survives.
DEGRADED_INFO = {"trailingPegRatio": 0.2269}

Q_COLS = pd.to_datetime(["2026-09-30", "2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30"])


def _q_income():
    return pd.DataFrame(
        {c: v for c, v in zip(Q_COLS, [
            [12.0e9, 0.69e9, 4.55], [9.5e9, 0.45e9, 2.96], [8.0e9, 0.30e9, 2.0], [7.8e9, 0.31e9, 2.08],
            [7.5e9, 0.34e9, 2.24]])},
        index=["Total Revenue", "Net Income", "Diluted EPS"]).astype(float)


def _ttm_income(eps=11.0, ni=1.68e9, rev=37.3e9):
    return pd.DataFrame({Q_COLS[0]: [rev, ni, eps]}, index=["Total Revenue", "Net Income", "Diluted EPS"])


def _balance():
    return pd.DataFrame({Q_COLS[0]: [10.5e9, 7.5e9], Q_COLS[1]: [9.6e9, 6.6e9]},
                        index=["Stockholders Equity", "Total Debt"])


def _cashflow(fcf=3.1e9):
    return pd.DataFrame({Q_COLS[0]: [fcf]}, index=["Free Cash Flow"])


def _valuation(fwd=25.3, trailing=48.9):
    return pd.DataFrame({"Current": [trailing, fwd], "9/30/2026": [None, None]}, index=["Trailing P/E", "Forward P/E"])


class _FakeTicker:
    def __init__(self, info, *, statements=True, analyst=False, raise_info=False):
        self._info, self._st, self._an, self._raise = info, statements, analyst, raise_info
        self.fast_info = {"lastPrice": 550.0}

    @property
    def info(self):
        if self._raise:
            raise RuntimeError("boom crumb=SECRET123")
        return self._info

    def get_valuation_measures(self):
        return _valuation() if self._st else pd.DataFrame()

    @property
    def ttm_income_stmt(self):
        return _ttm_income() if self._st else pd.DataFrame()

    @property
    def quarterly_income_stmt(self):
        return _q_income() if self._st else pd.DataFrame()

    @property
    def quarterly_balance_sheet(self):
        return _balance() if self._st else pd.DataFrame()

    @property
    def ttm_cashflow(self):
        return _cashflow() if self._st else pd.DataFrame()

    @property
    def analyst_price_targets(self):
        return {"current": 550.0, "mean": 770.0} if self._an else {}

    @property
    def recommendations_summary(self):
        if not self._an:
            return pd.DataFrame()
        return pd.DataFrame([{"period": "0m", "strongBuy": 1, "buy": 24, "hold": 5, "sell": 0, "strongSell": 0}])


class _Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


def _provider(factory, clock=None):
    calls = []

    def f(sym):
        calls.append(sym)
        return factory(sym)

    return fu.UsInfoProvider(ticker_factory=f, clock=clock or _Clock()), calls


def _row(sym, score=40.0):
    return {"symbol": sym, "kind": "stock", "score": score, "price": 550.0,
            "put": {"dte": 30, "expiry": "2026-12-09"}, "earnings": None}


GOOD_INFO = {"quoteType": "EQUITY", "forwardPE": 15.0, "profitMargins": 0.25, "returnOnEquity": 0.3,
             "revenueGrowth": 0.1, "earningsGrowth": 0.12, "numberOfAnalystOpinions": 20,
             "targetMeanPrice": 600.0, "recommendationMean": 1.8, "freeCashflow": 1e9, "debtToEquity": 40.0}


# ------------------------------------------------------------------ empty detection

def test_degraded_info_dict_is_counted_empty_not_ok():
    prov, _ = _provider(lambda s: _FakeTicker(DEGRADED_INFO, statements=False))
    rows = [_row("PWR")]
    meta = fu.apply_us_fundamentals(rows, "put", option_key="put", provider=prov)
    assert meta["info_ok"] == 0 and meta["info_empty"] == 1 and meta["info_failed"] == 0
    assert meta["data_status"] == "degraded" and meta["fund_scored"] == 0
    assert rows[0]["fund_score"] is None
    assert all(f["status"] == "missing" for f in rows[0]["fund_factors"])
    assert all("缺少打分字段" in f["reason"] for f in rows[0]["fund_factors"])
    st, present, missing = fu.info_needed_status(DEGRADED_INFO)
    assert st == "empty" and present == [] and "trailingPE" in missing


def test_legacy_tuple_with_degraded_dict_is_empty():
    rec = fu._normalize_record((DEGRADED_INFO, "2026-11-09", None))
    assert rec["info_status"] == "empty" and rec["info"] is None
    assert fu._normalize_record(({"quoteType": "ETF"}, "d", None))["info_status"] == "ok"


def test_empty_info_logs_missing_keys(caplog):
    prov, _ = _provider(lambda s: _FakeTicker(DEGRADED_INFO, statements=False))
    with caplog.at_level("WARNING", logger="fundamentals"):
        prov.batch(["PWR"])
    msg = " ".join(r.getMessage() for r in caplog.records)
    assert "PWR" in msg and "trailingPegRatio" in msg and "forwardPE" in msg


# ------------------------------------------------------------------ cache TTLs

def test_negative_cache_is_short_and_ok_cache_is_long():
    clock = _Clock()
    state = {"info": DEGRADED_INFO}
    prov, calls = _provider(lambda s: _FakeTicker(state["info"], statements=False), clock)
    assert prov._get_one("X")["info_status"] == "empty"
    clock.t += 10 * 60
    assert prov._get_one("X")["cached"] is True and len(calls) == 1
    clock.t += 6 * 60  # 16 min > 15 min negative TTL ⇒ refetch
    state["info"] = GOOD_INFO
    rec = prov._get_one("X")
    assert len(calls) == 2 and rec["info_status"] == "ok" and not rec["cached"]
    clock.t += 23 * 3600  # ok records stay 24h
    assert prov._get_one("X")["cached"] is True and len(calls) == 2
    clock.t += 2 * 3600
    prov._get_one("X")
    assert len(calls) == 3
    assert fu.US_INFO_NEG_TTL == 15 * 60


def test_exception_is_failed_and_negative_cached_and_redacted():
    clock = _Clock()
    prov, calls = _provider(lambda s: _FakeTicker({}, raise_info=True, statements=False), clock)
    rec = prov._get_one("Y")
    assert rec["info_status"] == "failed" and "SECRET123" not in rec["err"]
    clock.t += 60
    prov._get_one("Y")
    assert len(calls) == 1


# ------------------------------------------------------------------ statement fallback (pure)

def test_info_from_statements_computes_reported_ratios():
    out, how = fu.info_from_statements(valuation=_valuation(), ttm_income=_ttm_income(), q_income=_q_income(),
                                       q_balance=_balance(), ttm_cashflow=_cashflow(), price=550.0)
    assert out["trailingPE"] == pytest.approx(550.0 / 11.0)
    assert out["forwardPE"] == pytest.approx(25.3)
    assert out["profitMargins"] == pytest.approx(1.68e9 / 37.3e9)
    assert out["returnOnEquity"] == pytest.approx(1.68e9 / 10.5e9)
    assert out["debtToEquity"] == pytest.approx(7.5e9 / 10.5e9 * 100)
    assert out["freeCashflow"] == pytest.approx(3.1e9)
    assert out["revenueGrowth"] == pytest.approx(12.0 / 7.5 - 1)          # 2026-09-30 vs 2025-09-30
    assert out["earningsGrowth"] == pytest.approx(4.55 / 2.24 - 1)        # diluted EPS YoY
    assert fu._date_from_epoch(out["mostRecentQuarter"]) == "2026-09-30"
    assert "targetMeanPrice" not in out and "numberOfAnalystOpinions" not in out  # no analyst input ⇒ absent
    assert how["trailingPE"] == "现价/TTM稀释EPS"


def test_statements_loss_maker_gets_no_pe_and_scores_as_loss():
    # Yahoo valuation table can show a positive P/E for a loss-maker (seen for RIOT) – must not be used.
    out, _ = fu.info_from_statements(valuation=_valuation(fwd=20.9, trailing=12.9),
                                     ttm_income=_ttm_income(eps=-4.12, ni=-1.4e9, rev=0.66e9), price=17.2)
    assert "trailingPE" not in out and "forwardPE" not in out
    v = fu.us_valuation_factor(out, "RIOT", "2026-11-09")
    assert v["status"] == "ok" and "亏损" in v["value_text"]


def test_yoy_requires_year_ago_quarter_and_positive_base():
    two = _q_income()[Q_COLS[:2]]
    out, _ = fu.info_from_statements(q_income=two)
    assert "revenueGrowth" not in out and "earningsGrowth" not in out
    neg = _q_income().copy()
    neg.loc["Diluted EPS", Q_COLS[4]] = -0.5
    neg.loc["Net Income", Q_COLS[4]] = -1e8
    out2, _ = fu.info_from_statements(q_income=neg)
    assert "earningsGrowth" not in out2 and "revenueGrowth" in out2


def test_analyst_from_targets_and_recommendations():
    recs = pd.DataFrame([{"period": "0m", "strongBuy": 1, "buy": 24, "hold": 5, "sell": 0, "strongSell": 0}])
    out, _ = fu.info_from_statements(price_targets={"current": 550.0, "mean": 770.0}, rec_summary=recs)
    assert out["numberOfAnalystOpinions"] == 30
    assert out["recommendationMean"] == pytest.approx((1 * 1 + 2 * 24 + 3 * 5) / 30)
    assert out["targetMeanPrice"] == 770.0


def test_nothing_in_nothing_out():
    out, how = fu.info_from_statements()
    assert out == {} and how == {}


# ------------------------------------------------------------------ provider + apply with fallback

def test_empty_info_uses_statement_fallback_with_route_and_meta():
    prov, _ = _provider(lambda s: _FakeTicker(DEGRADED_INFO, statements=True))
    rows = [_row("PWR")]
    meta = fu.apply_us_fundamentals(rows, "put", option_key="put", provider=prov)
    by = {f["key"]: f for f in rows[0]["fund_factors"]}
    for k in ("valuation", "quality", "growth"):
        assert by[k]["status"] == "ok" and by[k]["data_route"] == "statements"
        assert "按报表计算" in by[k]["source"] and by[k]["source_url"].endswith("/financials")
    assert "Yahoo估值表远期PE" in by["valuation"]["value_text"]
    assert by["analyst"]["status"] == "missing" and "报表回退无此项" in by["analyst"]["reason"]
    assert meta["info_empty"] == 1 and meta["fallback_used"] == 1 and meta["fund_scored"] == 1
    assert meta["factor_routes"]["valuation"]["statements"] == 1
    assert meta["factor_routes"]["analyst"]["missing"] == 1
    p = meta["probe"]
    assert p["symbol"] == "PWR" and p["info_status"] == "empty" and p["info_key_count"] == 1
    assert p["needed_present"] == [] and "trailingPE" in p["fallback_keys"]
    assert p["endpoints"]["ttm_income_stmt"] == "ok" and p["endpoints"]["analyst_price_targets"] == "empty"


def test_good_info_is_ok_and_not_falling_back():
    prov, _ = _provider(lambda s: _FakeTicker(GOOD_INFO))
    rows = [_row("AAA")]
    called = []
    meta = fu.apply_us_fundamentals(rows, "put", option_key="put", provider=prov,
                                    snapshot=lambda: called.append(1) or fu.SnapshotFundIndex())
    assert meta["info_ok"] == 1 and meta["info_empty"] == 0 and meta["fallback_used"] == 0
    assert meta["data_status"] == "live" and called == [] and meta["snapshot_loaded"] is False
    assert all(f["data_route"] == "info" for f in rows[0]["fund_factors"])
    assert meta["probe"]["info_status"] == "ok" and "forwardPE" in meta["probe"]["needed_present"]


def test_sanitize_redacts_tokens():
    s = fu._sanitize("GET ...?crumb=abc123&symbol=X cookie: A3=zzz")
    assert "abc123" not in s and "zzz" not in s and "<redacted>" in s


# ------------------------------------------------------------------ snapshot reuse

SNAP_AT = "2026-11-09T22:22:00"  # naive = Beijing time (export runs with TZ=Asia/Shanghai)


def _snap_payload(updated_at=SNAP_AT, sym="PWR", extra=None):
    fac = {"key": "analyst", "name": "分析师（目标价/评级）", "status": "ok", "signal": 0.75,
           "signal_text": "明显偏多", "value": 0.75, "value_text": "29位分析师；目标均价 770.42",
           "asof": "2026-11-09", "source": "Yahoo Finance 关键统计"}
    val = dict(fac, key="valuation", name="估值（PE）", signal=0.0, value_text="远期PE 26.4")
    row = {"symbol": sym, "kind": "stock", "fund_factors": [fac, val] + (extra or [])}
    etf = {"symbol": "SPY", "kind": "etf", "fund_factors": [fac]}
    return {"updated_at": updated_at, "all": [row, etf], "picks": []}


def _now(hours_after):
    return lambda: fu._parse_snapshot_time(SNAP_AT) + timedelta(hours=hours_after)


def test_snapshot_reuse_fills_only_missing_factors_and_labels_them():
    idx = fu.SnapshotFundIndex([("docs/api/short_put.json", _snap_payload())], now=_now(5))
    prov, _ = _provider(lambda s: _FakeTicker(DEGRADED_INFO, statements=True))
    rows = [_row("PWR")]
    meta = fu.apply_us_fundamentals(rows, "put", option_key="put", provider=prov, snapshot=idx)
    by = {f["key"]: f for f in rows[0]["fund_factors"]}
    a = by["analyst"]
    assert a["status"] == "ok" and a["data_route"] == "snapshot" and a["from_snapshot"] is True
    assert "来自快照 2026-11-09 22:22 北京时间" in a["value_text"] and a["asof"] == "2026-11-09·来自快照"
    assert "来自快照" in a["source"] and a["snapshot_age_hours"] == pytest.approx(5.0)
    assert a["weight"] > 0 and a["contribution"] is not None  # re-scored, not copied
    # valuation came from statements and must NOT be overwritten by the snapshot
    assert by["valuation"]["data_route"] == "statements"
    assert meta["snapshot_used"] == 1 and meta["factor_routes"]["analyst"]["snapshot"] == 1
    assert meta["snapshot_loaded"] and meta["snapshot_sources"][0]["updated_at"] == SNAP_AT


def test_snapshot_older_than_48h_is_not_used():
    idx = fu.SnapshotFundIndex([("s", _snap_payload())], now=_now(49))
    assert idx.lookup("PWR", "analyst") is None
    prov, _ = _provider(lambda s: _FakeTicker(DEGRADED_INFO, statements=False))
    rows = [_row("PWR")]
    meta = fu.apply_us_fundamentals(rows, "put", option_key="put", provider=prov, snapshot=idx)
    assert rows[0]["fund_score"] is None and meta["snapshot_used"] == 0
    assert all("无48小时内快照" in f["reason"] for f in rows[0]["fund_factors"])


def test_snapshot_age_follows_original_time_for_reused_factors():
    fresh = _snap_payload(updated_at="2026-11-11T21:00:00")
    for f in fresh["all"][0]["fund_factors"]:
        f.update(from_snapshot=True, snapshot_at="2026-11-09T22:22:00+08:00", snapshot_orig_asof="2026-11-09",
                 value_text=f["value_text"] + "（来自快照 2026-11-09 22:22 北京时间）")
    idx = fu.SnapshotFundIndex([("s", fresh)], now=_now(47))
    g = idx.lookup("PWR", "analyst")
    assert g is not None and g["snapshot_at"].startswith("2026-11-09T22:22")
    assert g["value_text"].count("来自快照") == 1 and g["asof"] == "2026-11-09·来自快照"
    assert fu.SnapshotFundIndex([("s", fresh)], now=_now(49)).lookup("PWR", "analyst") is None


def test_snapshot_newest_wins_and_skips_etf_missing_and_bad_rows():
    old = _snap_payload(updated_at="2026-11-09T10:00:00")
    old["all"][0]["fund_factors"][0]["value_text"] = "OLD"
    bad = {"updated_at": SNAP_AT, "all": [{"symbol": "ZZZ", "kind": "stock", "fund_factors": [
        {"key": "analyst", "status": "missing", "signal": None}, {"key": "bogus", "status": "ok", "signal": 1}]}]}
    idx = fu.SnapshotFundIndex([("old", old), ("new", _snap_payload()), ("bad", bad),
                                ("notime", {"updated_at": None, "all": []}), ("junk", None)], now=_now(1))
    assert idx.lookup("PWR", "analyst")["value_text"].startswith("29位")
    assert idx.lookup("SPY", "analyst") is None and idx.lookup("ZZZ", "analyst") is None
    assert idx.lookup("ZZZ", "bogus") is None


def test_default_snapshot_index_reads_bundled_docs_without_network():
    idx = fu.default_snapshot_index(remote=False)
    labels = [s["source"] for s in idx.sources]
    assert "docs/api/short_put.json" in labels and "docs/api/short_call.json" in labels


def test_snapshot_factory_error_is_reported_not_fatal():
    prov, _ = _provider(lambda s: _FakeTicker(DEGRADED_INFO, statements=False))
    rows = [_row("PWR")]

    def boom():
        raise OSError("disk")

    meta = fu.apply_us_fundamentals(rows, "put", option_key="put", provider=prov, snapshot=boom)
    assert meta["snapshot_error"].startswith("OSError") and rows[0]["fund_score"] is None


def test_screens_pass_snapshot_factory():
    for mod in ("short_put_screen.py", "short_call_screen.py"):
        src = (ROOT / mod).read_text(encoding="utf-8")
        assert "snapshot=default_snapshot_index" in src
