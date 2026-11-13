"""ETF净申赎：上交所份额接口无返回（Render/Actions 偶发）时回退最近一次有数据的快照，并标「上次快照」+ asof。

回归：2026-11-13 线上上证50卡片「ETF净申赎 无数据（上交所ETF份额接口无返回）」——部署时打包的快照恰好是
一次 SSE 失败的 Actions 刷新，而 GitHub 上更新的快照已有 ETF 申赎；旧逻辑只回退主力净流入。
"""
import os
import socket
import time
from datetime import date, datetime, timedelta

import pytest

import cn_index_flow as F

TODAY = datetime.now(F.CST).date()
ETF_IDS = [m["id"] for m in F.FLOW_INDICES if m["etfs"]]


@pytest.fixture(autouse=True)
def _isolate_remote_seed_cache(monkeypatch):
    # refresh() stores the GitHub snapshot in module memory; never leak it into other tests.
    monkeypatch.setattr(F, "_REMOTE_SEED_CACHE", None)


def _etf_ok(asof, net=1.5):
    return {"status": "ok", "asof": asof, "unit": "亿元", "net_1d": net, "net_5d": net * 2, "net_20d": net * 5,
            "coverage": "2/2 只有数据", "source": F.SRC["etf"]["label"], "source_url": F.SRC["etf"]["url"],
            "items": [{"status": "ok", "code": "510050", "name": "华夏上证50ETF", "asof": asof, "net_1d": net}]}


def _etf_missing():
    return {"status": "missing", "reason": "上交所ETF份额接口无返回", "unit": "亿元",
            "items": [{"status": "missing", "code": "510050", "reason": "上交所ETF份额接口无返回"}],
            "net_1d": None, "net_5d": None, "net_20d": None}


def _main_ok(asof):
    return {"status": "ok", "asof": asof, "net_1d": -4.02, "net_5d": -28.87, "net_20d": -150.88}


def _bundle(fetched_at, etf_for, main_asof=None):
    idx = {}
    for m in F.FLOW_INDICES:
        etf = etf_for(m["id"]) if m["etfs"] else {"status": "missing", "reason": m.get("etf_note")}
        idx[m["id"]] = {"id": m["id"], "name": m["name"], "tracked": m["tracked"],
                        "main": _main_ok(main_asof) if main_asof else {"status": "missing", "reason": "跳过"},
                        "etf": etf}
    return {"fetched_at": fetched_at, "indices": idx, "notes": [], "errors": {},
            "market": {"main": _main_ok(main_asof) if main_asof else {"status": "missing"},
                       "northbound": {"status": "missing", "reason": F.NORTHBOUND_REASON}}}


def _d(n):
    return (TODAY - timedelta(days=n)).isoformat()


def test_merge_fills_missing_etf_from_snapshot_and_labels_it():
    live = _bundle("2026-11-13T09:10:00+08:00", lambda i: _etf_missing())
    snap = _bundle("2026-11-13T05:34:28+08:00", lambda i: _etf_ok(_d(1), net=-9.7), main_asof=_d(1))
    out = F.merge_main_from_fallback(live, snap)
    e = out["indices"]["sse50"]["etf"]
    assert e["status"] == "ok" and e["from_snapshot"] is True and e["asof"] == _d(1)
    assert e["net_1d"] == pytest.approx(-9.7)
    assert all(i.get("from_snapshot") for i in e["items"] if i["status"] == "ok")
    assert out["etf_from_snapshot"] is True and out["etf_snapshot_asof"] == _d(1)
    assert any("ETF净申赎为上次快照" in n for n in out["notes"])
    # 创业板：深交所无历史份额，仍诚实缺失
    assert out["indices"]["chinext"]["etf"]["status"] == "missing"
    # 不修改输入
    assert live["indices"]["sse50"]["etf"]["status"] == "missing"


def test_merge_keeps_live_etf_and_does_not_flag_it():
    live = _bundle("2026-11-13T09:10:00+08:00", lambda i: _etf_ok(_d(0), net=2.0))
    snap = _bundle("2026-11-12T15:41:08+08:00", lambda i: _etf_ok(_d(1), net=-9.7))
    out = F.merge_main_from_fallback(live, snap)
    e = out["indices"]["sse50"]["etf"]
    assert e["net_1d"] == pytest.approx(2.0) and not e.get("from_snapshot")
    assert not out.get("etf_from_snapshot")


def test_merge_picks_freshest_etf_snapshot_regardless_of_order():
    live = _bundle("x", lambda i: _etf_missing())
    older = _bundle("a", lambda i: _etf_ok(_d(3), net=1.0))
    newer = _bundle("b", lambda i: _etf_ok(_d(1), net=7.0))
    for order in ((older, newer), (newer, older)):
        out = F.merge_main_from_fallback(live, *order)
        assert out["indices"]["sse50"]["etf"]["asof"] == _d(1)
        assert out["indices"]["sse50"]["etf"]["net_1d"] == pytest.approx(7.0)


def test_merge_picks_freshest_main_snapshot_regardless_of_order():
    live = _bundle("x", lambda i: _etf_missing())
    older = _bundle("a", lambda i: _etf_missing(), main_asof=_d(3))
    newer = _bundle("b", lambda i: _etf_missing(), main_asof=_d(1))
    out = F.merge_main_from_fallback(live, older, newer)
    assert out["indices"]["sse50"]["main"]["asof"] == _d(1)
    assert out["main_snapshot_asof"] == _d(1)


def test_merge_never_uses_stale_or_missing_etf_snapshot():
    live = _bundle("x", lambda i: _etf_missing())
    stale = _bundle("a", lambda i: _etf_ok(_d(F.FLOW_MAX_AGE_DAYS + 1)))
    out = F.merge_main_from_fallback(live, stale, _bundle("b", lambda i: _etf_missing()))
    e = out["indices"]["sse50"]["etf"]
    assert e["status"] == "missing" and e["net_1d"] is None
    assert "上交所ETF份额接口无返回" in e["reason"]
    assert not out.get("etf_from_snapshot")
    # 没有 asof 的 ok 不算可用（不编造日期）
    noasof = _bundle("c", lambda i: dict(_etf_ok(_d(1)), asof=None))
    assert F.merge_main_from_fallback(live, noasof)["indices"]["sse50"]["etf"]["status"] == "missing"


def test_flag_etf_from_snapshot_marks_without_changing_numbers():
    b = _bundle("2026-11-13T05:34:28+08:00", lambda i: _etf_ok(_d(1), net=-9.7))
    out = F.flag_etf_from_snapshot(b)
    assert out["indices"]["sse50"]["etf"]["from_snapshot"] is True
    assert out["indices"]["sse50"]["etf"]["net_1d"] == pytest.approx(-9.7)
    assert out["etf_from_snapshot"] is True and out["etf_snapshot_asof"] == _d(1)
    assert "from_snapshot" not in b["indices"]["sse50"]["etf"]
    empty = _bundle("y", lambda i: _etf_missing())
    assert F.flag_etf_from_snapshot(empty) is empty


def test_coalesce_prefers_remote_snapshot_with_etf_over_bundled_failed_seed():
    bundled = _bundle("2026-11-12T15:41:08+08:00", lambda i: _etf_missing(), main_asof=_d(1))
    remote = _bundle("2026-11-13T05:34:28+08:00", lambda i: _etf_ok(_d(1), net=-9.7), main_asof=_d(1))
    out = F.coalesce_flow_bundles(None, bundled, remote)
    assert all(out["indices"][i]["etf"]["status"] == "ok" for i in ETF_IDS)


def test_refresh_sse_failure_falls_back_to_remote_snapshot(monkeypatch):
    """Production path (no injected fetchers): SSE empty → bundled failed seed + newer GitHub snapshot."""
    monkeypatch.setattr(F, "skip_push2", lambda: True)
    monkeypatch.setenv("SKIP_INDEX_HOLDERS", "1")
    cal = [TODAY - timedelta(days=k) for k in range(25, 0, -1)]
    monkeypatch.setattr(F, "default_fetchers", lambda: {
        "cal": lambda: cal, "etf_prices": lambda: {}, "margin_sh": lambda: []})
    monkeypatch.setattr(F, "_safe_sse", lambda d: {})       # 上交所无返回
    monkeypatch.setattr(F, "fetch_margin_sz", lambda dates: [])
    import cn_index_etf_holders as H
    monkeypatch.setattr(H, "refresh_holder_bundle", lambda live=True: None)
    bundled = _bundle("2026-11-12T15:41:08+08:00", lambda i: _etf_missing(), main_asof=_d(1))
    remote = _bundle("2026-11-13T05:34:28+08:00", lambda i: _etf_ok(_d(1), net=-9.7), main_asof=_d(1))
    monkeypatch.setattr(F, "load_bundled_flow_seed", lambda: bundled)
    monkeypatch.setattr(F, "fetch_remote_flow_seed", lambda *a, **k: remote)
    b = F.IndexFlowProvider().refresh(force=True)
    assert b["errors"].get("etf_shares") == "上交所ETF份额接口无返回"
    e = b["indices"]["sse50"]["etf"]
    assert e["status"] == "ok" and e["from_snapshot"] is True and e["asof"] == _d(1)
    assert b["etf_from_snapshot"] is True
    assert F.get_cached_remote_flow_seed() is remote
    assert b["indices"]["chinext"]["etf"]["status"] == "missing"


def test_refresh_sse_failure_without_any_snapshot_stays_missing(monkeypatch):
    monkeypatch.setattr(F, "skip_push2", lambda: True)
    monkeypatch.setenv("SKIP_INDEX_HOLDERS", "1")
    cal = [TODAY - timedelta(days=k) for k in range(25, 0, -1)]
    monkeypatch.setattr(F, "default_fetchers", lambda: {
        "cal": lambda: cal, "etf_prices": lambda: {}, "margin_sh": lambda: []})
    monkeypatch.setattr(F, "_safe_sse", lambda d: {})
    monkeypatch.setattr(F, "fetch_margin_sz", lambda dates: [])
    import cn_index_etf_holders as H
    monkeypatch.setattr(H, "refresh_holder_bundle", lambda live=True: None)
    monkeypatch.setattr(F, "load_bundled_flow_seed", lambda: None)
    monkeypatch.setattr(F, "fetch_remote_flow_seed", lambda *a, **k: None)
    b = F.IndexFlowProvider().refresh(force=True)
    e = b["indices"]["sse50"]["etf"]
    assert e["status"] == "missing" and e["net_1d"] is None
    assert e["reason"] == "上交所ETF份额接口无返回"
    assert not b.get("etf_from_snapshot")


def test_fetch_remote_seed_accepts_etf_only_snapshot(monkeypatch):
    monkeypatch.setenv("FLOW_SEED_REMOTE", "1")
    payload = {"index_flows": _bundle("z", lambda i: _etf_ok(_d(1)))}

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return payload

    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: R())
    assert F.fetch_remote_flow_seed() is not None
    payload = {"index_flows": _bundle("z", lambda i: _etf_missing())}
    assert F.fetch_remote_flow_seed() is None


def test_with_cn_flows_uses_cached_remote_seed_and_labels_etf(monkeypatch):
    """Request path stays offline: bundled seed has SSE failure, in-memory GitHub snapshot has ETF."""
    os.environ["SKIP_BG_WARMUP"] = "1"
    import app as app_mod

    def deny(*a, **k):
        raise OSError("network disabled")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("CN_INDEX_FLOW_AUTO", raising=False)
    monkeypatch.setattr(F.DEFAULT_FLOW_PROVIDER, "_cache", None)
    bundled = _bundle("2026-11-12T15:41:08+08:00", lambda i: _etf_missing(), main_asof=_d(1))
    remote = _bundle("2026-11-13T05:34:28+08:00", lambda i: _etf_ok(_d(1), net=-9.7), main_asof=_d(1))
    monkeypatch.setattr(F, "_REMOTE_SEED_CACHE", (time.time(), remote))
    out = app_mod._with_cn_flows({"index_flows": bundled, "indices": []}, kick=False)
    flows = out["index_flows"]
    e = flows["indices"]["sse50"]["etf"]
    assert e["status"] == "ok" and e["net_1d"] == pytest.approx(-9.7)
    assert e["from_snapshot"] is True and flows["etf_from_snapshot"] is True
    assert flows["etf_snapshot_asof"] == _d(1)
    assert flows["indices"]["sse50"]["main"]["from_snapshot"] is True


def test_with_cn_flows_kicks_remote_seed_refresh_on_render(monkeypatch):
    os.environ["SKIP_BG_WARMUP"] = "1"
    import app as app_mod

    started = []
    monkeypatch.setattr(app_mod, "_spawn_bg_refresh", lambda key, fn, **kw: started.append(key) or "started")
    monkeypatch.setattr(F.DEFAULT_FLOW_PROVIDER, "_cache", None)
    monkeypatch.setattr(F, "_REMOTE_SEED_CACHE", None)
    monkeypatch.delenv("SKIP_INDEX_FLOW", raising=False)
    monkeypatch.delenv("FLOW_SEED_REMOTE", raising=False)
    bundled = _bundle("2026-11-12T15:41:08+08:00", lambda i: _etf_missing(), main_asof=_d(1))
    app_mod._with_cn_flows({"index_flows": bundled, "indices": []}, kick=True)
    assert "cn-index-flow-seed" in started and "cn-index-flow" in started
    started.clear()
    monkeypatch.setenv("FLOW_SEED_REMOTE", "0")
    app_mod._with_cn_flows({"index_flows": bundled, "indices": []}, kick=True)
    assert "cn-index-flow-seed" not in started
