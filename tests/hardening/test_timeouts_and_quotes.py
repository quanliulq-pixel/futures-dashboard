import os
import threading
import time

os.environ.setdefault("SKIP_BG_WARMUP", "1")

import net_limits


def test_yf_download_forwards_explicit_timeout(monkeypatch):
    import yfinance as yf

    seen = {}
    monkeypatch.setattr(yf, "download", lambda *a, **k: seen.update(k) or "df")
    assert net_limits.yf_download(["CL=F"], period="5d", progress=False) == "df"
    assert seen["timeout"] == net_limits.YF_TIMEOUT_SEC


def test_yf_history_forwards_explicit_timeout():
    class T:
        def history(self, **k):
            return k

    assert net_limits.yf_history(T(), period="1y")["timeout"] == net_limits.YF_TIMEOUT_SEC


def test_yf_history_signature_supports_timeout():
    import inspect
    from yfinance.scrapers.history import PriceHistory

    assert "timeout" in inspect.signature(PriceHistory.history).parameters
    import yfinance as yf

    assert "timeout" in inspect.signature(yf.download).parameters


def test_hung_quote_source_does_not_accumulate_threads(monkeypatch):
    import data_sources as ds

    release = threading.Event()
    calls = {"cn": 0, "us": 0}

    def cn():
        calls["cn"] += 1
        release.wait(30)
        return {}

    def us():
        calls["us"] += 1
        release.wait(30)
        return {}

    monkeypatch.setattr(ds, "_fetch_cn_realtime_map", cn)
    monkeypatch.setattr(ds, "_batch_us_quotes", us)
    monkeypatch.setattr(ds, "get_cn_instruments", lambda: [])
    monkeypatch.setattr(ds, "get_us_instruments", lambda: [])
    ds._quote_source_futs.clear()
    before = threading.active_count()
    for _ in range(5):
        t0 = time.monotonic()
        ds._fetch_quotes_live(timeout_sec=0)
        assert time.monotonic() - t0 < 6
    # each source was started once; later refreshes skipped the hung source
    assert calls == {"cn": 1, "us": 1}
    assert threading.active_count() - before <= 2
    release.set()


import pytest


@pytest.mark.parametrize("raw,expected", [
    (None, 10.0), ("", 10.0), ("abc", 10.0), ("nan", 10.0), ("inf", 10.0),
    ("-inf", 10.0), ("0", 1.0), ("-5", 1.0), ("1e9", 60.0), ("15", 15.0),
])
def test_env_num_validated(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("T_ENV", raising=False)
    else:
        monkeypatch.setenv("T_ENV", raw)
    assert net_limits.env_num("T_ENV", 10.0, 1.0, 60.0) == expected


def test_bg_config_survives_malformed_env(monkeypatch):
    import importlib
    import bg_tasks

    for k, v in {"MAX_BG_TASKS": "nan", "BG_MIN_INTERVAL_SEC": "x", "BG_RATE_LIMIT_COOLDOWN_SEC": "0",
                 "YF_TIMEOUT_SEC": "-1"}.items():
        monkeypatch.setenv(k, v)
    importlib.reload(net_limits)
    importlib.reload(bg_tasks)
    try:
        assert bg_tasks.MAX_BG_TASKS == 2
        assert bg_tasks.DEFAULT_MIN_INTERVAL == 120.0
        assert bg_tasks.RATE_LIMIT_COOLDOWN == 30.0
        assert net_limits.YF_TIMEOUT_SEC == 1.0
    finally:
        for k in ("MAX_BG_TASKS", "BG_MIN_INTERVAL_SEC", "BG_RATE_LIMIT_COOLDOWN_SEC", "YF_TIMEOUT_SEC"):
            monkeypatch.delenv(k, raising=False)
        importlib.reload(net_limits)
        importlib.reload(bg_tasks)


def test_sina_runs_during_yahoo_cooldown(monkeypatch):
    import bg_tasks
    import data_sources as ds

    bg_tasks._reset_for_tests(max_tasks=2)
    calls = {"cn": 0, "us": 0}
    monkeypatch.setattr(ds, "_fetch_cn_realtime_map", lambda: calls.__setitem__("cn", calls["cn"] + 1) or {})
    monkeypatch.setattr(ds, "_batch_us_quotes", lambda: calls.__setitem__("us", calls["us"] + 1) or {})
    monkeypatch.setattr(ds, "get_cn_instruments", lambda: [])
    monkeypatch.setattr(ds, "get_us_instruments", lambda: [])
    ds._quote_source_futs.clear()
    bg_tasks.note_rate_limited("yahoo", 60)
    try:
        ds._fetch_quotes_live(timeout_sec=2)
        assert calls == {"cn": 1, "us": 0}
        # the combined job itself is not gated by the yahoo cooldown
        assert bg_tasks.try_start("quotes", lambda: None, min_interval=0, provider=None) == "started"
    finally:
        bg_tasks._reset_for_tests(max_tasks=2)


def test_render_no_seed_quotes_nonblocking(monkeypatch):
    import bg_tasks
    import data_sources as ds

    bg_tasks._reset_for_tests(max_tasks=2)
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setattr(ds, "_quote_bundle_cache", None)
    monkeypatch.setattr(ds, "_load_quotes_seed", lambda: None)
    started = threading.Event()
    monkeypatch.setattr(ds, "_fetch_quotes_live", lambda *a, **k: (started.set(), time.sleep(3), [])[2])
    t0 = time.monotonic()
    assert ds.get_quotes() == []
    assert time.monotonic() - t0 < 0.5
    assert started.wait(1)
    bg_tasks._reset_for_tests(max_tasks=2)
