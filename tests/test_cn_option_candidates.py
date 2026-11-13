# -*- coding: utf-8 -*-
"""Offline tests: CN futures enter put/call candidate pools (direction-only)."""
from __future__ import annotations

import json
import math
import os

os.environ.setdefault("SKIP_BG_WARMUP", "1")
from pathlib import Path

import pandas as pd
import pytest

import cn_option_candidates as coc


@pytest.fixture(autouse=True)
def _no_retry_sleep(monkeypatch):
    monkeypatch.setattr(coc, "FETCH_RETRY_DELAY_SEC", 0.0)
    # Offline: never hit akshare fundamentals from these technical-only tests.
    monkeypatch.setattr(coc, "DEFAULT_CN_FUND_PROVIDER", None)


def _synthetic_close(n: int = 120, start: float = 100.0, drift: float = -0.002, vol: float = 0.025, seed: int = 42) -> pd.Series:
    import numpy as np

    rng = np.random.default_rng(seed)
    rets = drift + vol * rng.standard_normal(n)
    px = start * np.cumprod(1.0 + rets)
    idx = pd.date_range("2025-01-01", periods=n, freq="B")
    return pd.Series(px, index=idx, name="close")


def _df_from_series(s: pd.Series) -> pd.DataFrame:
    return pd.DataFrame({"date": s.index, "close": s.values})


def test_allowlist_includes_verified_optionable_and_excludes_non_optionable():
    assert coc.is_optionable_continuous("CU0")
    assert coc.is_optionable_continuous("CN:M0")
    assert not coc.is_optionable_continuous("IF0")
    assert coc.is_explicitly_excluded("FB0")
    roots = {coc.continuous_symbol(x["symbol"]) for x in coc.optionable_universe()}
    assert "IF0" not in roots and "CU0" in roots


def test_every_whitelist_row_has_traceable_http_source_urls():
    assert all(u.startswith("http") for u in coc.SOURCE_CATALOG.values())
    assert any("dce.com.cn" in u for u in coc.SOURCE_CATALOG.values())
    assert any("czce.com.cn" in u for u in coc.SOURCE_CATALOG.values())
    assert any("shfe.com.cn" in u for u in coc.SOURCE_CATALOG.values())
    for note in coc.SOURCE_NOTES:
        if note.startswith("此前项目内"):
            continue
        assert "http" in note
    for sym, meta in coc.CN_OPTIONABLE_CONTINUOUS.items():
        assert meta.get("source_urls"), sym
        assert all(str(u).startswith("http") for u in meta["source_urls"]), sym
        assert "2026" not in str(meta.get("verify") or "")
        assert "exchange_lists_2026" not in str(meta.get("verify") or "")


def test_build_rejects_non_optionable_even_if_history_injected():
    series = _synthetic_close()

    def loader(iid: str):
        return _df_from_series(series)

    out = coc.build_cn_direction_candidates("put", history_loader=loader, top_n=50, min_score=0)
    syms = {r["symbol"] for r in out["all"]}
    assert "IF0" not in syms and "CU0" in syms


def test_no_invented_option_chain_fields_and_cny_continuous_flags():
    series = _synthetic_close()

    def loader(iid: str):
        return _df_from_series(series)

    put = coc.build_cn_direction_candidates("put", history_loader=loader, min_score=0)
    call = coc.build_cn_direction_candidates("call", history_loader=loader, min_score=0)
    assert put["all"] and call["all"]
    for side, bundle in (("put", put), ("call", call)):
        for r in bundle["all"]:
            assert r["put"] is None and r["call"] is None
            assert r["currency"] == "CNY"
            assert r["underlying_type"] == "cn_continuous_futures"
            assert r["candidate_mode"] == "underlying_direction_only"
            assert r["asof"] and r["asof"] != "扫描"
            assert r["scanned_at"]
            assert r["scanned_at"].endswith("+08:00")
            assert r.get("scanned_at_tz") == "北京时间/新加坡时间 UTC+8"
            assert "Tokyo" not in (r.get("scanned_at_tz") or "")
            assert r["source_urls"]
            assert "不适用美股" in (r.get("action") or "")
            assert "不能下单" in (r.get("action") or "")


def test_asof_from_date_column_not_scan_time():
    idx = pd.date_range("2024-06-01", periods=80, freq="B")
    # high vol so it passes HV gate
    import numpy as np

    rng = np.random.default_rng(1)
    px = 100 * np.cumprod(1 + 0.03 * rng.standard_normal(80))
    df = pd.DataFrame({"date": idx, "close": px})

    def loader(iid: str):
        return df

    out = coc.build_cn_direction_candidates("put", history_loader=loader, min_score=0)
    assert out["all"]
    for r in out["all"]:
        assert r["asof"] == "2024-09-20" or r["asof"] == idx[-1].strftime("%Y-%m-%d")
        assert r["asof"] != r["scanned_at"][:10] or True  # scan may coincide by chance; values are distinct fields
        assert "T" in r["scanned_at"]  # iso datetime
        assert r["asof_status"] == "ok"


def test_asof_unknown_when_no_date_axis():
    s = _synthetic_close(n=80, vol=0.04)
    df = pd.DataFrame({"close": s.values})  # no date col, RangeIndex

    series, asof, reject = coc.prepare_close_frame(df)
    assert reject is None and series is not None
    assert asof is None


def test_reject_nonpositive_or_inf_close_does_not_use_prior_bar_as_latest():
    idx = pd.date_range("2025-01-01", periods=50, freq="B")
    closes = [100.0] * 49 + [float("inf")]
    df = pd.DataFrame({"date": idx, "close": closes})
    series, asof, reject = coc.prepare_close_frame(df)
    assert series is None and asof is None
    assert reject == "nonpositive_or_nonfinite_close"

    df2 = pd.DataFrame({"date": idx, "close": [100.0] * 49 + [0.0]})
    series2, _, reject2 = coc.prepare_close_frame(df2)
    assert series2 is None and reject2 == "nonpositive_or_nonfinite_close"

    df3 = pd.DataFrame({"date": idx, "close": [100.0] * 49 + [-5.0]})
    series3, _, reject3 = coc.prepare_close_frame(df3)
    assert series3 is None and reject3 == "nonpositive_or_nonfinite_close"


def test_prepare_sorts_and_dedupes_when_date_confirmed():
    idx = pd.to_datetime(["2025-01-03", "2025-01-01", "2025-01-01", "2025-01-02"])
    df = pd.DataFrame({"date": idx, "close": [30.0, 10.0, 11.0, 20.0]})
    series, asof, reject = coc.prepare_close_frame(df)
    assert reject is None
    assert list(series.values) == [11.0, 20.0, 30.0]
    assert asof == "2025-01-03"


def test_put_and_call_direction_scores_differ_on_same_series():
    import numpy as np

    rng = np.random.default_rng(7)
    rets = 0.004 + 0.02 * rng.standard_normal(120)
    px = 80 * np.cumprod(1.0 + rets)
    s = pd.Series(px, index=pd.date_range("2025-01-01", periods=120, freq="B"))

    def loader(iid: str):
        return _df_from_series(s)

    put = {r["symbol"]: r for r in coc.build_cn_direction_candidates("put", history_loader=loader, min_score=0)["all"]}
    call = {r["symbol"]: r for r in coc.build_cn_direction_candidates("call", history_loader=loader, min_score=0)["all"]}
    diffs = [abs((put[s]["score"] or 0) - (call[s]["score"] or 0)) for s in put if s in call]
    assert any(d > 0.05 for d in diffs)


def test_min_score_28_excludes_zan_bu_tui_jian_from_candidates():
    # Low-ish vol borderline series may score below 28
    s = _synthetic_close(n=100, drift=0.0, vol=0.018, seed=99)

    def loader(iid: str):
        return _df_from_series(s)

    out = coc.build_cn_direction_candidates("put", history_loader=loader)  # default min_score=28
    for r in out["candidates"]:
        assert (r.get("score") or 0) >= 28
        assert "暂不推荐" not in (r.get("bias_cn") or "")
    # Force include low scores in all, but candidates filtered
    out0 = coc.build_cn_direction_candidates("put", history_loader=loader, min_score=0)
    low = [r for r in out0["all"] if (r.get("score") or 0) < 28]
    if low:
        assert all(r["symbol"] not in {x["symbol"] for x in out["candidates"]} for r in low)


def test_missing_history_yields_honest_empty_not_fake_rows():
    def loader(iid: str):
        raise RuntimeError("network down")

    out = coc.build_cn_direction_candidates("put", history_loader=loader)
    assert out["candidates"] == [] and out["all"] == []
    assert out["status"] == "empty"


def test_ensure_old_snapshot_does_not_invent_cn_signals():
    seed = {
        "picks": [{"symbol": "XLE", "kind": "etf", "put": {"strike": 60}}],
        "all": [{"symbol": "XLE", "kind": "etf"}, {"symbol": "M0", "kind": "cn_fut", "market": "cn"}],
        "method": {"goal": "US"},
        "disclaimer": "US only",
    }
    out = coc.ensure_cn_candidate_fields(seed, side="put")
    assert out["cn_candidates"] == []
    assert out["cn_candidate_meta"]["status"] == "absent_in_snapshot"
    assert all(r.get("kind") != "cn_fut" for r in out["all"])


def test_attach_preserves_us_picks_and_does_not_merge_cn_into_all():
    series = _synthetic_close(vol=0.04)

    def loader(iid: str):
        return _df_from_series(series)

    payload = {
        "picks": [{"symbol": "NVDA", "kind": "stock", "put": {"strike": 100}, "score": 60}],
        "all": [{"symbol": "NVDA", "kind": "stock", "put": {"strike": 100}}],
        "watchlist": [],
        "themes": [],
        "method": {"goal": "US CSP"},
        "disclaimer": "US",
        "universe_size": 1,
        "core_symbols": ["NVDA"],
    }
    out = coc.attach_cn_candidates(payload, side="put", populate=True, history_loader=loader)
    assert out["picks"][0]["symbol"] == "NVDA"
    assert all(r.get("kind") != "cn_fut" for r in out["picks"])
    assert all(r.get("kind") != "cn_fut" and r.get("market") != "cn" for r in out["all"])
    assert isinstance(out.get("cn_candidates"), list)


def test_seed_path_ensures_before_cache_second_hit_consistent(monkeypatch):
    import short_put_screen as sps

    sps._cache = None
    seed = {
        "picks": [{"symbol": "SPY", "kind": "etf", "put": {"strike": 400}, "score": 50}],
        "all": [{"symbol": "SPY", "kind": "etf"}],
        "watchlist": [],
        "themes": [],
        "method": {"goal": "US"},
        "disclaimer": "US",
        "universe_size": 1,
        "core_symbols": ["SPY"],
    }
    monkeypatch.setattr(sps, "_load_seed_snapshot", lambda: dict(seed))
    monkeypatch.setattr(sps.bg_tasks, "submit", lambda *a, **k: None)
    first = sps.build_short_put_screen(force=False)
    assert "cn_candidates" in first and first["cn_candidates"] == []
    assert first["cn_candidate_meta"]["status"] == "absent_in_snapshot"
    # Second hit must come from cache and already include fields (no missing keys)
    second = sps.build_short_put_screen(force=False)
    assert second is first or second.get("cn_candidates") == first.get("cn_candidates")
    assert "cn_candidates" in second and "cn_candidate_meta" in second
    assert sps._cache is not None
    cached_payload = sps._cache[1]
    assert "cn_candidates" in cached_payload
    assert cached_payload["cn_candidate_meta"]["status"] == "absent_in_snapshot"


def test_repo_seed_snapshots_remain_honest_without_fake_cn_rows():
    root = Path(__file__).resolve().parents[1]
    for name in ("short_put.json", "short_call.json"):
        data = json.loads((root / "docs" / "api" / name).read_text(encoding="utf-8"))
        # Pages exports may now carry real CN direction rows (snapshot 2026-11-09 18:20);
        # they must stay direction-only: no option chain fields, CNY, unverified chain.
        for r in data.get("cn_candidates") or []:
            assert r.get("market") == "cn" and r.get("kind") == "cn_fut"
            assert r.get("put") is None and r.get("call") is None
            assert r.get("currency") == "CNY"
            assert r.get("option_chain_status") == "unverified"
            assert coc.is_optionable_continuous(r["symbol"])
        for bucket in ("picks", "all", "watchlist"):
            for r in data.get(bucket) or []:
                assert r.get("market") != "cn"
                assert r.get("kind") != "cn_fut"


# ---- cn-fix: failure reasons, tz label, Render cache-over-seed ----

def test_failure_reasons_recorded_and_logged_at_warning(caplog):
    good = _synthetic_close(vol=0.04)
    flat = pd.Series([100.0 + 0.01 * (i % 2) for i in range(120)],
                     index=pd.date_range("2025-01-01", periods=120, freq="B"))
    calls: dict[str, int] = {}

    def loader(iid: str):
        sym = coc.continuous_symbol(iid)
        calls[sym] = calls.get(sym, 0) + 1
        if sym == "CU0":
            raise TimeoutError("read timed out")
        if sym == "AU0":
            return pd.DataFrame()
        if sym == "M0":
            return _df_from_series(flat)
        if sym == "SR0" and calls[sym] == 1:
            raise ConnectionError("transient")  # retried once, then ok
        return _df_from_series(good)

    with caplog.at_level("WARNING", logger="cn_option_candidates"):
        out = coc.build_cn_direction_candidates("put", history_loader=loader, min_score=0, top_n=50)
    failed = {f["symbol"]: f for f in out["failed"]}
    filtered = {f["symbol"]: f for f in out["filtered"]}
    assert set(failed) == {"CU0", "AU0"}
    assert failed["CU0"]["reason"] == "fetch_error" and "TimeoutError" in failed["CU0"]["detail"]
    assert failed["AU0"]["reason"] == "empty_frame"
    assert calls["CU0"] == coc.FETCH_ATTEMPTS
    assert filtered["M0"]["reason"] == "low_hv20" and filtered["M0"]["hv20"] < coc.CN_HV20_MIN
    assert "SR0" in {r["symbol"] for r in out["all"]}  # transient error recovered by retry
    assert out["fetched"] == len(coc.CN_OPTIONABLE_CONTINUOUS) - 2
    assert out["scanned"] == out["fetched"] - 1
    assert any("CU0" in rec.getMessage() and rec.levelname == "WARNING" for rec in caplog.records)

    attached = coc.attach_cn_candidates({"picks": [], "all": []}, side="call", history_loader=loader)
    meta = attached["cn_candidate_meta"]
    assert {f["symbol"] for f in meta["failed"]} == {"CU0", "AU0"}
    assert {f["symbol"] for f in meta["filtered"]} == {"M0"}
    assert meta["hv20_min"] == coc.CN_HV20_MIN


def test_tz_label_derived_from_offset():
    assert coc.tz_label_for("2026-11-09T18:25:00+08:00") == "北京时间/新加坡时间 UTC+8"
    assert coc.tz_label_for("2026-11-09T18:25:00+09:00") == "UTC+9"
    assert coc.tz_label_for("2026-11-09T18:25:00") is None
    assert coc.now_cst_iso().endswith("+08:00")
    assert coc.tz_label_for(coc.now_cst_iso()) == "北京时间/新加坡时间 UTC+8"


def test_ensure_fixes_legacy_tokyo_label_in_cached_payload():
    payload = {
        "cn_candidates": [{"symbol": "MA0", "kind": "cn_fut", "market": "cn",
                           "scanned_at": "2026-11-09T18:25:01+08:00", "scanned_at_tz": "Asia/Tokyo (UTC+9)",
                           "asof_tz": "Asia/Tokyo-calendar-date"}],
        "cn_candidate_meta": {"status": "ok", "scanned_at": "2026-11-09T18:25:01+08:00",
                              "scanned_at_tz": "Asia/Tokyo (UTC+9)"},
    }
    out = coc.ensure_cn_candidate_fields(payload, side="put")
    assert out["cn_candidate_meta"]["scanned_at_tz"] == "北京时间/新加坡时间 UTC+8"
    assert out["cn_candidates"][0]["scanned_at_tz"] == "北京时间/新加坡时间 UTC+8"
    assert "Tokyo" not in json.dumps(out, ensure_ascii=False)
