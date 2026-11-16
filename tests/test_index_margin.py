# -*- coding: utf-8 -*-
"""沪深300 / 上证50 / 中证1000 两融余额与比例（非 push2）。"""
from __future__ import annotations

from datetime import date

import pytest

import index_margin as M


TODAY = date(2026, 11, 14)


def _pt(d, margin, fin=None, loan=None, circ=None, n_cons=None, n_mgn=None):
    fin = margin * 0.98 if fin is None else fin
    loan = margin - fin if loan is None else loan
    circ = 10000.0 if circ is None else circ
    m_ratio = margin / circ * 100.0
    f_ratio = fin / circ * 100.0
    return M.DayPoint(d, margin, fin, loan, circ, m_ratio, f_ratio, n_cons, n_mgn)


def test_methods_and_no_push2():
    assert M.INDEX_MARGIN["csi300"]["board_code"] == "500"
    assert M.INDEX_MARGIN["sse50"]["board_code"] == "611"
    assert M.INDEX_MARGIN["csi1000"]["method"] == "aggregate_constituents"
    assert M.INDEX_MARGIN["csi1000"]["board_code"] is None
    blob = (M.SRC["definition"] + M.definition_for(M.INDEX_MARGIN["csi300"])
            + M.definition_for(M.INDEX_MARGIN["csi1000"])).lower()
    assert "push2" in blob and "不使用东方财富 push2" in M.SRC["definition"]
    assert "流通A股" in M.INDEX_MARGIN["csi300"]["denominator"]
    assert "当前" in M.definition_for(M.INDEX_MARGIN["csi1000"]) and "调样" in M.definition_for(M.INDEX_MARGIN["csi1000"])


def test_stats_windows_are_diffs_not_sums():
    pts = [_pt(date(2026, 10, 16), 8000.0 + i * 10, circ=400000.0 + i * 100)
           for i in range(21)]
    # last = 8000+200=8200, t-1=8190, t-5=8150, t-20=8000
    st = M.stats_for_index(pts, M.INDEX_MARGIN["csi300"])
    assert st["status"] == "ok"
    # dates are calendar +i days from Oct 16 → last is Nov 5, not Nov 13.
    assert st["asof"] == pts[-1].d.isoformat()
    assert st["margin_balance"] == pytest.approx(8200.0)
    assert st["chg_1d"] == pytest.approx(10.0)
    assert st["chg_5d"] == pytest.approx(50.0)
    assert st["chg_20d"] == pytest.approx(200.0)
    assert st["margin_ratio"] == pytest.approx(8200.0 / (400000.0 + 20 * 100) * 100)
    assert st["fin_share"] == pytest.approx(98.0)
    assert "NOTLIMITED_MARKETCAP_A" in st["definition"]
    short = pts[-4:]
    st2 = M.stats_for_index(short, M.INDEX_MARGIN["sse50"])
    assert st2["chg_1d"] == pytest.approx(10.0)
    assert st2["chg_5d"] is None and st2["chg_20d"] is None
    assert st2["ratio_chg_5d"] is None


def test_missing_csi1000_without_series_keeps_reason():
    st = M.stats_for_index([], M.INDEX_MARGIN["csi1000"])
    assert st["status"] == "missing"
    assert "中证1000" in (st["reason"] or "")
    assert st["margin_balance"] is None


def test_parse_board_row_units_and_ratio():
    row = {
        "TRADE_DATE": "2026-11-13 00:00:00",
        "BOARD_NAME": "HS300_",
        "MARGIN_BALANCE": 824055739192.66,
        "FIN_BALANCE": 811284130191,
        "LOAN_BALANCE": 12771609001.66,
        "NOTLIMITED_MARKETCAP_A": 48182244154534.07,
        "FIN_BALANCE_RATIO": 1.68378237,
    }
    pt = M.point_from_board_row(row)
    assert pt is not None and pt.d == date(2026, 11, 13)
    assert pt.margin == pytest.approx(8240.5573919266)
    assert pt.fin == pytest.approx(8112.84130191)
    assert pt.circ == pytest.approx(481822.4415453407)
    assert pt.fin_ratio == pytest.approx(1.68378237)
    assert pt.margin_ratio == pytest.approx(8240.5573919266 / 481822.4415453407 * 100)


def test_parse_csindex_codes_zfill_and_date():
    rows = [
        {"日期Date": 20261111, "成份券代码Constituent Code": 12},
        {"日期Date": 20261111, "成份券代码Constituent Code": "688001"},
        {"日期Date": 20261111, "成份券代码Constituent Code": 12},  # dup
    ]
    dates: list = []
    codes = M.parse_csindex_codes(rows, date_out=dates)
    assert codes == ["000012", "688001"]
    assert dates == [date(2026, 11, 11)]


def test_aggregate_ggmx_filters_to_constituents():
    rows = [
        {"SCODE": "000012", "RZRQYE": 2.0e8, "RZYE": 1.9e8, "RQYE": 0.1e8, "SZ": 50.0e8},
        {"SCODE": "000001", "RZRQYE": 9.0e8, "RZYE": 8.0e8, "RQYE": 1.0e8, "SZ": 200.0e8},  # not in index
        {"SCODE": "688001", "RZRQYE": 3.0e8, "RZYE": 3.0e8, "RQYE": 0.0, "SZ": 70.0e8},
    ]
    pt = M.aggregate_ggmx_rows(rows, {"000012", "688001", "000028"}, date(2026, 11, 13), n_cons=3)
    assert pt is not None
    assert pt.margin == pytest.approx(5.0)  # 亿元
    assert pt.fin == pytest.approx(4.9)
    assert pt.circ == pytest.approx(120.0)
    assert pt.n_cons == 3 and pt.n_mgn == 2
    assert pt.margin_ratio == pytest.approx(5.0 / 120.0 * 100)


def test_refresh_offline_no_network(monkeypatch):
    called = []

    def boom(*_a, **_k):
        called.append(1)
        raise AssertionError("should not network")

    monkeypatch.setattr(M, "fetch_board_history", boom)
    monkeypatch.setattr(M, "fetch_csindex_constituents", boom)
    monkeypatch.setattr(M, "fetch_ggmx_day", boom)
    import margin_history
    monkeypatch.setattr(margin_history, "fetch_index_closes", boom)
    seed = {
        "series": {
            "csi300": [["2026-11-10", 8000.0, 7900.0, 100.0, 480000.0, 1.6667, 1.6458],
                       ["2026-11-11", 8010.0, 7910.0, 100.0, 481000.0, 1.6653, 1.6445],
                       ["2026-11-12", 8020.0, 7920.0, 100.0, 482000.0, 1.6639, 1.6432],
                       ["2026-11-13", 8030.0, 7930.0, 100.0, 483000.0, 1.6625, 1.6418]],
        },
        "asof": {"csi300": "2026-11-13"},
    }
    out = M.refresh_index_margin_history(
        live=False, write=False, seed=seed, today=TODAY,
        fetch_board=boom, fetch_cons=boom, fetch_day=boom,
    )
    assert called == []
    assert out["stats"]["csi300"]["margin_balance"] == pytest.approx(8030.0)
    assert out["stats"]["csi300"]["chg_1d"] == pytest.approx(10.0)
    assert out["stats"]["sse50"]["status"] == "missing"
    assert out["stats"]["csi1000"]["status"] == "missing"
    assert out["from_snapshot"] is True


def test_refresh_live_incremental_board_and_aggregate(monkeypatch):
    seen_board = []
    seen_days = []

    def fetch_board(code, start=None, **_k):
        seen_board.append((code, start))
        if code == "500":
            return [_pt(date(2026, 11, 13), 8240.56, circ=481822.44)]
        if code == "611":
            return [_pt(date(2026, 11, 13), 2760.65, circ=230646.44)]
        raise AssertionError(code)

    def fetch_cons(index_code, **_k):
        assert index_code == "000852"
        return ["000012", "688001"], date(2026, 11, 11)

    def fetch_day(d, codes, **_k):
        seen_days.append(d)
        assert codes == {"000012", "688001"}
        return _pt(d, 3500.0 + d.day, circ=120000.0, n_cons=2, n_mgn=2)

    seed = {
        "series": {
            "csi300": [["2026-11-12", 8230.0, 8100.0, 130.0, 480000.0, 1.7146, 1.6875]],
        }
    }
    out = M.refresh_index_margin_history(
        live=True, write=False, seed=seed, today=TODAY, agg_days=5,
        fetch_board=fetch_board, fetch_cons=fetch_cons, fetch_day=fetch_day,
        fetch_index=lambda sym, datalen=None: [(date(2026, 11, 13), 4000.0)],
    )
    assert out["live_ok"] is True
    assert out["index"]["csi300"] == [["2026-11-13", 4000.0]]
    assert ("500", date(2026, 11, 12)) in seen_board
    assert ("611", None) in seen_board
    # calendar comes from merged csi300 (seed 11-12 + new 11-13); last csi1000 empty → last 5 of calendar (only 2 dates)
    assert seen_days == [date(2026, 11, 12), date(2026, 11, 13)]
    assert out["stats"]["csi300"]["margin_balance"] == pytest.approx(8240.56)
    assert out["stats"]["csi300"]["chg_1d"] == pytest.approx(10.56)
    assert out["stats"]["sse50"]["margin_balance"] == pytest.approx(2760.65)
    assert out["stats"]["csi1000"]["status"] == "ok"
    assert out["stats"]["csi1000"]["constituent_n"] == 2
    assert out["stats"]["csi1000"]["constituent_date"] == "2026-11-11"
    assert "调样" in (out["stats"]["csi1000"]["constituent_caveat"] or "")
    pub = M.public_history_bundle(live=False, seed=out, today=TODAY)
    assert "codes" not in (pub.get("constituents") or {}).get("csi1000", {})
    assert pub["status"] == "ok"


def test_attach_and_snapshot_flag():
    bundle = {"indices": {"csi300": {"id": "csi300", "name": "沪深300", "main": {"status": "ok"}}}}
    payload = M.refresh_index_margin_history(
        live=False, write=False, today=TODAY,
        seed={"series": {"csi300": [["2026-11-13", 8240.0, 8112.0, 128.0, 481822.0, 1.71, 1.68]]}},
    )
    assert payload["from_snapshot"] is True
    out = M.attach_index_margins(bundle, payload)
    m = out["indices"]["csi300"]["margin"]
    assert m["status"] == "ok" and m["from_snapshot"] is True
    assert m["margin_balance"] == pytest.approx(8240.0)
    assert out["index_margins"]["csi300"]["asof"] == "2026-11-13"
    assert out["index_margin_from_snapshot"] is True
    assert "流通" in out["index_margin_definition"]
    # 上证50 / 中证1000 仍挂 missing，不虚构
    assert out["indices"]["sse50"]["margin"]["status"] == "missing"
    assert out["indices"]["csi1000"]["margin"]["status"] == "missing"


def test_ensure_offline_does_not_call_network(monkeypatch):
    called = []

    def boom(*_a, **_k):
        called.append(1)
        raise AssertionError("network")

    monkeypatch.setattr(M, "fetch_board_history", boom)
    monkeypatch.setattr(M, "fetch_csindex_constituents", boom)
    monkeypatch.setattr(M, "fetch_ggmx_day", boom)
    monkeypatch.setattr(M, "load_index_margin_seed", lambda paths=None: {
        "series": {"sse50": [["2026-11-13", 2760.0, 2708.0, 52.0, 230646.0, 1.1966, 1.1743]]},
        "asof": {"sse50": "2026-11-13"},
    })
    bundle = {"indices": {"sse50": {"id": "sse50", "name": "上证50"}}}
    out = M.ensure_index_margins(bundle, live=False)
    assert called == []
    assert out["indices"]["sse50"]["margin"]["margin_balance"] == pytest.approx(2760.0)


# ---------------- 历史图表 + 关系统计
import index_margin_relation as R
from datetime import timedelta


def _series(n=300, start=date(2025, 1, 1)):
    pts, closes = [], []
    for i in range(n):
        d = start + timedelta(days=i)
        m = 1000.0 + i + (50 if i % 7 == 3 else 0)
        pts.append(M.DayPoint(d, m, m * 0.98, m * 0.02, 100000.0, m / 1000.0, None))
        closes.append((d, 3000.0 + 2 * i))
    return pts, closes


def test_pearson_and_percentile():
    assert R.pearson([1, 2, 3], [1, 2, 3]) is None  # too few
    xs = list(range(30))
    assert R.pearson(xs, [2 * x for x in xs]) == pytest.approx(1.0)
    assert R.pearson(xs, [-x for x in xs]) == pytest.approx(-1.0)
    assert R.percentile(list(range(1, 101)), 90) == pytest.approx(90.0)
    assert R.percentile([1, 2], 2) is None


def test_relation_extrema_use_same_day_close_and_earliest_tie():
    pts, closes = _series()
    closes = [c for c in closes if c[0] != pts[0].d]  # first day has no close
    rel = R.relation_for_index(pts, closes)
    assert rel["status"] == "ok"
    lo = rel["margin_low"]
    assert lo["date"] == pts[0].d.isoformat() and lo["index_close"] is None  # 不拿邻日顶替
    hi = rel["margin_high"]
    assert hi["value"] == pytest.approx(max(p.margin for p in pts))
    assert hi["index_close"] is not None
    w = rel["windows"]["all"]
    assert w["margin_pctile"] is not None and 0 <= w["margin_pctile"] <= 100
    assert w["corr_level"] > 0.9
    assert w["corr_chg20"] is not None and w["corr_lead20"] is not None and w["corr_weekly"] is not None
    assert "不能下单" in rel["note"]


def test_relation_short_history_returns_none_not_fabricated():
    pts, closes = _series(n=10)
    rel = R.relation_for_index(pts, closes)
    w = rel["windows"]["all"]
    assert w["corr_level"] is None and w["corr_chg20"] is None and w["margin_pctile"] is None
    assert R.relation_for_index([], [])["status"] == "missing"


def test_public_bundle_has_chart_and_relation_no_raw_series():
    pts, closes = _series()
    seed = {
        "series": {"csi300": M.rows_from_points(pts)},
        "index": {"csi300": [[d.isoformat(), c] for d, c in closes]},
        "asof": {"csi300": pts[-1].d.isoformat()},
    }
    pub = M.public_history_bundle(live=False, seed=seed, today=TODAY)
    c = pub["chart"]["csi300"]
    assert c["series"][-1][0] == pts[-1].d.isoformat() and c["series"][-1][3] == pytest.approx(closes[-1][1])
    assert c["relation"]["status"] == "ok"
    assert pub["chart"]["csi1000"]["relation"]["status"] == "missing"
    assert "series" not in pub and "index" not in pub


def test_relation_low_uses_2014_window_not_pilot_start():
    # 2010-03-31 极小余额不应成为「历史最低」
    pts = [
        M.DayPoint(date(2010, 3, 31), 0.06, 0.05, 0.01, 10000.0, 0.0006, 0.0005),
        M.DayPoint(date(2014, 9, 21), 10.0, 9.0, 1.0, 10000.0, 0.1, 0.09),
        M.DayPoint(date(2014, 9, 22), 80.0, 78.0, 2.0, 20000.0, 0.4, 0.39),
        M.DayPoint(date(2015, 6, 19), 200.0, 190.0, 10.0, 30000.0, 0.667, 0.63),
    ] + [M.DayPoint(date(2020, 1, 1) + timedelta(days=i), 150.0 + i, 140.0, 10.0, 40000.0, 0.4, 0.35)
         for i in range(30)]
    closes = [(p.d, 3000.0 + i) for i, p in enumerate(pts)]
    rel = R.relation_for_index(pts, closes)
    assert rel["low_coverage_start"] == "2014-09-22"
    assert rel["margin_low"]["date"] == "2014-09-22"
    assert rel["margin_low"]["value"] == pytest.approx(80.0)
    assert rel["margin_high"]["date"] == pts[-1].d.isoformat() or rel["margin_high"]["value"] >= 150
    assert rel["ratio_low"]["date"] != "2010-03-31"


def test_csi1000_backfill_resumes_from_checkpoint(tmp_path):
    ck = tmp_path / "bf.json"
    cal = [date(2026, 11, 10), date(2026, 11, 11), date(2026, 11, 12), date(2026, 11, 13)]
    codes = {"000012"}
    seen = []

    def fetch(d, cs, **_k):
        seen.append(d)
        if d == date(2026, 11, 11) and seen.count(d) == 1 and len(seen) == 2:
            raise RuntimeError("interrupt")
        return _pt(d, 1000.0 + d.day, circ=10000.0, n_cons=1, n_mgn=1)

    # first run dies mid-way; days before the failure should be checkpointed
    try:
        M.backfill_csi1000_history(start=date(2026, 11, 10), codes=codes, calendar=cal,
                                   fetch_day=fetch, checkpoint=ck, write_every=1)
        raise AssertionError("should have interrupted")
    except RuntimeError:
        pass
    saved = M.load_backfill_points(ck)
    assert [p.d for p in saved] == [date(2026, 11, 10)]
    seen.clear()
    out = M.backfill_csi1000_history(start=date(2026, 11, 10), codes=codes, calendar=cal,
                                     existing=saved, fetch_day=fetch, checkpoint=ck, write_every=1)
    assert date(2026, 11, 10) not in seen  # 已完成的日期不重抓
    assert seen == [date(2026, 11, 11), date(2026, 11, 12), date(2026, 11, 13)]
    assert [p.d for p in out] == cal
