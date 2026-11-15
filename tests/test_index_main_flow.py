# -*- coding: utf-8 -*-
"""Per-index 主力净流入（新浪板块，非 push2）。"""
from __future__ import annotations

from datetime import date

import pytest

import index_main_flow as I
import cn_index_flow as F


TODAY = date(2026, 11, 14)


def test_boards_document_sina_mapping():
    assert I.INDEX_BOARDS["csi300"]["bankuai"] == "hs300"
    assert I.INDEX_BOARDS["star50"]["bankuai"] == "zhishu_000688"
    assert I.INDEX_BOARDS["chinext"]["bankuai"] == "zhishu_399006"
    for iid in ("sse50", "csi500", "csi1000"):
        assert I.INDEX_BOARDS[iid]["bankuai"] is None
        st = I.stats_for_index([], I.INDEX_BOARDS[iid])
        assert st["status"] == "missing"
        assert "push2" in (st["reason"] or "").lower() or "不回退" in (st["reason"] or "")


def test_stats_windows_no_short_window_fill():
    series = [(date(2026, 11, 7), 1.0), (date(2026, 11, 10), 2.0),
              (date(2026, 11, 11), -3.0), (date(2026, 11, 12), 4.0),
              (date(2026, 11, 13), -5.0)]
    st = I.stats_for_index(series, I.INDEX_BOARDS["csi300"])
    assert st["status"] == "ok" and st["asof"] == "2026-11-13"
    assert st["net_1d"] == pytest.approx(-5.0)
    assert st["net_5d"] == pytest.approx(-1.0)
    assert st["net_20d"] is None  # not enough days
    assert "hs300" in st["definition"] and "特大单" in st["definition"]


def test_refresh_offline_no_network(monkeypatch):
    called = []

    def boom(*_a, **_k):
        called.append(1)
        raise AssertionError("should not network")

    monkeypatch.setattr(I, "fetch_main_flow_history", boom)
    seed = {
        "series": {"csi300": [["2026-11-10", 1.0], ["2026-11-11", 2.0],
                              ["2026-11-12", 3.0], ["2026-11-13", -4.0],
                              ["2026-11-14", 5.0]]},
        "asof": {"csi300": "2026-11-14"},
    }
    out = I.refresh_index_main_history(live=False, write=False, seed=seed, today=TODAY, fetch=boom)
    assert called == []
    assert out["stats"]["csi300"]["net_1d"] == pytest.approx(5.0)
    assert out["stats"]["sse50"]["status"] == "missing"
    assert out["from_snapshot"] is True


def test_refresh_live_incremental_merges(monkeypatch):
    seen = []

    def fetch(bankuai, pages=None, page_size=None, **_k):
        seen.append((bankuai, pages, page_size))
        if bankuai == "hs300":
            return [(date(2026, 11, 13), -31.1)]
        if bankuai == "zhishu_000688":
            return [(date(2026, 11, 13), -18.5)]
        if bankuai == "zhishu_399006":
            return [(date(2026, 11, 13), -62.0)]
        raise AssertionError(bankuai)

    seed = {"series": {"csi300": [["2026-11-12", 1.0]]}}
    out = I.refresh_index_main_history(live=True, write=False, seed=seed, today=TODAY, fetch=fetch)
    assert out["live_ok"] is True
    bks = [s[0] for s in seen]
    assert "hs300" in bks and "zhishu_000016" not in bks and "zhishu_000905" not in bks
    # existing seed + new point
    assert out["stats"]["csi300"]["net_1d"] == pytest.approx(-31.1)
    assert any(p == 1 and sz == 260 for _, p, sz in seen)


def test_attach_overwrites_eastmoney_index_main():
    bundle = {"indices": {
        "csi300": {"id": "csi300", "name": "沪深300",
                   "main": {"status": "ok", "asof": "2026-11-11", "net_1d": 110.13, "source": "东方财富"}},
        "sse50": {"id": "sse50", "name": "上证50", "main": {"status": "ok", "net_1d": -18.13, "source": "东方财富"}},
    }}
    payload = {
        "from_snapshot": True,
        "snapshot_asof": "2026-11-13",
        "stats": {
            "csi300": I.stats_for_index(
                [(date(2026, 11, i), float(i)) for i in range(1, 14)],
                I.INDEX_BOARDS["csi300"],
            ),
            "sse50": I.stats_for_index([], I.INDEX_BOARDS["sse50"]),
        },
    }
    out = I.attach_index_mains(bundle, payload)
    assert out["indices"]["csi300"]["main"]["source"] == "新浪财经 板块资金流向"
    assert out["indices"]["csi300"]["main"]["from_snapshot"] is True
    assert "特大单" in out["indices"]["csi300"]["main"]["definition"]
    assert out["indices"]["sse50"]["main"]["status"] == "missing"
    assert "东方财富" not in (out["indices"]["sse50"]["main"].get("source") or "") or out["indices"]["sse50"]["main"]["status"] == "missing"
    # input not mutated
    assert bundle["indices"]["csi300"]["main"]["source"] == "东方财富"


def test_fetch_fflow_path_disabled():
    with pytest.raises(RuntimeError, match="已停用"):
        F.fetch_fflow("1.000300")
    with pytest.raises(RuntimeError, match="已停用"):
        F.fetch_main_all()


def test_default_fetchers_never_include_push2(monkeypatch):
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("SKIP_PUSH2", raising=False)
    assert "main_all" not in F.default_fetchers()
    monkeypatch.setenv("SKIP_PUSH2", "1")
    assert "main_all" not in F.default_fetchers()
