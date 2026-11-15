"""两融历史最高/最低及对应指数收盘：解析、合计口径、高低点、seed 回填。"""
from datetime import date

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
        (date(2010, 3, 31), 0.06),
        (date(2010, 4, 1), 0.06),   # 并列最低，取最早
        (date(2021, 1, 1), 10.0),
        (date(2026, 6, 25), 20.0),
        (date(2026, 6, 26), 20.0),   # 并列最高，取最早
    ]
    idx = [
        (date(2010, 3, 31), 3109.11),
        # 2010-04-01 缺失：不拿邻日
        (date(2026, 6, 25), 4120.28),
        (date(2026, 6, 26), 4100.0),
        (date(2026, 11, 13), 3634.35),
    ]
    ext = H.extrema_for(series, idx, "margin_sh")
    assert ext["hist_low"]["date"] == "2010-03-31" and ext["hist_low"]["balance"] == pytest.approx(0.06)
    assert ext["hist_low"]["index_close"] == pytest.approx(3109.11)
    assert ext["hist_high"]["date"] == "2026-06-25" and ext["hist_high"]["index_close"] == pytest.approx(4120.28)
    assert ext["index_now"]["date"] == "2026-11-13" and ext["index_now"]["close"] == pytest.approx(3634.35)
    assert ext["index_name"] == "上证指数" and ext["index_code"] == "000001"
    # 最低并列日没有指数
    series2 = [(date(2010, 4, 1), 0.06), (date(2026, 6, 25), 20.0)]
    low = H.extrema_for(series2, idx, "margin_sh")["hist_low"]
    assert low["date"] == "2010-04-01" and low["index_close"] is None
    assert H.close_on(idx, date(2010, 4, 2)) is None
    assert H.extrema_for([], idx, "margin_sh")["hist_high"] is None


def test_fill_index_from_cached_only_same_date():
    ext = H.extrema_for(
        [(date(2010, 3, 31), 1.0), (date(2026, 6, 25), 9.0)],
        [], "margin_sz",
    )
    cached = {"margin_sz": {
        "hist_low": {"date": "2010-03-31", "balance": 1.0, "index_close": 12494.35},
        "hist_high": {"date": "2020-01-01", "balance": 8.0, "index_close": 9999.0},  # 日期不同不得回填
        "index_now": {"date": "2026-11-12", "close": 11197.98, "index_name": "深证成指", "index_code": "399001"},
    }}
    filled = H._fill_index_from_cached({"margin_sz": ext}, cached)["margin_sz"]
    assert filled["hist_low"]["index_close"] == pytest.approx(12494.35)
    assert filled["hist_high"]["index_close"] is None
    assert filled["index_now"]["close"] == pytest.approx(11197.98)


def test_refresh_offline_uses_seed_and_labels_snapshot(tmp_path, monkeypatch):
    seed = {
        "coverage_start": "2010-03-31",
        "asof": {"sh": "2026-06-25", "sz": "2026-06-25"},
        "sh": [["2010-03-31", 0.0582], ["2026-06-25", 15211.1408]],
        "sz": [["2010-03-31", 0.0067], ["2026-06-25", 14798.2858]],
        "extrema": {"margin_sh": {
            "hist_high": {"date": "2026-06-25", "balance": 15211.1408, "index_close": 4120.28},
            "hist_low": {"date": "2010-03-31", "balance": 0.0582, "index_close": 3109.11},
            "index_now": {"date": "2026-06-25", "close": 4120.28, "index_name": "上证指数", "index_code": "000001"},
        }},
    }
    monkeypatch.setattr(H, "load_history_seed", lambda paths=None: seed)
    out = H.refresh_history(live=False, write=False, today=TODAY, seed=seed)
    assert out["from_snapshot"] is True and out["live_ok"] is False
    sh = out["extrema"]["margin_sh"]
    assert sh["hist_high"]["index_close"] == pytest.approx(4120.28)
    assert sh["hist_low"]["index_close"] == pytest.approx(3109.11)
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
                          "hist_low": {"date": "2010-03-31", "balance": 0.0582, "index_close": 3109.11},
                          "index_now": {"date": "2026-11-13", "close": 3634.35},
                          "index_name": "上证指数", "index_code": "000001",
                          "hist_coverage_start": "2010-03-31", "hist_n": 2},
            "margin_sz": {"hist_high": {"date": "2026-06-25", "balance": 14798.3, "index_close": 16344.08},
                          "hist_low": {"date": "2010-03-31", "balance": 0.0067, "index_close": 12494.35},
                          "index_now": {"date": "2026-11-13", "close": 10968.79},
                          "index_name": "深证成指", "index_code": "399001",
                          "hist_coverage_start": "2010-03-31", "hist_n": 2},
            "margin_total": {"hist_high": {"date": "2026-06-25", "balance": 30009.4, "index_close": 5020.1},
                             "hist_low": {"date": "2010-03-31", "balance": 0.065, "index_close": 3345.61},
                             "index_now": {"date": "2026-11-13", "close": 4016.29},
                             "index_name": "沪深300", "index_code": "000300",
                             "hist_coverage_start": "2010-03-31", "hist_n": 2},
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
    assert sz["hist_low"]["balance"] == pytest.approx(0.0067)
    assert out["margin_history_coverage_start"] == "2010-03-31"
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
        assert ext[key]["hist_low"]["date"] == "2010-03-31"
    assert stored["margin_sh"]["hist_high"]["date"] == "2026-06-25"
    assert stored["margin_sh"]["hist_high"]["index_close"] == pytest.approx(4120.28)
    assert stored["margin_sz"]["hist_high"]["index_close"] == pytest.approx(16344.08)
    assert stored["margin_total"]["hist_high"]["index_close"] == pytest.approx(5020.1)
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
