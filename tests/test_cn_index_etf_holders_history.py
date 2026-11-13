"""机构持仓 vs 指数涨跌历史：种子、对齐重建、挂到页面、失败不抹掉资金流。不联网。"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

import cn_index_etf_holders as H
import cn_index_etf_holders_history as HH
import cn_index_flow as F
import export_static_pages as export


ROOT = Path(__file__).resolve().parents[1]


def test_history_seed_has_verified_2026h1_and_stats():
    seed = HH.load_history_seed()
    assert seed and seed["status"] == "ok"
    assert seed["in_score"] is False
    assert seed["partial_asof"] == "2026-11-11"
    assert seed["mark_date"] == "2026-06-30"
    order = seed["order"]
    assert order == ["csi300", "sse50", "csi500", "csi1000", "star50", "chinext"]
    total = sum(len(seed["indices"][i]["series"]) for i in order)
    assert total == 160

    expect = {
        "csi300": (348.75, 72.76, -1968.88, -21.12, 0.0755, -0.1815),
        "sse50": (30.39, 39.41, -503.15, -51.75, -0.0138, -0.0749),
        "csi500": (61.58, 63.78, -203.97, -23.98, 0.2097, -0.2567),
        "csi1000": (55.00, 75.08, -462.26, -21.51, 0.1599, -0.2429),
        "star50": (259.80, 47.34, -337.42, -3.69, 0.6425, -0.4110),
        "chinext": (104.86, 57.47, -219.78, -14.43, 0.3558, -0.4077),
    }
    for iid, (shares, pct, dsh, dpct, same, nxt) in expect.items():
        row = next(r for r in seed["indices"][iid]["series"] if r["report_date"] == "2026-06-30")
        assert row["inst_shares"] == pytest.approx(shares, abs=0.01)
        assert row["inst_pct"] == pytest.approx(pct, abs=0.01)
        assert row["d_inst_shares"] == pytest.approx(dsh, abs=0.01)
        assert row["d_inst_pct"] == pytest.approx(dpct, abs=0.01)
        assert row["same_ret"] == pytest.approx(same, abs=0.0002)
        assert row["next_ret"] == pytest.approx(nxt, abs=0.0002)
        assert row["next_ret_kind"] == "partial"
        assert row["next_end_date"] == "2026-11-11"

    st = seed["stats"]
    assert st["same_period"]["pooled"]["n"] == 154
    assert st["same_period"]["pooled"]["display_r"] == pytest.approx(0.074)
    assert st["same_period"]["pooled"]["display_p"] == pytest.approx(0.36)
    assert st["same_period"]["pooled"]["same_dir"] == 87
    assert st["same_period"]["period_avg"]["n"] == 42
    assert st["same_period"]["period_avg"]["display_r"] == pytest.approx(0.087)
    assert st["next_period"]["exclude_unfinished"] is True
    assert st["next_period"]["period_avg"]["n"] == 41
    assert st["next_period"]["period_avg"]["display_r"] == pytest.approx(0.307)
    assert st["next_period"]["period_avg"]["display_p"] == pytest.approx(0.051)
    text = "\n".join(seed["notes"])
    assert "没有稳定关系" in text
    assert "减持预示下跌" in text
    assert "汇金" not in text
    assert "未完结" in text or "尚未走完" in text


def test_load_history_bundle_is_seed_only(monkeypatch):
    def deny(*a, **k):
        raise AssertionError("must not fetch")

    monkeypatch.setattr(HH, "fetch_sina_index_kline", deny)
    monkeypatch.setattr(HH, "refresh_history_bundle", deny)
    # reset cache to force a file read
    HH._cache = None
    b = HH.load_history_bundle()
    assert b["status"] == "ok" and b["indices"]["csi300"]["series"]
    assert HH.load_history_bundle() is b


def test_attach_history_does_not_blank_flows():
    bundle = {
        "indices": [{"id": "csi300", "name": "沪深300"}],
        "index_flows": {
            "indices": {
                "csi300": {
                    "id": "csi300", "name": "沪深300",
                    "main": {"status": "ok", "net_1d": 1},
                    "etf": {"status": "ok", "net_1d": -2},
                    "holders": {"status": "ok", "inst_shares": 104.86, "report_date": "2026-06-30"},
                },
                "chinext": {
                    "id": "chinext", "name": "创业板指",
                    "main": {"status": "missing"},
                    "etf": {"status": "missing", "reason": "深交所不提供历史份额"},
                    "holders": {"status": "ok", "inst_shares": 104.86, "report_date": "2026-06-30"},
                },
            },
            "notes": ["keep-me"],
        },
    }
    out = HH.attach_holder_history(bundle)
    flows = out["index_flows"]["indices"]
    assert flows["csi300"]["main"]["net_1d"] == 1
    assert flows["csi300"]["etf"]["net_1d"] == -2
    assert flows["csi300"]["holders"]["inst_shares"] == 104.86
    assert flows["chinext"]["etf"]["status"] == "missing"
    assert flows["chinext"]["holders"]["status"] == "ok"
    assert out["holder_history"]["status"] == "ok"
    assert "keep-me" in out["index_flows"]["notes"]
    # missing history must not wipe flows
    blank = HH.attach_holder_history(bundle, {"status": "missing", "reason": "无种子", "indices": {}})
    assert blank["index_flows"]["indices"]["chinext"]["holders"]["inst_shares"] == 104.86
    assert blank["holder_history"]["status"] == "missing"


def test_rebuild_overlap_same_ret_and_partial_next():
    # A present both periods; B only in the second period — change uses A only.
    funds = {
        "A": [
            {"date": "2025-12-31", "inst_pct": 80.0, "total_shares": 10.0},
            {"date": "2026-06-30", "inst_pct": 50.0, "total_shares": 10.0},
        ],
        "B": [
            {"date": "2026-06-30", "inst_pct": 90.0, "total_shares": 20.0},
        ],
    }
    # close on period ends + a later unfinished window
    klines = {
        "csi300": [
            (date(2025, 12, 31), 100.0),
            (date(2026, 6, 30), 110.0),
            (date(2026, 11, 11), 88.0),
        ],
    }
    # Only attach these funds onto csi300 by temporarily using real codes? The builder
    # uses HOLDER_INDICES ETF lists. Use real 沪深300 codes so they are included.
    funds = {
        "510300": funds["A"],
        "510310": funds["B"],
    }
    built = HH.build_history_from_funds_and_klines(
        funds, klines, partial_asof="2026-11-11", stats={}, notes=["t"]
    )
    series = built["indices"]["csi300"]["series"]
    assert [r["report_date"] for r in series] == ["2025-12-31", "2026-06-30"]
    first, second = series
    # levels: first = only 510300 (8 shares); second = 5 + 18 = 23 shares, pct = 23/30*100
    assert first["inst_shares"] == pytest.approx(8.0)
    assert first["n_funds"] == 1
    assert second["inst_shares"] == pytest.approx(23.0)
    assert second["n_funds"] == 2
    assert second["inst_pct"] == pytest.approx(23 / 30 * 100)
    # overlap change: only 510300 (8 → 5)
    assert second["n_overlap"] == 1
    assert second["d_inst_shares"] == pytest.approx(-3.0)
    assert second["d_inst_pct"] == pytest.approx(50.0 - 80.0)
    assert second["same_ret"] == pytest.approx(0.10)
    assert second["next_ret"] == pytest.approx(88 / 110 - 1)
    assert second["next_ret_kind"] == "partial"
    assert second["next_end_date"] == "2026-11-11"
    assert first["next_ret"] == pytest.approx(0.10)
    assert first["next_ret_kind"] == "full"
    # other indices stay empty rather than fabricated
    assert built["indices"]["chinext"]["series"] == []


def test_refresh_keeps_seed_when_klines_fail(monkeypatch):
    seed = HH.load_history_seed()
    assert seed

    def boom(*a, **k):
        raise RuntimeError("sina down")

    monkeypatch.setattr(HH, "fetch_sina_index_kline", boom)
    out = HH.refresh_history_bundle(live_klines=True)
    assert out["status"] == "ok"
    assert out["indices"]["csi300"]["series"]
    row = next(r for r in out["indices"]["csi300"]["series"] if r["report_date"] == "2026-06-30")
    assert row["inst_shares"] == pytest.approx(348.75, abs=0.01)


def test_ensure_holders_still_independent_of_history():
    bundle = {
        "indices": {
            "chinext": {"id": "chinext", "name": "创业板指", "main": {"status": "ok"},
                        "etf": {"status": "missing", "reason": "深交所不提供历史份额"}},
        },
        "notes": [],
    }
    out = F.ensure_etf_holders(bundle)
    assert out["indices"]["chinext"]["etf"]["status"] == "missing"
    assert out["indices"]["chinext"]["holders"]["status"] == "ok"
    assert out["indices"]["chinext"]["holders"]["inst_shares"] == pytest.approx(104.86, abs=0.01)


def test_export_html_copies_history_seed(tmp_path, monkeypatch):
    monkeypatch.setattr(export, "DOCS", tmp_path)
    monkeypatch.setattr(export, "API", tmp_path / "api")

    def local_dump(path, obj):
        path.parent.mkdir(parents=True, exist_ok=True)
        import json
        path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(export, "dump", local_dump)
    export.export_html()
    dest = tmp_path / "api" / "cn_index_etf_holders_history.json"
    assert dest.is_file()
    import json
    data = json.loads(dest.read_text(encoding="utf-8"))
    assert data["indices"]["csi300"]["series"]
    assert "20261113etf1" in (tmp_path / "index.html").read_text(encoding="utf-8")

    assert (tmp_path / "static" / "cn_index_holders_charts.js").is_file()


def test_docs_snapshot_has_history_and_asset_v():
    docs = (ROOT / "docs" / "api" / "cn_index_etf_holders_history.json").read_text(encoding="utf-8")
    import json
    data = json.loads(docs)
    assert data["stats"]["same_period"]["pooled"]["n"] == 154
    html = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    assert "20261113etf1" in html

    assert "cn_index_holders_charts.js" in html
    assert "机构持仓对照" in html
    cn = json.loads((ROOT / "docs" / "api" / "cn_indices.json").read_text(encoding="utf-8"))
    assert cn["holder_history"]["indices"]["csi300"]["series"]
    # 创业板持仓不因申赎缺失被抹掉（快照里仍应有 index_flows holders）
    flows = cn.get("index_flows") or {}
    if flows.get("indices") and "chinext" in flows["indices"]:
        h = flows["indices"]["chinext"].get("holders") or {}
        if h.get("status") == "ok":
            assert h.get("inst_shares") not in (None, 0, 0.0)
