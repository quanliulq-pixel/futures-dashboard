# -*- coding: utf-8 -*-
"""美股风险：口径、无前瞻、快照回退、端点秒回与前端接线。默认全部离线。"""
from __future__ import annotations

import io
import json
import os
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest

os.environ.setdefault("SKIP_BG_WARMUP", "1")
import us_risk  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ASSET_V = "20261122usrisk1"
NOW = datetime(2026, 11, 22, 16, 0, tzinfo=us_risk.CST)


def _cboe_csv(rows: list[tuple[str, float]]) -> str:
    lines = ["DATE,OPEN,HIGH,LOW,CLOSE"]
    for d, c in rows:
        lines.append(f"{d},{c},{c},{c},{c}")
    return "\n".join(lines) + "\n"


def _finra_xlsx(pairs: list[tuple[str, float]]) -> bytes:
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Customer Margin Balances"
    ws.append(["Year-Month", "Debit Balances in Customers' Securities Margin Accounts", "Free Credit", "Free Credit Margin"])
    for ym, v in pairs:
        ws.append([ym, v, 0, 0])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _bea_xlsx(quarters: dict[str, float], published: str = "October 29, 2026") -> bytes:
    import openpyxl

    wb = openpyxl.Workbook()
    # dummy first sheet
    wb.active.title = "Contents"
    ws = wb.create_sheet("T10105-Q")
    ws.append(["Table 1.1.5. Gross Domestic Product"])
    ws.append(["[Millions of dollars] Seasonally adjusted at annual rates"])
    ws.append([f"Data published {published}"])
    header = ["Line", None, None] + list(quarters.keys())
    gdp = ["1", "    Gross domestic product", "A191RC"] + list(quarters.values())
    ws.append([])
    ws.append(header)
    ws.append(gdp)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_parse_cboe_and_term_structure():
    text = _cboe_csv([("11/19/2026", 28.44), ("11/20/2026", 35.89)])
    s = us_risk.parse_cboe_csv(text)
    assert str(s.index[-1].date()) == "2026-11-20" and float(s.iloc[-1]) == 35.89
    assert us_risk.term_structure(35.89, 40.72, 30.94) == "倒挂"
    assert us_risk.term_structure(15.0, 13.0, 18.0) == "升水"
    assert us_risk.term_structure(20.0, None, 22.0) == "升水"
    assert us_risk.term_structure(None, 10.0, 12.0) is None


def test_parse_finra_xlsx_and_html_peaks():
    raw = _finra_xlsx([
        ("2000-03", 299933), ("2000-02", 284206),
        ("2007-07", 416403), ("2021-10", 935862),
        ("2025-10", 1183654), ("2026-09", 1480054), ("2026-10", 1448360),
    ])
    s = us_risk.parse_finra_xlsx(raw)
    assert s.loc["2026-10"] == 1448360
    html = """
    <table><tr><th>Month/Year</th><th>Debit Balances in Customers' Securities Margin Accounts</th></tr>
    <tr><td>Oct-26</td><td>1,448,360</td></tr>
    <tr><td>Sep-26</td><td>1,480,054</td></tr>
    <tr><td>Oct-25</td><td>1,183,654</td></tr>
    </table>
    """
    h = us_risk.parse_finra_html(html)
    assert h.loc["2026-10"] == 1448360 and h.loc["2025-10"] == 1183654


def test_bea_gdp_no_lookahead_and_buffett():
    raw = _bea_xlsx({"2026Q2": 32563030, "2026Q3": 33010036})
    gdp, meta = us_risk.parse_bea_gdp(raw)
    assert gdp.loc["2026Q3"] == 33010036
    assert meta["latest_period"] == "2026Q3"
    # before quarter end, still use previous quarter
    assert us_risk.gdp_for_date(gdp, date(2026, 9, 30))[1] == "2026Q3"
    assert us_risk.gdp_for_date(gdp, date(2026, 9, 29))[1] == "2026Q2"
    cap = us_risk.market_cap_usd(68957.11)
    ratio = cap / (33010036 * 1_000_000)
    assert ratio == pytest.approx(68957.11 / 33010.036, rel=1e-9)


def test_percentile_and_judgment_bands():
    s = pd.Series(range(1, 101), dtype=float)
    assert us_risk.percentile_inclusive(s, 80) == 80.0
    assert us_risk.band_from_percentile(80) == "偏热"
    assert us_risk.band_from_percentile(20) == "偏冷"
    assert us_risk.band_from_percentile(50) == "中性"
    assert us_risk.band_from_percentile(None) is None
    assert us_risk.judge_sentiment(35.89, 99.0, "倒挂") == "偏冷"
    assert us_risk.judge_sentiment(12.0, 10.0, "升水") == "偏热"
    assert us_risk.judge_sentiment(18.0, 40.0, "升水") == "中性"
    assert us_risk.judge_sentiment(None, None, None) is None


def test_build_payload_and_no_fabricated_fields():
    idx = pd.bdate_range("2020-01-03", "2026-11-20")
    # W5000 slowly rising, last two: 71566.32 then 68957.11 on 11-19/11-20
    w = pd.Series(50000 + pd.RangeIndex(len(idx)) * 2.0, index=idx, dtype=float)
    w.iloc[-2] = 71566.32
    w.iloc[-1] = 68957.11
    spx = pd.Series(6000.0, index=idx)
    spx.iloc[-2] = 7175.83
    spx.iloc[-1] = 6910.39
    ndx = pd.Series(20000.0, index=idx)
    ndx.iloc[-2] = 26517.05
    ndx.iloc[-1] = 25028.98
    rut = pd.Series(2000.0, index=idx)
    rut.iloc[-2] = 2655.11
    rut.iloc[-1] = 2581.79
    yahoo = {"^W5000": w, "^GSPC": spx, "^NDX": ndx, "^RUT": rut}
    vix_idx = pd.bdate_range("2025-11-20", "2026-11-20")
    vix = pd.Series(20.0, index=vix_idx)
    vix.iloc[-2] = 28.44
    vix.iloc[-1] = 35.89
    v9 = vix.copy(); v9.iloc[-1] = 40.72
    v3 = vix.copy(); v3.iloc[-1] = 30.94
    vv = vix.copy(); vv.iloc[-1] = 138.03
    cboe = {"vix": vix, "vix9d": v9, "vix3m": v3, "vvix": vv}
    months = pd.period_range("2000-01", "2026-10", freq="M").astype(str)
    margin = pd.Series({m: 200000 + i * 1000 for i, m in enumerate(months)}, dtype=float)
    margin.loc["2000-03"] = 299933
    margin.loc["2007-07"] = 416403
    margin.loc["2021-10"] = 935862
    margin.loc["2025-10"] = 1183654
    margin.loc["2026-09"] = 1480054
    margin.loc["2026-10"] = 1448360
    gdp = pd.Series({"2026Q2": 32563030.0, "2026Q3": 33010036.0, "2020Q1": 20000000.0})
    p = us_risk.build_payload(
        yahoo=yahoo, cboe=cboe, margin=margin, margin_source="test",
        gdp=gdp, gdp_meta={"published": "October 29, 2026"}, errors={}, now=NOW,
    )
    assert p["status"] == "ok" and p["asof"] == "2026-11-20"
    assert p["market"]["w5000"] == 68957.11
    assert p["market"]["indices"]["sp500"]["close"] == 6910.39
    assert p["market"]["indices"]["sp500"]["chg_pct"] == pytest.approx(6910.39 / 7175.83 - 1, abs=1e-4)
    assert p["market"]["buffett"]["gdp_period"] == "2026Q3"
    assert p["market"]["buffett"]["ratio"] == pytest.approx(68957.11 / 33010.036, abs=1e-4)
    assert p["leverage"]["month"] == "2026-10"
    assert p["leverage"]["mom_pct"] == pytest.approx(1448360 / 1480054 - 1, abs=1e-4)
    assert p["leverage"]["yoy_pct"] == pytest.approx(1448360 / 1183654 - 1, abs=1e-4)
    peaks = {x["year"]: x for x in p["leverage"]["peaks"]}
    assert peaks[2000]["peak_month"] == "2000-03" and peaks[2021]["peak_usd_millions"] == 935862
    assert p["vix"]["close"] == 35.89 and p["vix"]["term"] == "倒挂" and p["vix"]["vvix"] == 138.03
    assert p["vix"]["chg"] == pytest.approx(7.45, abs=0.01)
    assert p["judgment"]["sentiment"] == "偏冷"
    # missing block stays 无数据, not a made-up number
    p2 = us_risk.build_payload(yahoo=yahoo, now=NOW)
    assert p2["vix"]["status"] == "missing" and p2["leverage"]["status"] == "missing"
    assert p2["market"]["buffett"]["status"] == "missing"


def test_refresh_failure_uses_snapshot_label(tmp_path, monkeypatch):
    seed = {
        "status": "ok",
        "updated_at": "2026-11-21T10:00:00+08:00",
        "asof": "2026-11-20",
        "market": {"status": "ok", "asof": "2026-11-20", "w5000": 68957.11, "block": "market"},
        "leverage": {"status": "ok", "month": "2026-10", "asof": "2026-10", "debit_usd_millions": 1448360, "block": "leverage"},
        "vix": {"status": "ok", "asof": "2026-11-20", "close": 35.89, "block": "vix"},
        "judgment": {"line": "估值无数据，杠杆无数据，情绪无数据"},
    }
    seed_path = tmp_path / "seed.json"
    seed_path.write_text(json.dumps(seed), encoding="utf-8")
    monkeypatch.setattr(us_risk, "_cache", None)
    monkeypatch.setattr(us_risk, "RUNTIME_PATH", tmp_path / "runtime.json")
    monkeypatch.setattr(us_risk, "SEED_PATHS", (seed_path,))
    monkeypatch.setattr(us_risk, "fetch_remote_seed", lambda: None)

    def boom():
        raise RuntimeError("network down")

    out = us_risk.refresh({"yahoo": boom, "cboe": boom, "bea": boom, "finra": boom}, use_remote=False, now=NOW)
    assert out["from_snapshot"] is True
    assert "上次快照" in (out.get("note") or "")
    # snapshot() itself must not hit the network
    called = []

    def no_http(*a, **k):
        called.append(1)
        raise AssertionError("snapshot must not use network")

    monkeypatch.setattr(us_risk, "_http_get", no_http)
    monkeypatch.setattr(us_risk, "fetch_yahoo_closes", no_http)
    s = us_risk.snapshot()
    assert s["status"] == "ok" and s["asof"] == "2026-11-20"
    assert not called


def test_endpoint_serves_seed_without_blocking(monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setenv("SKIP_AKSHARE", "1")
    monkeypatch.setenv("SKIP_INDEX_FLOW", "1")
    import app as app_mod

    calls = []
    monkeypatch.setattr(us_risk, "snapshot", lambda: {"status": "ok", "updated_at": "2020-01-01T00:00:00+08:00", "asof": "2020-01-01"})
    monkeypatch.setattr(us_risk, "needs_refresh", lambda value: True)
    monkeypatch.setattr(app_mod, "_spawn_bg_refresh", lambda key, fn, **kw: calls.append((key, kw)) or "started")
    c = app_mod.app.test_client()
    r = c.get("/api/us/risk")
    assert r.status_code == 200 and r.get_json()["refresh_status"] == "started"
    assert calls[-1][0] == "us-risk"
    r = c.get("/api/us/risk/refresh")
    assert r.status_code == 200 and r.get_json()["refresh_status"] == "started"


def test_frontend_and_static_wiring():
    tpl = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    assert 'id="usrisk-content"' in tpl and "/static/us_risk_view.js?v={{ asset_v }}" in tpl
    assert "研究参考，不能下单" in tpl
    docs = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    assert f"./static/us_risk_view.js?v={ASSET_V}" in docs
    js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    assert "'/api/us/risk': './api/us_risk.json'" in js and "UsRiskView" in js
    assert (ROOT / "static" / "us_risk_view.js").read_bytes() == (ROOT / "docs" / "static" / "us_risk_view.js").read_bytes()
    assert "data/us_risk_latest.json" in (ROOT / ".gitignore").read_text(encoding="utf-8")
    src = (ROOT / "us_risk.py").read_text(encoding="utf-8")
    assert "eastmoney.com" not in src.lower() and "push2" not in src and "push2his" not in src
    a = (ROOT / "data" / "us_risk.json").read_bytes()
    b = (ROOT / "docs" / "api" / "us_risk.json").read_bytes()
    assert a == b
    p = json.loads(a)
    assert p["status"] == "ok"
    json.dumps(p, allow_nan=False)
    # asset_v bumped
    assert f'asset_v = "{ASSET_V}"' in (ROOT / "app.py").read_text(encoding="utf-8")


def test_snapshot_needs_refresh_age(tmp_path, monkeypatch):
    monkeypatch.setattr(us_risk, "_cache", None)
    monkeypatch.setattr(us_risk, "RUNTIME_PATH", tmp_path / "none.json")
    monkeypatch.setattr(us_risk, "SEED_PATHS", (tmp_path / "x.json",))
    s = us_risk.snapshot()
    assert s["status"] == "no_data" and us_risk.needs_refresh(s)
    a = tmp_path / "a.json"
    a.write_text(json.dumps({"status": "ok", "updated_at": "2026-11-22T15:00:00+08:00"}), encoding="utf-8")
    monkeypatch.setattr(us_risk, "SEED_PATHS", (a,))
    monkeypatch.setattr(us_risk, "_now", lambda: NOW)
    s = us_risk.snapshot()
    assert s["age_hours"] == 1.0 and not us_risk.needs_refresh(s)
