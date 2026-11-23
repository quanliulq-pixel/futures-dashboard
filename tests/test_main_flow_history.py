"""沪深主力净流入（新浪特大单 r0）：解析、合计口径、窗口合计、离线 seed。"""
from datetime import date, datetime, timezone, timedelta

import pytest

import main_flow_history as F

TODAY = date(2026, 11, 14)


def test_parse_sina_zjlrqs_converts_yuan_and_skips_bad():
    js = [
        {"opendate": "2026-11-13", "r0_net": "-8249990743.3400", "netamount": "-38744556751.6720"},
        {"opendate": "bad", "r0_net": "1"},
        {"opendate": "2026-11-12", "r0_net": None},
        {"opendate": "2026-11-11", "r0_net": "27096890000"},
    ]
    rows = F.parse_sina_zjlrqs(js)
    assert [d for d, _ in rows] == [date(2026, 11, 11), date(2026, 11, 13)]
    assert rows[0][1] == pytest.approx(270.9689)
    assert rows[-1][1] == pytest.approx(-82.4999074334)


def test_total_is_common_dates_only():
    sh = [(date(2026, 11, 11), 10.0), (date(2026, 11, 12), -4.0), (date(2026, 11, 13), -8.0)]
    sz = [(date(2026, 11, 11), 20.0), (date(2026, 11, 12), -11.0)]
    tot = F.total_series(sh, sz)
    assert [d for d, _ in tot] == [date(2026, 11, 11), date(2026, 11, 12)]
    assert tot[-1][1] == pytest.approx(-15.0)


def test_window_sum_requires_full_window():
    series = [(date(2026, 11, d), float(d)) for d in (10, 11, 12, 13)]
    assert F.window_sum(series, 1) == pytest.approx(13.0)
    assert F.window_sum(series, 5) is None
    assert F.window_sum(series, 4) == pytest.approx(10 + 11 + 12 + 13)


def test_refresh_offline_uses_seed_and_labels_snapshot(monkeypatch):
    seed = {
        "sh": [["2026-11-11", 270.9689], ["2026-11-12", -42.7308], ["2026-11-13", -82.4999]],
        "sz": [["2026-11-11", 420.2252], ["2026-11-12", -115.8023], ["2026-11-13", -133.3968]],
    }
    monkeypatch.setattr(F, "load_history_seed", lambda paths=None: seed)
    out = F.refresh_history(live=False, write=False, today=date(2026, 11, 23), seed=seed)
    assert out["from_snapshot"] is True and out["live_ok"] is False
    tot = out["stats"]["main_total"]
    assert tot["status"] == "ok" and tot["asof"] == "2026-11-13"
    assert tot["net_1d"] == pytest.approx(-215.8967)
    assert tot["label"] == "两市合计"
    assert "特大单" in (out["sources"]["definition"] or "")


def test_refresh_live_incremental_merges(tmp_path, monkeypatch):
    seed = {"sh": [["2026-11-12", -42.7]], "sz": [["2026-11-12", -115.8]]}
    monkeypatch.setattr(F, "RUNTIME_PATH", tmp_path / "latest.json")
    seen = []

    def fetch(bankuai, pages=8, page_size=2000):
        seen.append((bankuai, pages, page_size))
        if bankuai.endswith("000001"):
            return [(date(2026, 11, 13), -82.5)]
        return [(date(2026, 11, 13), -133.4)]

    out = F.refresh_history(live=True, write=True, today=TODAY, seed=seed, fetch=fetch)
    assert out["live_ok"] is True and not out["from_snapshot"]
    assert all(p == 1 and sz == 260 for _, p, sz in seen)
    assert out["stats"]["main_sh"]["net_1d"] == pytest.approx(-82.5)
    assert out["stats"]["main_total"]["net_1d"] == pytest.approx(-215.9)
    assert (tmp_path / "latest.json").is_file()


def test_attach_aliases_market_main_to_sina_total():
    bundle = {"market": {
        "main": {"status": "ok", "asof": "2026-11-11", "net_1d": 198.47, "source": "东方财富"},
        "margin_sh": {"status": "ok", "balance": 1},
    }}
    payload = {
        "from_snapshot": True,
        "snapshot_asof": "2026-11-13",
        "coverage_start": "2010-03-08",
        "stats": {
            "main_sh": {"status": "ok", "asof": "2026-11-13", "net_1d": -82.5, "net_5d": 1.0, "net_20d": 2.0,
                        "label": "沪市", "source": "新浪财经 板块资金流向",
                        "source_url": "https://vip.stock.finance.sina.com.cn/moneyflow/"},
            "main_sz": {"status": "ok", "asof": "2026-11-13", "net_1d": -133.4, "net_5d": 3.0, "net_20d": 4.0,
                        "label": "深市"},
            "main_total": {"status": "ok", "asof": "2026-11-13", "net_1d": -215.9, "net_5d": 4.0, "net_20d": 6.0,
                           "label": "两市合计"},
        },
    }
    out = F.attach_main_flow(bundle, payload)
    assert out["market"]["main"]["net_1d"] == pytest.approx(-215.9)
    assert out["market"]["main"]["asof"] == "2026-11-13"
    assert out["market"]["main"]["from_snapshot"] is True
    assert out["market"]["main_sh"]["net_1d"] == pytest.approx(-82.5)
    assert out["market"]["main_sh"]["from_snapshot"] is True
    assert out["main_flow_from_snapshot"] is True
    assert bundle["market"].get("main_sh") is None


def test_committed_seed_covers_2010_and_latest():
    seed = F.load_history_seed(paths=(F.SEED_PATH,))
    assert seed is not None
    sh, sz = F.seed_series(seed)
    assert sh[0][0] == date(2010, 3, 8)
    assert sz[0][0] == date(2010, 3, 24)
    assert sh[-1][0] == date(2026, 11, 20)
    assert sz[-1][0] == date(2026, 11, 20)
    st = seed["stats"]
    assert st["main_sh"]["net_1d"] == pytest.approx(64.8976, abs=1e-4)
    assert st["main_sz"]["net_1d"] == pytest.approx(31.5220, abs=1e-4)
    assert st["main_total"]["net_1d"] == pytest.approx(96.4197, abs=1e-3)
    assert st["main_sh"]["net_5d"] is not None and st["main_sh"]["net_20d"] is not None
    assert "特大单" in seed["sources"]["definition"]


def test_public_bundle_and_endpoint_no_network(monkeypatch):
    called = []

    def boom(*a, **k):
        called.append(1)
        raise AssertionError("should not network")

    monkeypatch.setattr(F, "fetch_main_flow_history", boom)
    bundle = F.public_history_bundle(live=False)
    assert called == []
    assert bundle["status"] == "ok"
    sh = bundle["markets"]["main_sh"]
    assert sh["label"] == "沪市" and sh["series"][0][0] == "2010-03-08"
    assert sh["series"][-1][0] == "2026-11-20"
    assert sh["series"][-1][1] == pytest.approx(64.8976, abs=1e-4)

    import os
    os.environ["SKIP_BG_WARMUP"] = "1"
    import app as app_mod
    monkeypatch.setattr(app_mod, "_spawn_bg_refresh", lambda *a, **k: "throttled")
    r = app_mod.app.test_client().get("/api/main-flow-history")
    assert r.status_code == 200
    data = r.get_json()
    assert data["status"] == "ok"
    assert data["markets"]["main_total"]["net_1d"] == pytest.approx(96.4197, abs=1e-3)
    assert data["markets"]["main_total"]["asof"] == "2026-11-20"
    assert called == []
    assert not data.get("from_snapshot")


def test_ensure_offline_does_not_call_network(monkeypatch):
    called = []

    def boom(*a, **k):
        called.append(1)
        raise AssertionError("should not network")

    monkeypatch.setattr(F, "fetch_main_flow_history", boom)
    b = {"market": {"main": {"status": "missing"}}}
    out = F.ensure_main_flow(b, live=False)
    assert called == []
    assert out["market"]["main_total"]["status"] == "ok"
    assert out["market"]["main"]["status"] == "ok"
    assert out["market"]["main"]["net_1d"] == out["market"]["main_total"]["net_1d"]
    assert out["market"]["main"]["asof"] == out["market"]["main_total"]["asof"]
    # 输入对象不被改写
    assert b["market"]["main"]["status"] == "missing"

def test_flow_needs_refresh_and_fresh_offline_not_snapshot():
    cst = timezone(timedelta(hours=8))
    assert F.expected_published_asof(datetime(2026, 11, 23, 15, 0, tzinfo=cst)) == date(2026, 11, 20)
    assert F.needs_refresh({"asof": {"sh": "2026-11-13", "sz": "2026-11-13"}},
                           now=datetime(2026, 11, 23, 15, 0, tzinfo=cst))
    assert not F.needs_refresh({"asof": {"sh": "2026-11-20", "sz": "2026-11-20"}},
                               now=datetime(2026, 11, 23, 15, 0, tzinfo=cst))
    seed = {
        "fetched_at": "2026-11-16T10:00:00+08:00",
        "asof": {"sh": "2026-11-13", "sz": "2026-11-13"},
        "sh": [["2026-11-11", 270.9689], ["2026-11-13", -82.4999]],
        "sz": [["2026-11-11", 420.2252], ["2026-11-13", -133.3968]],
    }
    out = F.refresh_history(live=False, write=False, today=date(2026, 11, 16), seed=seed)
    assert out["from_snapshot"] is False and out["fetched_at"] == "2026-11-16T10:00:00+08:00"


def test_flow_live_failure_labels_snapshot():
    seed = {
        "sh": [["2026-11-13", -82.4999]],
        "sz": [["2026-11-13", -133.3968]],
    }

    def boom(*a, **k):
        raise RuntimeError("blocked")

    out = F.refresh_history(live=True, write=False, today=date(2026, 11, 23), seed=seed, fetch=boom)
    assert out["live_ok"] is False and out["from_snapshot"] is True
    assert out["snapshot_asof"] == "2026-11-13"
