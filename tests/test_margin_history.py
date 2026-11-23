"""两融历史最高/最低及对应指数收盘：解析、合计口径、高低点、seed 回填。"""
from datetime import date
from pathlib import Path

import pytest

import margin_history as H

TODAY = date(2026, 11, 14)


def test_parse_sse_margin_page_converts_yuan_and_pages():
    js = {
        "result": [
            {"opDate": "20100331", "rzye": 5824813},
            {"opDate": "20260625", "rzye": 1521114083800},
        ],
        "pageHelp": {"pageCount": 3, "pageNo": 1},
    }
    series, n, pages = H.parse_sse_margin_page(js)
    assert n == 2 and pages == 3
    assert series[0] == (date(2010, 3, 31), pytest.approx(0.05824813))
    assert series[1][0] == date(2026, 6, 25)
    assert series[1][1] == pytest.approx(15211.140838)


def test_parse_em_margin_page_keeps_new_index_and_yuan():
    js = {"success": True, "result": {
        "pages": 9, "count": 2,
        "data": [
            {"DIM_DATE": "2010-03-31 00:00:00", "RZYE": 670796, "NEW": 12494.35},
            {"DIM_DATE": "2026-11-12 00:00:00", "RZYE": 1088393582025, "NEW": 11197.9763},
        ],
    }}
    series, idx, pages = H.parse_em_margin_page(js)
    assert pages == 9 and series[0][1] == pytest.approx(0.00670796)
    assert series[-1][1] == pytest.approx(10883.93582025)
    assert idx[date(2010, 3, 31)] == pytest.approx(12494.35)
    assert idx[date(2026, 11, 12)] == pytest.approx(11197.9763)


def test_total_is_common_dates_only_not_beijing():
    sh = [(date(2026, 11, 11), 100.0), (date(2026, 11, 12), 110.0), (date(2026, 11, 13), 120.0)]
    sz = [(date(2026, 11, 11), 50.0), (date(2026, 11, 12), 55.0)]  # 深市尚未发布 13 日
    tot = H.total_series(sh, sz)
    assert [d for d, _ in tot] == [date(2026, 11, 11), date(2026, 11, 12)]
    assert tot[-1][1] == pytest.approx(165.0)
    # 东财「全部」含北交所 7.5，不能当沪深合计
    em_all = 110.0 + 55.0 + 7.5
    assert tot[-1][1] != pytest.approx(em_all)


def test_extrema_take_earliest_tie_and_exact_index_date():
    series = [
        (date(2014, 9, 22), 100.0),
        (date(2014, 9, 23), 80.0),
        (date(2015, 1, 5), 80.0),   # 并列最低，取最早
        (date(2026, 6, 25), 200.0),
        (date(2026, 6, 26), 200.0),  # 并列最高，取最早
    ]
    idx = [
        (date(2014, 9, 23), 2300.0),
        # 2015-01-05 缺失：不拿邻日
        (date(2026, 6, 25), 4120.28),
        (date(2026, 6, 26), 4100.0),
        (date(2026, 11, 13), 3634.35),
    ]
    ext = H.extrema_for(series, idx, "margin_sh")
    assert ext["hist_low"]["date"] == "2014-09-23" and ext["hist_low"]["balance"] == pytest.approx(80.0)
    assert ext["hist_low"]["index_close"] == pytest.approx(2300.0)
    assert ext["hist_high"]["date"] == "2026-06-25" and ext["hist_high"]["index_close"] == pytest.approx(4120.28)
    assert ext["index_now"]["date"] == "2026-11-13" and ext["index_now"]["close"] == pytest.approx(3634.35)
    assert ext["index_name"] == "上证指数" and ext["index_code"] == "000001"
    # 最低并列日没有指数
    series2 = [(date(2015, 1, 5), 80.0), (date(2026, 6, 25), 200.0)]
    low = H.extrema_for(series2, idx, "margin_sh")["hist_low"]
    assert low["date"] == "2015-01-05" and low["index_close"] is None
    assert H.close_on(idx, date(2010, 4, 2)) is None
    assert H.extrema_for([], idx, "margin_sh")["hist_high"] is None


def test_hist_low_ignores_pre_expansion_even_if_smaller():
    series = [
        (date(2010, 3, 31), 0.06),     # 全样本更低，历史最低不用
        (date(2014, 9, 19), 50.0),      # 扩容日前一天，不用
        (date(2014, 9, 22), 100.0),
        (date(2018, 1, 2), 90.0),       # 扩容后最低
        (date(2026, 6, 25), 200.0),
    ]
    ext = H.extrema_for(series, [(date(2018, 1, 2), 3300.0), (date(2026, 6, 25), 4120.0)], "margin_total")
    assert ext["hist_high"]["date"] == "2026-06-25" and ext["hist_high"]["balance"] == pytest.approx(200.0)
    assert ext["hist_low"]["date"] == "2018-01-02" and ext["hist_low"]["balance"] == pytest.approx(90.0)
    assert ext["hist_low"]["index_close"] == pytest.approx(3300.0)
    assert ext["hist_coverage_start"] == "2010-03-31"
    assert ext["hist_low_coverage_start"] == "2014-09-22"
    assert ext["hist_n"] == 5 and ext["hist_low_n"] == 3


def test_fill_index_from_cached_only_same_date():
    ext = H.extrema_for(
        [(date(2014, 9, 22), 1.0), (date(2026, 6, 25), 9.0)],
        [], "margin_sz",
    )
    cached = {"margin_sz": {
        "hist_low": {"date": "2014-09-22", "balance": 1.0, "index_close": 7895.87},
        "hist_high": {"date": "2020-01-01", "balance": 8.0, "index_close": 9999.0},  # 日期不同不得回填
        "index_now": {"date": "2026-11-12", "close": 11197.98, "index_name": "深证成指", "index_code": "399001"},
    }}
    filled = H._fill_index_from_cached({"margin_sz": ext}, cached)["margin_sz"]
    assert filled["hist_low"]["index_close"] == pytest.approx(7895.87)
    assert filled["hist_high"]["index_close"] is None
    assert filled["index_now"]["close"] == pytest.approx(11197.98)


def test_refresh_offline_uses_seed_and_labels_snapshot(tmp_path, monkeypatch):
    seed = {
        "coverage_start": "2010-03-31",
        "asof": {"sh": "2026-06-25", "sz": "2026-06-25"},
        "sh": [["2010-03-31", 0.0582], ["2014-09-22", 3776.4166], ["2026-06-25", 15211.1408]],
        "sz": [["2010-03-31", 0.0067], ["2014-09-22", 2133.8169], ["2026-06-25", 14798.2858]],
        "extrema": {"margin_sh": {
            "hist_high": {"date": "2026-06-25", "balance": 15211.1408, "index_close": 4120.28},
            "hist_low": {"date": "2014-09-22", "balance": 3776.4166, "index_close": 2289.87},
            "index_now": {"date": "2026-06-25", "close": 4120.28, "index_name": "上证指数", "index_code": "000001"},
        }},
    }
    monkeypatch.setattr(H, "load_history_seed", lambda paths=None: seed)
    out = H.refresh_history(live=False, write=False, today=TODAY, seed=seed)
    assert out["from_snapshot"] is True and out["live_ok"] is False
    sh = out["extrema"]["margin_sh"]
    assert sh["hist_high"]["index_close"] == pytest.approx(4120.28)
    assert sh["hist_low"]["date"] == "2014-09-22"
    assert sh["hist_low"]["index_close"] == pytest.approx(2289.87)
    assert sh["hist_low_coverage_start"] == "2014-09-22"
    tot = out["extrema"]["margin_total"]
    assert tot["hist_high"]["date"] == "2026-06-25"
    assert tot["hist_high"]["balance"] == pytest.approx(30009.4266)
    assert tot["index_name"] == "沪深300" and tot["index_code"] == "000300"


def test_refresh_live_incremental_updates_high_and_writes(tmp_path, monkeypatch):
    seed = {
        "sh": [["2026-06-25", 100.0]],
        "sz": [["2026-06-25", 80.0]],
        "extrema": {},
    }
    monkeypatch.setattr(H, "RUNTIME_PATH", tmp_path / "latest.json")

    def fetch_sh(start, end):
        return [(date(2026, 6, 26), 120.0)]

    def fetch_sz(start, end):
        return [(date(2026, 6, 26), 90.0)], {}

    def fetch_index(symbol, datalen=8000):
        # 只给新高日和当前
        close = {"sh000001": 4000.0, "sz399001": 12000.0, "sh000300": 4500.0}[symbol]
        return [(date(2026, 6, 26), close)]

    out = H.refresh_history(live=True, write=True, today=TODAY, seed=seed,
                            fetch_sh=fetch_sh, fetch_sz=fetch_sz, fetch_index=fetch_index)
    assert out["live_ok"] is True and not out["from_snapshot"]
    assert out["extrema"]["margin_sh"]["hist_high"]["date"] == "2026-06-26"
    assert out["extrema"]["margin_sh"]["hist_high"]["index_close"] == pytest.approx(4000.0)
    assert out["extrema"]["margin_total"]["hist_high"]["balance"] == pytest.approx(210.0)
    assert (tmp_path / "latest.json").is_file()


def test_attach_preserves_balance_and_fills_missing_row():
    bundle = {"market": {
        "margin_sh": {"status": "ok", "asof": "2026-11-13", "balance": 11692.6,
                      "chg_1d": -10.0, "chg_5d": 1.0, "chg_20d": 2.0},
        "margin_sz": {"status": "missing", "reason": "接口无返回"},
        "margin_total": {"status": "ok", "asof": "2026-11-12", "balance": 22576.6,
                         "chg_1d": 1.0, "chg_5d": 2.0, "chg_20d": 3.0},
    }}
    payload = {
        "coverage_start": "2010-03-31",
        "from_snapshot": True,
        "snapshot_asof": "2026-11-12",
        "extrema": {
            "margin_sh": {"hist_high": {"date": "2026-06-25", "balance": 15211.1, "index_close": 4120.28},
                          "hist_low": {"date": "2014-09-22", "balance": 3776.4, "index_close": 2289.87},
                          "index_now": {"date": "2026-11-13", "close": 3634.35},
                          "index_name": "上证指数", "index_code": "000001",
                          "hist_coverage_start": "2010-03-31", "hist_low_coverage_start": "2014-09-22", "hist_n": 2},
            "margin_sz": {"hist_high": {"date": "2026-06-25", "balance": 14798.3, "index_close": 16344.08},
                          "hist_low": {"date": "2014-09-22", "balance": 2133.8, "index_close": 7895.87},
                          "index_now": {"date": "2026-11-13", "close": 10968.79},
                          "index_name": "深证成指", "index_code": "399001",
                          "hist_coverage_start": "2010-03-31", "hist_low_coverage_start": "2014-09-22", "hist_n": 2},
            "margin_total": {"hist_high": {"date": "2026-06-25", "balance": 30009.4, "index_close": 5020.1},
                             "hist_low": {"date": "2014-09-22", "balance": 5910.2, "index_close": 2378.92},
                             "index_now": {"date": "2026-11-13", "close": 4016.29},
                             "index_name": "沪深300", "index_code": "000300",
                             "hist_coverage_start": "2010-03-31", "hist_low_coverage_start": "2014-09-22", "hist_n": 2},
        },
    }
    out = H.attach_margin_history(bundle, payload)
    sh = out["market"]["margin_sh"]
    assert sh["balance"] == pytest.approx(11692.6) and sh["chg_1d"] == pytest.approx(-10.0)
    assert sh["hist_high"]["balance"] == pytest.approx(15211.1)
    assert sh["hist_from_snapshot"] is True
    # 深市当日缺失时历史高低仍挂上，不补当日余额
    sz = out["market"]["margin_sz"]
    assert sz["status"] == "missing" and sz.get("balance") is None
    assert sz["hist_low"]["balance"] == pytest.approx(2133.8)
    assert sz["hist_low_coverage_start"] == "2014-09-22"
    assert out["margin_history_coverage_start"] == "2010-03-31"
    assert out["margin_history_low_coverage_start"] == "2014-09-22"
    assert out["margin_history_from_snapshot"] is True
    assert bundle["market"]["margin_sh"].get("hist_high") is None  # 不改输入


def test_committed_seed_covers_2010_and_matches_computed_extrema():
    seed = H.load_history_seed(paths=(H.SEED_PATH,))
    assert seed is not None
    sh, sz = H.seed_series(seed)
    assert sh[0][0] == H.MARGIN_HISTORY_START == date(2010, 3, 31)
    assert sz[0][0] == date(2010, 3, 31)
    assert sh[0][1] == pytest.approx(0.058248, abs=1e-6)
    assert sz[0][1] == pytest.approx(0.006708, abs=1e-6)
    ext = H.build_extrema(sh, sz, {})
    # 与写入 seed 时计算的高低点日期/余额一致（指数由 seed 回填）
    stored = seed["extrema"]
    for key in H.MARGIN_KEYS:
        assert ext[key]["hist_high"]["date"] == stored[key]["hist_high"]["date"]
        assert ext[key]["hist_high"]["balance"] == pytest.approx(stored[key]["hist_high"]["balance"], rel=1e-6)
        assert ext[key]["hist_low"]["date"] == "2014-09-22"
        assert ext[key]["hist_low_coverage_start"] == "2014-09-22"
        assert ext[key]["hist_low"]["balance"] == pytest.approx(stored[key]["hist_low"]["balance"], rel=1e-6)
    assert stored["margin_sh"]["hist_high"]["date"] == "2026-06-25"
    assert stored["margin_sh"]["hist_high"]["index_close"] == pytest.approx(4120.28)
    assert stored["margin_sz"]["hist_high"]["index_close"] == pytest.approx(16344.08)
    assert stored["margin_total"]["hist_high"]["index_close"] == pytest.approx(5020.1)
    assert stored["margin_sh"]["hist_low"]["index_close"] == pytest.approx(2289.87)
    assert stored["margin_sz"]["hist_low"]["index_close"] == pytest.approx(7895.87)
    assert stored["margin_total"]["hist_low"]["index_close"] == pytest.approx(2378.92)
    assert stored["margin_sh"]["index_name"] == "上证指数" or stored["margin_sh"]["hist_high"]["index_name"] == "上证指数"


def test_ensure_offline_does_not_call_network(monkeypatch):
    called = []

    def boom(*a, **k):
        called.append(1)
        raise AssertionError("should not network")

    monkeypatch.setattr(H, "fetch_margin_sh_history", boom)
    monkeypatch.setattr(H, "fetch_margin_sz_history", boom)
    monkeypatch.setattr(H, "fetch_index_closes", boom)
    b = {"market": {"margin_sh": {"status": "ok", "balance": 1, "asof": "2026-11-13"},
                    "margin_sz": {"status": "ok", "balance": 1, "asof": "2026-11-12"},
                    "margin_total": {"status": "ok", "balance": 2, "asof": "2026-11-12"}}}
    out = H.ensure_margin_history(b, live=False)
    assert called == []
    assert out["market"]["margin_sh"]["hist_high"]["date"] == "2026-06-25"
    assert out["market"]["margin_sh"].get("hist_from_snapshot") is True


def test_series_for_chart_no_interpolation():
    bal = [(date(2026, 6, 24), 100.0), (date(2026, 6, 25), 200.0), (date(2026, 6, 26), 150.0)]
    idx = [(date(2026, 6, 24), 4000.0), (date(2026, 6, 26), 4100.0)]  # 缺 25 日
    rows = H.series_for_chart(bal, idx)
    assert rows[0] == ["2026-06-24", 100.0, 4000.0]
    assert rows[1][0] == "2026-06-25" and rows[1][1] == 200.0 and rows[1][2] is None
    assert rows[2] == ["2026-06-26", 150.0, 4100.0]


def test_downsample_weekly_keeps_actual_last_point_and_reinserts_extrema():
    # 2014-09-22 Mon ... 09-26 Fri; 2026-06-22 Mon ... 06-26 Fri
    daily = [
        ["2014-09-22", 100.0, 2289.87],
        ["2014-09-23", 110.0, 2300.0],
        ["2014-09-26", 120.0, 2400.0],
        ["2026-06-24", 190.0, 4100.0],
        ["2026-06-25", 200.0, 4120.28],
        ["2026-06-26", 198.0, 4100.0],
    ]
    weekly = H.downsample_weekly(daily)
    assert [p[0] for p in weekly] == ["2014-09-26", "2026-06-26"]
    merged = H.insert_extrema_points(weekly, daily, "2026-06-25", "2014-09-22")
    assert [p[0] for p in merged] == ["2014-09-22", "2014-09-26", "2026-06-25", "2026-06-26"]
    # 窗口外的极值不加
    late = [p for p in daily if p[0] >= "2026-06-01"]
    late_w = H.downsample_weekly(late)
    only = H.insert_extrema_points(late_w, late, "2026-06-25", "2014-09-22")
    assert "2014-09-22" not in [p[0] for p in only]
    assert "2026-06-25" in [p[0] for p in only]


def test_refresh_merges_cached_index_incrementally(tmp_path, monkeypatch):
    seed = {
        "sh": [["2026-06-25", 100.0]],
        "sz": [["2026-06-25", 80.0]],
        "index": {"sh000001": [["2026-06-25", 4120.28]],
                  "sz399001": [["2026-06-25", 16344.08]],
                  "sh000300": [["2026-06-25", 5020.1]]},
        "extrema": {},
    }
    monkeypatch.setattr(H, "RUNTIME_PATH", tmp_path / "latest.json")
    seen = []

    def fetch_sh(start, end):
        return [(date(2026, 6, 26), 120.0)]

    def fetch_sz(start, end):
        return [(date(2026, 6, 26), 90.0)], {}

    def fetch_index(symbol, datalen=8000):
        seen.append((symbol, datalen))
        close = {
            "sh000001": 4000.0, "sz399001": 12000.0, "sh000300": 4500.0,
            "sh000016": 2500.0, "sh000852": 7000.0,
        }[symbol]
        return [(date(2026, 6, 26), close)]

    out = H.refresh_history(live=True, write=True, today=TODAY, seed=seed,
                            fetch_sh=fetch_sh, fetch_sz=fetch_sz, fetch_index=fetch_index)
    # seed 指数不从 2010-03-31 起，应拉满
    assert all(d == 8000 for _, d in seen)
    assert out["index"]["sh000001"][0] == ["2026-06-25", 4120.28]
    assert out["index"]["sh000001"][-1] == ["2026-06-26", 4000.0]
    # 再刷一次：序列已覆盖起点则只拉 260
    seed2 = {k: out[k] for k in ("sh", "sz", "index", "extrema")}
    seed2["index"] = {"sh000001": [["2010-03-31", 3109.1], ["2026-06-26", 4000.0]],
                      "sz399001": [["2010-03-31", 12494.35], ["2026-06-26", 12000.0]],
                      "sh000300": [["2010-03-31", 3345.6], ["2026-06-26", 4500.0]]}
    seen.clear()

    def fetch_none(start, end):
        return []

    def fetch_sz_none(start, end):
        return [], {}

    def fetch_index2(symbol, datalen=8000):
        seen.append((symbol, datalen))
        return []

    out2 = H.refresh_history(live=True, write=False, today=TODAY, seed=seed2,
                             fetch_sh=fetch_none, fetch_sz=fetch_sz_none, fetch_index=fetch_index2)
    by = dict(seen)
    assert by["sh000001"] == 260 and by["sz399001"] == 260 and by["sh000300"] == 260
    # 上证50 / 中证1000 还不在 seed 里，仍拉满
    assert by["sh000016"] == 8000 and by["sh000852"] == 8000
    assert out2["index"]["sh000001"][0][0] == "2010-03-31"


def test_committed_seed_includes_index_closes_on_extrema_dates():
    seed = H.load_history_seed(paths=(H.SEED_PATH,))
    idx = H.index_from_seed(seed)
    for key in H.MARGIN_KEYS:
        series = idx[key]
        assert series[0][0] == date(2010, 3, 31)
        assert series[-1][0] >= date(2026, 11, 13)
        stored = seed["extrema"][key]
        assert H.close_on(series, date(2026, 6, 25)) == pytest.approx(stored["hist_high"]["index_close"], abs=0.011)
        assert H.close_on(series, date(2014, 9, 22)) == pytest.approx(stored["hist_low"]["index_close"], abs=0.011)


def test_public_bundle_offline_and_endpoint_no_network(monkeypatch):
    called = []

    def boom(*a, **k):
        called.append(1)
        raise AssertionError("should not network")

    monkeypatch.setattr(H, "fetch_margin_sh_history", boom)
    monkeypatch.setattr(H, "fetch_margin_sz_history", boom)
    monkeypatch.setattr(H, "fetch_index_closes", boom)
    bundle = H.public_history_bundle(live=False)
    assert called == []
    assert bundle["status"] == "ok"
    sh = bundle["markets"]["margin_sh"]
    assert sh["label"] == "上交所" and sh["index_name"] == "上证指数"
    assert sh["series"][0][0] == "2010-03-31"
    # 2010-03-31 有余额和指数
    assert sh["series"][0][1] == pytest.approx(0.0582, abs=1e-4)
    assert sh["series"][0][2] == pytest.approx(3109.11, abs=0.02)
    high = next(p for p in sh["series"] if p[0] == "2026-06-25")
    assert high[1] == pytest.approx(15211.1408, rel=1e-6)
    assert high[2] == pytest.approx(4120.28, abs=0.011)
    assert sh["hist_low"]["date"] == "2014-09-22"
    tot = bundle["markets"]["margin_total"]
    # 沪深合计仅共同日期，不含北交所
    assert tot["asof"] == "2026-11-12" or tot["asof"] <= bundle["markets"]["margin_sh"]["asof"]

    import os
    os.environ["SKIP_BG_WARMUP"] = "1"
    import app as app_mod
    r = app_mod.app.test_client().get("/api/margin-history")
    assert r.status_code == 200
    data = r.get_json()
    assert data["status"] == "ok"
    assert "series" in data["markets"]["margin_sz"]
    assert called == []
    assert set(data["indices"]) == {"sse", "csi300", "sse50", "csi1000"}
    for key, name in (("sse", "上证指数"), ("csi300", "沪深300"),
                      ("sse50", "上证50"), ("csi1000", "中证1000")):
        ix = data["indices"][key]
        assert ix["name"] == name and ix["status"] in ("ok", "missing")
        if ix["status"] == "ok":
            assert ix["series"][0][0] >= "2010-03-31"
            assert ix["n"] == len(ix["series"])


def test_need_full_respects_csi1000_listing_date():
    csi = H.CHART_INDICES["csi1000"]
    assert H._need_full_index([], csi) is True
    assert H._need_full_index([(date(2014, 10, 17), 6041.0)], csi) is False
    assert H._need_full_index([(date(2014, 10, 20), 6100.0)], csi) is True
    sse50 = H.CHART_INDICES["sse50"]
    assert H._need_full_index([(date(2010, 3, 31), 2000.0)], sse50) is False
    assert H._need_full_index([(date(2010, 4, 1), 2000.0)], sse50) is True


def test_chart_indices_leave_csi1000_prelisting_empty():
    seed = {
        "index": {
            "sh000001": [["2010-03-31", 3109.11], ["2014-10-17", 2400.0]],
            "sh000300": [["2010-03-31", 3345.6], ["2014-10-17", 2400.0]],
            "sh000016": [["2010-03-31", 2000.0], ["2014-10-17", 1600.0]],
            "sh000852": [["2014-10-17", 6041.0]],
        }
    }
    payload = H.chart_indices_payload(H.chart_index_from_seed(seed))
    assert payload["csi1000"]["start"] == "2014-10-17"
    assert payload["csi1000"]["series"][0] == ["2014-10-17", 6041.0]
    assert "2010-03-31" not in [p[0] for p in payload["csi1000"]["series"]]
    assert payload["sse"]["start"] == "2010-03-31"
    assert payload["sse50"]["code"] == "000016" and payload["csi1000"]["code"] == "000852"


def test_module_has_no_eastmoney_push2():
    src = Path(__file__).resolve().parents[1] / "margin_history.py"
    text = src.read_text(encoding="utf-8")
    assert "push2his" not in text and "push2." not in text and "push2/" not in text


def test_load_seed_merges_chart_indices_from_other_candidate(tmp_path):
    """运行时缓存 asof 更新但不含上证50/中证1000 时，从提交 seed 合并进来。"""
    runtime = {
        "asof": {"sh": "2026-11-18", "sz": "2026-11-18"},
        "sh": [["2010-03-31", 0.05], ["2026-11-18", 15000.0]],
        "sz": [["2010-03-31", 0.01], ["2026-11-18", 14000.0]],
        "index": {"sh000001": [["2010-03-31", 3109.11], ["2026-11-18", 4000.0]]},
    }
    committed = {
        "asof": {"sh": "2026-11-13", "sz": "2026-11-12"},
        "sh": [["2010-03-31", 0.05], ["2026-11-13", 14900.0]],
        "sz": [["2010-03-31", 0.01], ["2026-11-12", 13900.0]],
        "index": {
            "sh000001": [["2010-03-31", 3109.11], ["2026-11-13", 3990.0]],
            "sh000016": [["2010-03-31", 2000.0], ["2026-11-13", 2700.0]],
            "sh000852": [["2014-10-17", 6058.67], ["2026-11-13", 5600.0]],
        },
    }
    rp = tmp_path / "latest.json"
    sp = tmp_path / "seed.json"
    rp.write_text(__import__("json").dumps(runtime), encoding="utf-8")
    sp.write_text(__import__("json").dumps(committed), encoding="utf-8")
    loaded = H.load_history_seed(paths=(rp, sp))
    assert loaded["asof"]["sh"] == "2026-11-18"
    assert loaded["index"]["sh000016"][0] == ["2010-03-31", 2000.0]
    assert loaded["index"]["sh000852"][0][0] == "2014-10-17"
    # 运行时更新的上证收盘保留
    assert loaded["index"]["sh000001"][-1] == ["2026-11-18", 4000.0]
