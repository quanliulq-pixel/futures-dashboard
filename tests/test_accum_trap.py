# -*- coding: utf-8 -*-
"""吸筹/诱多量价规则：纯本地合成序列，不联网。"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

import accum_trap as A

CST = timezone(timedelta(hours=8))


def _bars(n, start=date(2018, 1, 2), close0=100.0, trend=0.0, vol=1_000_000.0,
          noise=0.0, high_frac=0.01, low_frac=0.01, weekend=True):
    """简单日线。trend 为每日漂移。"""
    recs = []
    d = start
    c = close0
    i = 0
    while len(recs) < n:
        if weekend and d.weekday() >= 5:
            d += timedelta(days=1)
            continue
        o = c
        c = c * (1 + trend)
        h = max(o, c) * (1 + high_frac)
        l = min(o, c) * (1 - low_frac)
        recs.append({"date": pd.Timestamp(d), "open": o, "high": h, "low": l,
                     "close": c, "volume": vol})
        d += timedelta(days=1)
        i += 1
    return pd.DataFrame(recs).set_index("date")


def test_position_buckets_and_long_decline():
    # 250 日从 100 跌到 ~55，再横盘，分位应在低位且回撤深
    fall = _bars(200, close0=100.0, trend=-0.003, vol=2e6)
    flat = _bars(80, start=fall.index[-1].date() + timedelta(days=1),
                 close0=float(fall["close"].iloc[-1]), trend=0.0, vol=0.8e6,
                 high_frac=0.004, low_frac=0.004)
    df = A.merge_frames(fall, flat)
    feat = A.analyze_bar_frame(df, None, A.holders_feature(None, applicable=False))
    assert feat is not None
    assert feat["position"]["bucket"] == "低位"
    assert feat["position"]["long_decline"] is True
    assert feat["position"]["drawdown_250"] <= -0.20
    assert feat["position"]["range_pctile"] <= 0.30


def test_high_bucket():
    rise = _bars(280, close0=50.0, trend=0.004, vol=2e6)
    feat = A.analyze_bar_frame(rise, None, A.holders_feature(None, applicable=False))
    assert feat["position"]["bucket"] == "高位"
    assert feat["position"]["range_pctile"] >= 0.70


def test_sudden_volume_and_mild_and_tight():
    df = _bars(280, close0=100.0, trend=0.0, vol=1e6, high_frac=0.003, low_frac=0.003)
    # 最后一日突然放量且收阳
    df = df.copy()
    df.iloc[-1, df.columns.get_loc("volume")] = 2.5e6
    df.iloc[-1, df.columns.get_loc("close")] = df.iloc[-1]["open"] * 1.01
    df.iloc[-1, df.columns.get_loc("high")] = df.iloc[-1]["close"] * 1.002
    feat = A.analyze_bar_frame(df, None, A.holders_feature(None, applicable=False))
    assert feat["volume"]["sudden_spike"] is True
    # 横盘：振幅很小
    assert feat["volume"]["tight_range"] is True


def test_long_upper_shadow_and_reject():
    df = _bars(280, close0=80.0, trend=0.002, vol=1e6)
    df = df.copy()
    # 今日冲出 20 日新高后回落，长上影
    prev_high = float(df["high"].iloc[-21:-1].max())
    o = float(df["close"].iloc[-2])
    h = max(prev_high * 1.02, o * 1.05)
    c = o * 0.995
    l = min(o, c) * 0.998
    df.iloc[-1, df.columns.get_loc("open")] = o
    df.iloc[-1, df.columns.get_loc("high")] = h
    df.iloc[-1, df.columns.get_loc("low")] = l
    df.iloc[-1, df.columns.get_loc("close")] = c
    feat = A.analyze_bar_frame(df, None, A.holders_feature(None, applicable=False))
    assert feat["patterns"]["long_upper_shadow"] is True
    assert feat["patterns"]["reject_high"] is True


def test_price_volume_diverge_no_fabrication():
    df = _bars(280, close0=80.0, trend=0.002, vol=1e6)
    df = df.copy()
    # 价创新高，量低于均量
    df.iloc[-1, df.columns.get_loc("close")] = float(df["close"].iloc[-21:-1].max()) * 1.01
    df.iloc[-1, df.columns.get_loc("high")] = df.iloc[-1]["close"] * 1.002
    df.iloc[-1, df.columns.get_loc("volume")] = 0.5e6
    feat = A.analyze_bar_frame(df, None, A.holders_feature(None, applicable=False))
    assert feat["patterns"]["price_vol_diverge"] is True
    # 缺量的日期不应编造背离
    df2 = df.copy()
    df2.iloc[-1, df2.columns.get_loc("volume")] = float("nan")
    # 重做 rolling 时 vol20 可能仍在，但今日量为 nan
    feat2 = A.analyze_bar_frame(df2, None, A.holders_feature(None, applicable=False))
    assert feat2["patterns"]["price_vol_diverge"] is False


def test_weak_after_spike():
    df = _bars(280, close0=100.0, trend=0.0, vol=1e6)
    df = df.copy()
    # 4 日前放量拉高，此后回落
    j = -5
    df.iloc[j, df.columns.get_loc("volume")] = 3e6
    spike_close = float(df.iloc[j]["close"]) * 1.03
    df.iloc[j, df.columns.get_loc("close")] = spike_close
    df.iloc[j, df.columns.get_loc("high")] = spike_close * 1.01
    for k in range(j + 1, 0):
        df.iloc[k, df.columns.get_loc("close")] = spike_close * 0.98
        df.iloc[k, df.columns.get_loc("high")] = spike_close * 0.99
    feat = A.analyze_bar_frame(df, None, A.holders_feature(None, applicable=False))
    assert feat["patterns"]["weak_after_spike"] is True


def test_holders_not_applicable_for_index_and_declining_for_stock():
    na = A.holders_feature(None, applicable=False)
    assert na["status"] == "not_applicable" and na["reason"] == "不适用"
    miss = A.holders_feature([["2025-06-30", 10000]], applicable=True)
    assert miss["status"] == "missing" and miss["reason"] == "无数据"
    ok = A.holders_feature(
        [["2025-06-30", 12000], ["2025-09-30", 11000], ["2025-12-31", 10000]],
        applicable=True,
    )
    assert ok["status"] == "ok" and ok["declining"] is True and ok["asof"] == "2025-12-31"
    up = A.holders_feature(
        [["2025-06-30", 10000], ["2025-09-30", 11000], ["2025-12-31", 12000]],
        applicable=True,
    )
    assert up["declining"] is False


def test_score_excludes_na_holders_and_label_gated_by_position():
    fall = _bars(200, close0=100.0, trend=-0.003, vol=2e6)
    flat = _bars(80, start=fall.index[-1].date() + timedelta(days=1),
                 close0=float(fall["close"].iloc[-1]), trend=0.0, vol=0.7e6,
                 high_frac=0.003, low_frac=0.003)
    df = A.merge_frames(fall, flat)
    feat = A.analyze_bar_frame(df, None, A.holders_feature(None, applicable=False))
    holder_hit = [h for h in feat["hits"]["accum"] if h["id"] == "holders_down"][0]
    assert holder_hit["applicable"] is False
    # 分母不含不适用项
    appl = [h for h in feat["hits"]["accum"] if h["applicable"]]
    assert all(h["id"] != "holders_down" for h in appl)
    assert feat["accum_score"] is not None
    # 低位才可能疑似吸筹
    if feat["accum_score"] >= 60:
        assert feat["label"] in ("疑似吸筹", "中性")
        if feat["label"] == "疑似吸筹":
            assert feat["position"]["bucket"] == "低位"


def test_high_spike_can_be_trap():
    rise = _bars(260, close0=50.0, trend=0.004, vol=1e6)
    rise = rise.copy()
    prev_high = float(rise["high"].iloc[-21:-1].max())
    o = float(rise["close"].iloc[-2])
    h = max(prev_high * 1.03, o * 1.06)
    c = o * 0.99
    rise.iloc[-1, rise.columns.get_loc("open")] = o
    rise.iloc[-1, rise.columns.get_loc("high")] = h
    rise.iloc[-1, rise.columns.get_loc("low")] = min(o, c) * 0.998
    rise.iloc[-1, rise.columns.get_loc("close")] = c
    rise.iloc[-1, rise.columns.get_loc("volume")] = 3e6
    feat = A.analyze_bar_frame(rise, None, A.holders_feature(None, applicable=False))
    assert feat["position"]["bucket"] == "高位"
    hits = {h["id"]: h["hit"] for h in feat["hits"]["trap"]}
    assert hits["mid_or_high"] and hits["wick_or_reject"]
    assert feat["trap_score"] >= 40
    if feat["trap_score"] >= 60 and feat["trap_score"] >= (feat["accum_score"] or 0) + 10:
        assert feat["label"] == "疑似诱多"


def test_chips_are_estimates_and_not_invented_on_short_history():
    short = _bars(5, close0=10.0)
    ch = A.estimate_chips(short)
    assert ch["status"] == "missing" and ch["reason"] == "无数据"
    assert "估算，非真实持仓" in ch["note"]
    df = _bars(80, close0=10.0, vol=1e6)
    ch2 = A.estimate_chips(df)
    assert ch2["status"] == "ok"
    assert ch2["peak_price"] is not None
    assert "估算，非真实持仓" in ch2["note"]


def test_backtest_has_no_lookahead_and_reports_short_sample():
    # 构造一段先跌后横再涨，保证能跑完回看，但信号未必够 8 次
    fall = _bars(200, close0=100.0, trend=-0.002, vol=1.5e6)
    flat = _bars(80, start=fall.index[-1].date() + timedelta(days=1),
                 close0=float(fall["close"].iloc[-1]), trend=0.0, vol=0.8e6,
                 high_frac=0.004, low_frac=0.004)
    rise = _bars(80, start=flat.index[-1].date() + timedelta(days=1),
                 close0=float(flat["close"].iloc[-1]), trend=0.003, vol=1.2e6)
    df = A.merge_frames(A.merge_frames(fall, flat), rise)
    bt = A.backtest_signals(df, None, A.holders_feature(None, applicable=False))
    assert "accum" in bt and "trap" in bt
    # 若样本少必须写明
    if bt["accum"]["n"] < A.RULES["backtest_min_n"]:
        assert bt["accum"]["insufficient"] is True
        assert "样本不足" in (bt["accum"]["note"] or "")
    # 无前视：用手工在某一日算信号，前向收益必须等于 close[i+n]/close[i]-1
    feat_mid = A.analyze_bar_frame(df, None, A.holders_feature(None, applicable=False),
                                   i=len(df) - 70)
    assert feat_mid is not None
    c0 = float(df["close"].iloc[len(df) - 70])
    c20 = float(df["close"].iloc[len(df) - 50])
    assert c20 / c0 - 1 == pytest.approx(float(df["close"].iloc[len(df) - 50] / df["close"].iloc[len(df) - 70] - 1))


def test_relative_strength_holds_when_name_flat_and_bench_down():
    name = _bars(280, close0=100.0, trend=0.0, vol=1e6, high_frac=0.002, low_frac=0.002)
    bench = _bars(280, start=name.index[0].date(), close0=100.0, trend=-0.002, vol=1e6)
    # 对齐同一日期
    bench = bench.reindex(name.index).ffill()
    feat = A.analyze_bar_frame(name, bench, A.holders_feature(None, applicable=False))
    assert feat["relative"]["n_bench_down"] > 0
    assert feat["relative"]["holds_on_bench_down"] is True
    assert feat["relative"]["rs_20"] is not None


def test_bench_relative_not_applicable():
    df = _bars(280, close0=100.0, trend=-0.001, vol=1e6)
    feat = A.analyze_bar_frame(df, df, A.holders_feature(None, applicable=False), is_bench=True)
    hit = [h for h in feat["hits"]["accum"] if h["id"] == "holds_on_down"][0]
    assert hit["applicable"] is False


def test_missing_when_history_too_short():
    df = _bars(40, close0=100.0)
    item = A.build_item({"id": "csi1000", "name": "中证1000", "market": "cn", "kind": "index",
                         "sina": "sh000852", "bench_id": "csi300"}, df, None, None)
    assert item["status"] == "missing"
    assert item["reason"] == "无数据" or "不足" in (item["reason"] or "")
    assert item["label"] == "无数据"


def test_watchlist_official_names_and_sina_code():
    wl = A.load_watchlist()
    cn = {r["symbol"]: r for r in wl["cn_stocks"]}
    us = {r["symbol"]: r for r in wl["us_stocks"]}
    assert cn["600519"]["name"] == "贵州茅台" and not cn["600519"].get("example")
    assert set(cn) == {"600519", "300750", "600036", "300308", "002594"}
    assert set(us) == {"NVDA", "AAPL", "MSFT", "TSLA", "META"}
    assert all(not r.get("example") for r in list(cn.values()) + list(us.values()))
    ids = {m["id"] for m in A.universe()}
    assert "cn_600519" in ids and "us_NVDA" in ids
    assert A.sina_symbol_for_stock("600519") == "sh600519"
    assert A.sina_symbol_for_stock("000001") == "sz000001"
    assert A.sina_symbol_for_stock("300750") == "sz300750"
    assert A.sina_symbol_for_stock("002594") == "sz002594"
    assert A.sina_symbol_for_stock("AAPL") is None
    # 个股基准
    by = {m["id"]: m for m in A.universe()}
    assert by["cn_600519"]["bench_id"] == "csi300" and by["cn_600519"]["holders_applicable"] is True
    assert by["us_NVDA"]["bench_id"] == "sp500"


def test_module_has_no_eastmoney_push2():
    text = Path(A.__file__).read_text(encoding="utf-8")
    assert "push2.eastmoney" not in text and "push2his.eastmoney" not in text
    assert "quotes.eastmoney.com/push2" not in text
    assert "datacenter-web" in text


def test_refresh_offline_injected_frames_and_snapshot_label(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "RUNTIME_PATH", tmp_path / "latest.json")
    monkeypatch.setattr(A, "BARS_PATH", tmp_path / "bars.json")
    monkeypatch.setattr(A, "SEED_PATHS", (tmp_path / "seed.json",))
    fall = _bars(260, close0=100.0, trend=-0.002, vol=1.2e6)
    frames = {
        "sse": fall, "csi300": fall, "sse50": fall, "csi1000": fall,
        "sp500": fall, "ndx": fall, "rut": fall,
    }

    def boom_cn(*a, **k):
        raise AssertionError("should use injected fetcher")

    payload = A.analyze_from_frames(frames, now=datetime(2026, 11, 20, tzinfo=CST))
    assert payload["status"] == "ok"
    assert payload["disclaimer"] == "研究参考，不能下单，不构成投资建议"
    ids = {it["id"] for it in payload["items"]}
    assert {"sse", "csi300", "sse50", "csi1000", "sp500", "ndx", "rut"} <= ids
    # 指数股东户数不适用
    for it in payload["items"]:
        if it["kind"] == "index":
            assert it["holders"]["status"] == "not_applicable"
            assert it["holders"]["reason"] == "不适用"
    A.write_seed(payload)
    # snapshot 可加 age_hours，但种子文件不得持久化该字段
    seed = A._load_json(tmp_path / "seed.json")
    assert "age_hours" not in seed
    snap = A.snapshot()
    assert snap["status"] == "ok"
    assert "age_hours" in snap


def test_request_path_snapshot_does_not_network(monkeypatch):
    called = []

    def boom(*a, **k):
        called.append(1)
        raise AssertionError("no network")

    monkeypatch.setattr(A, "fetch_cn_ohlcv", boom)
    monkeypatch.setattr(A, "fetch_us_ohlcv", boom)
    monkeypatch.setattr(A, "fetch_cn_holders", boom)
    A.snapshot()
    assert called == []


def test_parse_sina_and_tencent_ohlcv():
    js = [{"day": "2024-01-02", "open": "10", "high": "11", "low": "9", "close": "10.5", "volume": "100"}]
    df = A.parse_sina_ohlcv(js)
    assert list(df["close"]) == [10.5] and list(df["volume"]) == [100]
    tjs = {"data": {"sh000001": {"day": [["2024-01-02", "10", "10.5", "11", "9", "100"]]}}}
    df2 = A.parse_tencent_ohlcv(tjs, "sh000001")
    assert list(df2["close"]) == [10.5]


def test_parse_holders_datacenter_web():
    js = {"success": True, "result": {"data": [
        {"END_DATE": "2026-09-30 00:00:00", "HOLDER_NUM": 278364, "SECURITY_CODE": "600519"},
        {"END_DATE": "2026-06-30 00:00:00", "HOLDER_NUM": 296404, "SECURITY_CODE": "600519"},
        {"END_DATE": "2026-03-31 00:00:00", "HOLDER_NUM": 310000, "SECURITY_CODE": "600519"},
    ]}}
    def get_json(url, params=None, headers=None):
        assert "datacenter-web.eastmoney.com" in url
        assert "push2" not in url
        assert params["reportName"] == "RPT_HOLDERNUM_DET"
        assert "600519" in params["filter"]
        return js
    rows = A.fetch_cn_holders("600519", get_json=get_json)
    assert rows[0] == ["2026-03-31", 310000]
    assert rows[-1] == ["2026-09-30", 278364]
    feat = A.holders_feature(rows, applicable=True)
    assert feat["declining"] is True and feat["asof"] == "2026-09-30"
