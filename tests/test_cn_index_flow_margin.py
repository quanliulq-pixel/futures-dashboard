"""两融：深交所按实际最新发布日对齐 T-1/T-5/T-20；沪深合计按同一交易日；快照回退并标「上次快照」。

回归：2026-11-13 线上「深交所 余额 10888.9 · 1/5/20日变化 无数据」、沪深合计变化全无数据。
原因：按日历最后一天（当天、深交所未发布）取 T-1/T-5/T-20，而深交所实际最新是前一交易日，
margin_summary 按实际最新日找 T-n 时这些日期从未抓取。
"""
import os
import socket
import time
from datetime import date, datetime, timedelta

import pytest

import cn_index_flow as F

TODAY = datetime.now(F.CST).date()


def _cal(n=25, end=None):
    end = end or TODAY
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)


def _szse_stub(cal, published_upto_idx, calls):
    vals = {d: 10000.0 + 10 * i for i, d in enumerate(cal)}

    def fetch(dates):
        calls.append(list(dates))
        return [(d, vals[d]) for d in dates if d in vals and cal.index(d) <= published_upto_idx]
    return fetch, vals


def test_aligned_fetch_uses_szse_actual_latest_day():
    cal = _cal()
    calls = []
    fetch, vals = _szse_stub(cal, len(cal) - 2, calls)    # 当天未发布
    series = F.fetch_margin_sz_aligned(cal, fetch)
    got = dict(series)
    t = len(cal) - 2
    assert max(got) == cal[t]
    for n in F.WINDOWS:
        assert cal[t - n] in got
    s = F.margin_summary(series, TODAY, "margin_sz", cal)
    assert s["status"] == "ok" and s["asof"] == cal[t].isoformat()
    assert s["chg_1d"] == pytest.approx(10.0) and s["chg_5d"] == pytest.approx(50.0)
    assert s["chg_20d"] == pytest.approx(200.0)


def test_old_unaligned_dates_reproduce_the_bug():
    """记录旧行为：按 cal[-1] 取日期、深交所落后一天 ⇒ 变化全空（防止回退到旧取数方式）。"""
    cal = _cal()
    calls = []
    fetch, _ = _szse_stub(cal, len(cal) - 2, calls)
    old_dates = [cal[-1 - n] for n in (0,) + F.WINDOWS]
    s = F.margin_summary(fetch(old_dates), TODAY, "margin_sz", cal)
    assert s["status"] == "ok"
    assert s["chg_1d"] is None and s["chg_5d"] is None and s["chg_20d"] is None


def test_aligned_fetch_same_day_published_and_gives_up_after_max_lag():
    cal = _cal()
    calls = []
    fetch, _ = _szse_stub(cal, len(cal) - 1, calls)
    assert max(dict(F.fetch_margin_sz_aligned(cal, fetch))) == cal[-1]
    calls = []
    fetch, _ = _szse_stub(cal, -1, calls)                  # 全部失败
    assert F.fetch_margin_sz_aligned(cal, fetch) == []
    assert len(calls) == F.SZSE_MARGIN_MAX_LAG
    assert F.fetch_margin_sz_aligned([], fetch) == []


def test_total_uses_latest_common_day_when_sse_is_a_day_ahead():
    cal = _cal()
    sh = [(d, 11000.0 + i) for i, d in enumerate(cal)]               # 上交所到 T
    sz = [(d, 10000.0 + 10 * i) for i, d in enumerate(cal[:-1])]     # 深交所到 T-1
    tot = F.margin_total_summary(sh, sz, TODAY, cal)
    t = len(cal) - 2
    assert tot["status"] == "ok" and tot["asof"] == cal[t].isoformat()
    assert tot["balance"] == pytest.approx(11000.0 + t + 10000.0 + 10 * t)
    assert tot["chg_1d"] == pytest.approx(11.0) and tot["chg_5d"] == pytest.approx(55.0)
    assert tot["chg_20d"] == pytest.approx(220.0)
    assert "source" not in tot


def test_total_missing_when_no_common_day_or_gap():
    cal = _cal()
    assert F.margin_total_summary([(cal[-1], 1.0)], [(cal[-2], 2.0)], TODAY, cal)["status"] == "missing"
    # T-1 深交所缺 ⇒ 1日变化 None，不拿邻日顶替
    sh = [(d, 100.0) for d in cal]
    sz = [(cal[-1], 50.0), (cal[-6], 40.0)]
    tot = F.margin_total_summary(sh, sz, TODAY, cal)
    assert tot["chg_1d"] is None and tot["chg_5d"] == pytest.approx(10.0)


def test_build_bundle_total_and_sz_changes_with_lagging_szse():
    cal = _cal()
    raw = {"calendar": cal,
           "margin_sh": [(d, 11000.0 + i) for i, d in enumerate(cal)],
           "margin_sz": [(d, 10000.0 + 10 * i) for i, d in enumerate(cal[:-1])]}
    mk = F.build_flow_bundle(raw, {}, TODAY)["market"]
    assert mk["margin_sh"]["asof"] == cal[-1].isoformat()
    assert mk["margin_sz"]["asof"] == cal[-2].isoformat() and mk["margin_sz"]["chg_1d"] == pytest.approx(10.0)
    assert mk["margin_total"]["status"] == "ok" and mk["margin_total"]["asof"] == cal[-2].isoformat()


def _mk(sz, total=None, sh=None):
    return {"fetched_at": "x", "indices": {}, "notes": [], "errors": {},
            "market": {"main": {"status": "missing"}, "margin_sh": sh or {"status": "missing"},
                       "margin_sz": sz, "margin_total": total or {"status": "missing"}}}


def _mg(asof, chg=1.0, bal=10888.9):
    return {"status": "ok", "asof": asof, "balance": bal, "chg_1d": chg, "chg_5d": chg, "chg_20d": chg, "unit": "亿元"}


def _d(n):
    return (TODAY - timedelta(days=n)).isoformat()


def test_merge_fills_margin_without_changes_from_snapshot():
    live = _mk({"status": "ok", "asof": _d(2), "balance": 10888.9, "chg_1d": None, "chg_5d": None, "chg_20d": None})
    snap = _mk(_mg(_d(1), chg=-4.9, bal=10883.9), total=_mg(_d(1), chg=7.0, bal=22656.4))
    out = F.merge_main_from_fallback(live, snap)
    sz = out["market"]["margin_sz"]
    assert sz["from_snapshot"] is True and sz["asof"] == _d(1) and sz["chg_1d"] == pytest.approx(-4.9)
    assert out["market"]["margin_total"]["from_snapshot"] is True
    assert out["margin_from_snapshot"] is True and out["margin_snapshot_asof"] == _d(1)
    assert live["market"]["margin_sz"]["chg_1d"] is None  # 不改输入


def test_merge_keeps_live_margin_and_rejects_older_or_stale_snapshot():
    live = _mk(_mg(_d(1), chg=3.0))
    out = F.merge_main_from_fallback(live, _mk(_mg(_d(1), chg=9.0)))
    assert out["market"]["margin_sz"]["chg_1d"] == pytest.approx(3.0) and not out.get("margin_from_snapshot")
    # 当前余额更新但缺变化：更旧的快照不覆盖
    newer_nochg = _mk({"status": "ok", "asof": _d(1), "balance": 1.0, "chg_1d": None, "chg_5d": None, "chg_20d": None})
    out = F.merge_main_from_fallback(newer_nochg, _mk(_mg(_d(3))))
    assert out["market"]["margin_sz"]["chg_1d"] is None and not out.get("margin_from_snapshot")
    # 过期快照不用；缺失保持缺失
    out = F.merge_main_from_fallback(_mk({"status": "missing", "reason": "接口无返回"}),
                                     _mk(_mg(_d(F.FLOW_MAX_AGE_DAYS + 1))))
    assert out["market"]["margin_sz"]["status"] == "missing"


def test_flag_margin_from_snapshot():
    b = _mk(_mg(_d(1)), total=_mg(_d(1)))
    out = F.flag_margin_from_snapshot(b)
    assert out["market"]["margin_sz"]["from_snapshot"] is True
    assert out["market"]["margin_total"]["from_snapshot"] is True
    assert "from_snapshot" not in out["market"]["margin_sh"]
    assert "from_snapshot" not in b["market"]["margin_sz"]
    empty = _mk({"status": "missing"})
    assert F.flag_margin_from_snapshot(empty) is empty


def test_refresh_production_path_uses_aligned_szse(monkeypatch):
    monkeypatch.setattr(F, "_REMOTE_SEED_CACHE", None)
    monkeypatch.setattr(F, "skip_push2", lambda: True)
    monkeypatch.setenv("SKIP_INDEX_HOLDERS", "1")
    cal = _cal()
    monkeypatch.setattr(F, "default_fetchers", lambda: {
        "cal": lambda: cal, "etf_prices": lambda: {},
        "margin_sh": lambda: [(d, 11000.0 + i) for i, d in enumerate(cal)]})
    monkeypatch.setattr(F, "_safe_sse", lambda d: {})
    calls = []
    fetch, _ = _szse_stub(cal, len(cal) - 2, calls)
    monkeypatch.setattr(F, "fetch_margin_sz", fetch)
    import cn_index_etf_holders as H
    monkeypatch.setattr(H, "refresh_holder_bundle", lambda live=True: None)
    monkeypatch.setattr(F, "load_bundled_flow_seed", lambda: None)
    monkeypatch.setattr(F, "fetch_remote_flow_seed", lambda *a, **k: None)
    mk = F.IndexFlowProvider().refresh(force=True)["market"]
    assert mk["margin_sz"]["asof"] == cal[-2].isoformat()
    assert all(mk["margin_sz"][f"chg_{n}d"] is not None for n in F.WINDOWS)
    assert mk["margin_total"]["status"] == "ok" and mk["margin_total"]["chg_20d"] is not None
    assert not mk["margin_sz"].get("from_snapshot")


def test_with_cn_flows_labels_seed_margin_as_snapshot(monkeypatch):
    os.environ["SKIP_BG_WARMUP"] = "1"
    import app as app_mod

    def deny(*a, **k):
        raise OSError("network disabled")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(F.DEFAULT_FLOW_PROVIDER, "_cache", None)
    monkeypatch.setattr(F, "_REMOTE_SEED_CACHE", None)
    seed = _mk(_mg(_d(1)), total=_mg(_d(1)), sh=_mg(_d(1)))
    seed["indices"] = {"csi300": {"id": "csi300", "main": {"status": "missing"}, "etf": {"status": "missing"}}}
    flows = app_mod._with_cn_flows({"index_flows": seed, "indices": []}, kick=False)["index_flows"]
    assert flows["market"]["margin_sz"]["from_snapshot"] is True
    assert flows["market"]["margin_sh"]["from_snapshot"] is True
    assert flows["margin_from_snapshot"] is True
