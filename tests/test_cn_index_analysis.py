# -*- coding: utf-8 -*-
"""A-share index cards: Sina/Tencent incremental refresh and snapshot labels."""
from __future__ import annotations

import json
import os

os.environ.setdefault("SKIP_BG_WARMUP", "1")
os.environ.setdefault("SKIP_INDEX_FLOW", "1")
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

import cn_index_analysis as A

ROOT = Path(__file__).resolve().parents[1]


def _pairs(start: str, n: int, base: float, step: float = 1.0) -> list[tuple[date, float]]:
    d0 = date.fromisoformat(start)
    return [(d0 + timedelta(days=i), float(base + i * step)) for i in range(n)]


def _payload(asof: str, price: float = 6000.0, **extra) -> dict:
    rows = []
    for yahoo, meta in A.CN_INDICES.items():
        row = {
            "yahoo": yahoo,
            "id": meta["id"],
            "name": meta["name"],
            "short": meta["short"],
            "price": price,
            "chg_1d": -1.0,
            "asof": asof,
            "from_snapshot": False,
            "bias_cn": "中性",
        }
        rows.append(row)
    data = {
        "ok": True,
        "updated_at": f"{asof}T16:00:00+08:00",
        "asof": asof,
        "from_snapshot": False,
        "source": "sina",
        "method": dict(A.DEFAULT_METHOD),
        "note": "",
        "indices": rows,
        "satellites": [],
        "disclaimer": A.DISCLAIMER,
    }
    data.update(extra)
    return data


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    A._cache = None
    monkeypatch.setattr(A, "RUNTIME_PATH", tmp_path / "cn_index_analysis_latest.json")
    monkeypatch.setenv("SKIP_AKSHARE", "1")
    monkeypatch.setattr(A, "_series_from_ak", lambda *_a, **_k: None)
    yield
    A._cache = None


def test_naive_day_index_strips_tz():
    naive = pd.Series([1.0, 2.0], index=pd.to_datetime(["2026-01-01", "2026-01-02"]))
    aware = pd.Series([3.0, 4.0], index=pd.to_datetime(["2026-01-01", "2026-01-02"], utc=True))
    ny = pd.Series(
        [5.0, 6.0],
        index=pd.to_datetime(["2026-01-01", "2026-01-02"]).tz_localize("America/New_York"),
    )
    a = A._naive_day_index(naive)
    b = A._naive_day_index(aware)
    c = A._naive_day_index(ny)
    assert a.index.tz is None and b.index.tz is None and c.index.tz is None
    assert list(a.index) == list(b.index) == list(c.index)


def test_mixed_tz_dataframe_would_have_raised_without_helper():
    naive = pd.Series([1.0, 2.0], index=pd.to_datetime(["2026-01-01", "2026-01-02"]))
    aware = pd.Series([3.0, 4.0], index=pd.to_datetime(["2026-01-01", "2026-01-02"], utc=True))
    with pytest.raises(TypeError, match="tz-naive with tz-aware"):
        pd.DataFrame({"a": naive, "b": aware}).sort_index()
    fixed = pd.DataFrame({"a": A._naive_day_index(naive), "b": A._naive_day_index(aware)}).sort_index()
    assert len(fixed) == 2 and fixed.index.tz is None


def test_parse_sina_and_tencent_kline():
    sina = [
        {"day": "2026-11-12", "close": "6537.839"},
        {"day": "2026-11-13", "close": "6357.258"},
        {"day": "2026-11-18", "close": "6052.825"},
    ]
    pairs = A.parse_sina_kline(sina)
    assert pairs[0] == (date(2026, 11, 12), 6537.839)
    assert pairs[-1][0] == date(2026, 11, 18)
    js = {
        "data": {
            "sh000852": {
                "day": [
                    ["2026-11-12", "6500", "6537.839", "6600", "6400", "1"],
                    ["2026-11-13", "6400", "6357.258", "6500", "6300", "1"],
                ]
            }
        }
    }
    t = A.parse_tencent_kline(js, "sh000852")
    assert t[1] == (date(2026, 11, 13), 6357.258)


def test_drop_incomplete_bars_before_close_cutoff():
    pairs = [
        (date(2026, 11, 18), 6052.825),
        (date(2026, 11, 19), 6013.09),
    ]
    morning = datetime(2026, 11, 19, 10, 0, tzinfo=A.CST)
    after = datetime(2026, 11, 19, 15, 30, tzinfo=A.CST)
    assert A.drop_incomplete(pairs, morning) == [(date(2026, 11, 18), 6052.825)]
    assert A.drop_incomplete(pairs, after)[-1][0] == date(2026, 11, 19)
    assert A.last_completed_session(morning) == date(2026, 11, 18)
    assert A.last_completed_session(after) == date(2026, 11, 19)


def test_screenshot_numbers_are_2026_11_13_closes():
    """线上卡片 6357.26 / -2.76% 对应 11-13 收盘，不是 11-17 或 11-18。"""
    c12, c13 = 6537.839, 6357.258
    chg = round((c13 / c12 - 1) * 100, 2)
    assert round(c13, 2) == 6357.26
    assert chg == -2.76


def test_download_close_uses_sina_and_joins_tz_aware_satellites(monkeypatch, tmp_path):
    monkeypatch.setattr(A, "PRICE_SEED_PATH", tmp_path / "cn_index_daily.json")
    monkeypatch.setenv("SKIP_AKSHARE", "0")
    monkeypatch.delenv("RENDER", raising=False)
    sina_pairs = _pairs("2026-01-01", 60, 4000.0)

    def fetch_sina(symbol, **_k):
        return list(sina_pairs)

    def fetch_tencent(symbol, **_k):
        raise AssertionError("tencent must not run when sina succeeds")

    def fetch_yahoo(ticker):
        idx = pd.date_range("2026-01-01", periods=60, freq="D").tz_localize("America/New_York")
        return pd.Series(30.0 + pd.RangeIndex(60).astype(float), index=idx)

    frames = A._download_close(
        list(A.CN_INDICES) + list(A.SATELLITES),
        live=True,
        now=datetime(2026, 11, 19, 10, 0, tzinfo=A.CST),
        fetch_sina=fetch_sina,
        fetch_tencent=fetch_tencent,
        fetch_yahoo=fetch_yahoo,
    )
    assert "000300.SS" in frames and "000852.SS" in frames
    assert "FXI" in frames and "KWEB" in frames
    assert frames["000300.SS"].index.tz is None
    assert frames["FXI"].index.tz is None
    row = A._row_from_series("000300.SS", frames["000300.SS"], A.CN_INDICES["000300.SS"])
    assert row is not None and row["id"] == "csi300" and row["asof"] == "2026-03-01"


def test_tencent_fallback_and_incremental_merge(tmp_path, monkeypatch):
    seed_path = tmp_path / "cn_index_daily.json"
    monkeypatch.setattr(A, "PRICE_SEED_PATH", seed_path)
    old = _pairs("2026-01-01", 40, 4000.0)
    A.save_price_seed({"000300.SS": old, "000016.SS": old, "000852.SS": old})

    def fetch_sina(symbol, **_k):
        return []

    new = [(date(2026, 2, 10) + timedelta(days=i), 4100.0 + i) for i in range(20)]

    def fetch_tencent(symbol, **_k):
        return list(new)

    frames = A._download_close(
        list(A.CN_INDICES),
        live=True,
        now=datetime(2026, 11, 19, 10, 0, tzinfo=A.CST),
        fetch_sina=fetch_sina,
        fetch_tencent=fetch_tencent,
        fetch_yahoo=lambda *_a, **_k: None,
    )
    s = frames["000300.SS"]
    assert s.index.min().date() == date(2026, 1, 1)
    assert s.index.max().date() == date(2026, 3, 1)


def test_refresh_failure_labels_last_snapshot(tmp_path, monkeypatch):
    prev = _payload("2026-11-13", 6357.26)
    monkeypatch.setattr(A, "PRICE_SEED_PATH", tmp_path / "cn_index_daily.json")
    monkeypatch.setattr(A, "API_SEED_PATH", tmp_path / "missing.json")

    def boom(*_a, **_k):
        raise RuntimeError("source down")

    out = A.refresh(
        live=True,
        now=datetime(2026, 11, 19, 10, 0, tzinfo=A.CST),
        fetch_sina=boom,
        fetch_tencent=boom,
        fetch_yahoo=lambda *_a, **_k: None,
        write=True,
        prev=prev,
        attach_bundles=False,
    )
    assert out["from_snapshot"] is True
    assert out["snapshot_asof"] == "2026-11-13"
    assert out["note"] == "上次快照（2026-11-13）"
    assert out["indices"][2]["price"] == 6357.26
    # 不得把超时备注无限追加
    assert out["note"].count("上次快照") == 1
    again = A.refresh(
        live=True,
        now=datetime(2026, 11, 19, 10, 5, tzinfo=A.CST),
        fetch_sina=boom,
        fetch_tencent=boom,
        fetch_yahoo=lambda *_a, **_k: None,
        write=True,
        prev=out,
        attach_bundles=False,
    )
    assert again["note"] == "上次快照（2026-11-13）"


def test_refresh_success_updates_asof_and_clears_snapshot_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "PRICE_SEED_PATH", tmp_path / "cn_index_daily.json")
    prev = _payload("2026-11-13", 6357.26, from_snapshot=True, note="上次快照（2026-11-13）")
    series = {
        "sh000300": _pairs("2026-08-01", 80, 4000.0),
        "sh000016": _pairs("2026-08-01", 80, 2700.0),
        "sh000852": _pairs("2026-08-01", 80, 6000.0),
    }
    # last date: 2026-08-01 + 79 = 2026-10-19
    out = A.refresh(
        live=True,
        now=datetime(2026, 11, 19, 10, 0, tzinfo=A.CST),
        fetch_sina=lambda symbol, **_k: list(series[symbol]),
        fetch_tencent=lambda *_a, **_k: [],
        fetch_yahoo=lambda *_a, **_k: None,
        write=True,
        prev=prev,
        attach_bundles=False,
    )
    assert out["from_snapshot"] is False
    assert out["asof"] == "2026-10-19"
    assert out["source"] == "sina"
    assert out["note"] == ""
    assert out["indices"][0]["asof"] == "2026-10-19"
    csi = next(r for r in out["indices"] if r["id"] == "csi1000")
    assert csi["price"] != 6357.26


def test_snapshot_does_not_network(monkeypatch, tmp_path):
    seed = _payload("2026-11-18", 6052.83)
    path = tmp_path / "cn_indices.json"
    path.write_text(json.dumps(seed), encoding="utf-8")
    monkeypatch.setattr(A, "API_SEED_PATH", path)
    monkeypatch.setattr(A, "PRICE_SEED_PATH", tmp_path / "daily.json")

    def no_net(*_a, **_k):
        raise AssertionError("request path must not network")

    monkeypatch.setattr(A, "_get_json", no_net)
    monkeypatch.setattr(A, "fetch_sina_kline", no_net)
    monkeypatch.setattr(A, "fetch_tencent_kline", no_net)
    data = A.snapshot()
    assert len(data["indices"]) == 3
    assert data["asof"] == "2026-11-18"


def test_needs_refresh_respects_close_cutoff():
    payload = _payload("2026-11-18")
    morning = datetime(2026, 11, 19, 10, 0, tzinfo=A.CST)
    after = datetime(2026, 11, 19, 15, 30, tzinfo=A.CST)
    assert A.needs_refresh(payload, morning) is False
    assert A.needs_refresh(payload, after) is True
    payload["from_snapshot"] = True
    assert A.needs_refresh(payload, morning) is True
    # 假日午后已成功刷新但仍只有上一交易日：不再反复打源
    holiday = dict(payload)
    holiday["from_snapshot"] = False
    holiday["updated_at"] = "2026-11-19T15:20:00+08:00"
    assert A.needs_refresh(holiday, after) is False


def test_render_uses_sina_even_when_render_env_set(tmp_path, monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setenv("SKIP_INDEX_FLOW", "1")
    monkeypatch.setenv("SKIP_BG_WARMUP", "1")
    monkeypatch.setattr(A, "PRICE_SEED_PATH", tmp_path / "daily.json")
    called = {"sina": 0}

    def fetch_sina(symbol, **_k):
        called["sina"] += 1
        return _pairs("2026-08-01", 80, 4000.0)

    A.refresh(
        live=True,
        now=datetime(2026, 11, 19, 10, 0, tzinfo=A.CST),
        fetch_sina=fetch_sina,
        fetch_tencent=lambda *_a, **_k: [],
        fetch_yahoo=lambda *_a, **_k: None,
        write=False,
        prev=_payload("2026-11-13"),
        attach_bundles=False,
    )
    assert called["sina"] == 3


def test_committed_cn_indices_seed_has_real_indices():
    path = ROOT / "docs" / "api" / "cn_indices.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    err = str(data.get("error") or "")
    assert "tz-naive" not in err and "tz-aware" not in err
    idxs = data.get("indices") or []
    assert isinstance(idxs, list) and len(idxs) >= 3, f"seed indices empty or short: {len(idxs)} error={err!r}"
    ids = {row.get("id") for row in idxs if isinstance(row, dict)}
    assert {"csi300", "sse50", "csi1000"} <= ids
    assert data.get("asof")
    assert data.get("from_snapshot") is not True
    assert "指数历史源超时" not in str(data.get("note") or "")
    for row in idxs:
        assert row.get("asof")
    assert "新浪" in (data.get("method") or {}).get("source", "")


def test_ui_shows_last_snapshot_label():
    js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    docs = (ROOT / "docs" / "static" / "app.js").read_text(encoding="utf-8")
    for body in (js, docs):
        assert "function cnidxSnapLabel" in body
        assert "上次快照（${asof}）" in body or "上次快照（" in body
        assert "cnidxChgCell" in body


def test_no_eastmoney_push2_in_cn_index_module():
    src = (ROOT / "cn_index_analysis.py").read_text(encoding="utf-8")
    assert "push2.eastmoney" not in src
    assert "push2his" not in src
    assert "quotes.eastmoney.com" not in src
    assert "CN_MarketData.getKLineData" in src
    assert "fqkline" in src


def test_cn_indices_api_request_path_does_not_network(monkeypatch, tmp_path):
    os.environ["SKIP_BG_WARMUP"] = "1"
    seed = _payload("2026-11-18", 6052.83)
    seed_path = tmp_path / "cn_indices.json"
    seed_path.write_text(json.dumps(seed), encoding="utf-8")
    monkeypatch.setattr(A, "API_SEED_PATH", seed_path)
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setenv("SKIP_INDEX_FLOW", "1")
    monkeypatch.setenv("SKIP_BG_WARMUP", "1")
    net = {"n": 0}

    def no_net(*_a, **_k):
        net["n"] += 1
        raise AssertionError("no network on request path")

    monkeypatch.setattr(A, "_get_json", no_net)
    monkeypatch.setattr(A, "refresh", no_net)

    import app as app_mod
    import bg_tasks

    bg_tasks._reset_for_tests(max_tasks=2)
    # asof 已是上一完整交易日：不应排队刷新
    A._remember(seed)
    client = app_mod.app.test_client()
    r = client.get("/api/cn-indices")
    assert r.status_code == 200
    body = r.get_json()
    assert body["asof"] == "2026-11-18"
    assert net["n"] == 0
    assert body.get("source") != "forced-live"
