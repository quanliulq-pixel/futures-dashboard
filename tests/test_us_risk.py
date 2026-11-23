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
ASSET_V = "20261123midx2"
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
    view = (ROOT / "static" / "us_risk_view.js").read_text(encoding="utf-8")
    assert "历次重大下跌对比" in view and "cycleTable" in view
    assert "暗盘" in view and "darkCard" in view and "DIX" in view
    assert "研究参考，不能下单" in view
    js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    assert "'/api/us/risk': './api/us_risk.json'" in js and "UsRiskView" in js
    assert (ROOT / "static" / "us_risk_view.js").read_bytes() == (ROOT / "docs" / "static" / "us_risk_view.js").read_bytes()
    assert "data/us_risk_latest.json" in (ROOT / ".gitignore").read_text(encoding="utf-8")
    src = (ROOT / "us_risk.py").read_text(encoding="utf-8") + "\n" + (ROOT / "us_risk_dark.py").read_text(encoding="utf-8")
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



def test_drawdown_highs_annual_and_episodes():
    """可控日线：最高点、年内回撤、完成轮次恢复日、进行中轮次、VIX 峰值，均无前瞻。"""
    # 2021: 100 → 110 (May 3) → 90 (May 17) → 120 (year end). intra-year DD = 90/110-1.
    # 2022: 120 → 150 (Mar 1) → 75 (Jun 1) → 80 (year end, not recovered). ATH=150.
    idx = pd.to_datetime([
        "2021-01-04", "2021-05-03", "2021-05-17", "2021-12-31",
        "2022-03-01", "2022-06-01", "2022-12-30",
    ])
    c = pd.Series([100.0, 110.0, 90.0, 120.0, 150.0, 75.0, 80.0], index=idx)
    h = us_risk.highs_and_current_drawdown(c)
    assert h["ath"] == 150.0 and h["ath_date"] == "2022-03-01"
    assert h["dd_ath"] == pytest.approx(80 / 150 - 1, abs=1e-4)
    assert h["ytd_high"] == 150.0 and h["dd_ytd"] == pytest.approx(80 / 150 - 1, abs=1e-4)
    ann = us_risk.annual_drawdowns(c, start_year=2021)
    by = {r["year"]: r for r in ann}
    assert by[2021]["max_dd"] == pytest.approx(90 / 110 - 1, abs=1e-4)
    assert by[2021]["peak_date"] == "2021-05-03" and by[2021]["trough_date"] == "2021-05-17"
    assert by[2021]["year_return"] is None  # no prior year close
    assert by[2022]["max_dd"] == pytest.approx(75 / 150 - 1, abs=1e-4)
    assert by[2022]["year_return"] == pytest.approx(80 / 120 - 1, abs=1e-4)
    assert by[2022]["incomplete"] is True  # last date is year end of sample's last year... last is 2022-12-30, year==last year
    eps = us_risk.drawdown_episodes(c, min_dd=-0.10)
    assert len(eps) == 2
    done, cur = eps[0], eps[1]
    assert done["peak_date"] == "2021-05-03" and done["trough_date"] == "2021-05-17"
    assert done["dd"] == pytest.approx(90 / 110 - 1, abs=1e-4)
    assert done["recovery_date"] == "2021-12-31"
    assert done["duration_days"] == (date(2021, 5, 17) - date(2021, 5, 3)).days
    assert done["recovery_days"] == (date(2021, 12, 31) - date(2021, 5, 17)).days
    assert done["ongoing"] is False
    assert cur["ongoing"] is True and cur["recovery_date"] is None and cur["recovery_days"] is None
    assert cur["peak_date"] == "2022-03-01" and cur["trough"] == 75.0
    # recovery must not precede trough
    assert date.fromisoformat(done["recovery_date"]) >= date.fromisoformat(done["trough_date"])
    vix = pd.Series([12.0, 40.0, 18.0], index=pd.to_datetime(["2022-03-01", "2022-05-15", "2022-06-01"]))
    vp = us_risk.vix_peak_in_range(vix, date(2022, 3, 1), date(2022, 6, 1))
    assert vp["status"] == "ok" and vp["close"] == 40.0 and vp["asof"] == "2022-05-15"
    assert us_risk.vix_peak_in_range(None, date(2022, 3, 1), date(2022, 6, 1))["status"] == "missing"
    # 浅回撤完成轮次（min_dd=-0.10）不应进入列表：再造一段只有 -5% 的完成回撤
    shallow = pd.Series([100.0, 95.0, 101.0], index=pd.to_datetime(["2020-01-02", "2020-01-15", "2020-02-03"]))
    assert us_risk.drawdown_episodes(shallow, min_dd=-0.10) == []


def test_named_events_and_comparison_and_payload_drawdowns():
    # 覆盖 2020-03 窗口的一轮 + 当前浅回撤
    idx = pd.to_datetime(["2019-12-31", "2020-02-19", "2020-03-23", "2020-08-18", "2026-11-01", "2026-11-20"])
    c = pd.Series([100.0, 120.0, 80.0, 130.0, 140.0, 130.0], index=idx)
    eps = us_risk.label_episodes(us_risk.drawdown_episodes(c, min_dd=-0.10))
    labels = {e.get("label"): e for e in eps}
    assert "2020-03 新冠" in labels
    assert labels["2020-03 新冠"]["dd"] == pytest.approx(80 / 120 - 1, abs=1e-4)
    assert labels["当前回撤"]["ongoing"] is True
    major = us_risk.select_major_episodes(eps)
    assert any(e.get("label") == "2020-03 新冠" for e in major)
    highs = us_risk.highs_and_current_drawdown(c)
    ann = us_risk.annual_drawdowns(c, start_year=2019)
    cmp_ = us_risk.compare_to_history(highs["dd_ath"], ann, eps)
    assert cmp_["status"] == "ok" and "当前距历史最高收盘回撤" in cmp_["line"]
    yahoo = {"^GSPC": c, "^NDX": c, "^RUT": c, "^DJI": c, "^W5000": c * 400}
    p = us_risk.build_payload(yahoo=yahoo, now=NOW)
    assert p["drawdowns"]["status"] == "ok"
    spx = next(x for x in p["drawdowns"]["indices"] if x["key"] == "sp500")
    assert spx["highs"]["ath_date"] == "2026-11-01"
    assert spx["annual"][0]["year"] >= 1990 or spx["annual"][0]["year"] == 2019
    # 缺数据不编造
    p2 = us_risk.build_payload(now=NOW)
    assert p2["drawdowns"]["status"] == "missing"



def test_named_window_not_swallowed():
    """2018 高点未收复就遇到 2020 疫情时，2018Q4 与 2020-03 仍应分别给出窗口峰值/谷底。"""
    idx = pd.to_datetime([
        "2018-08-31", "2018-12-24", "2019-06-03",
        "2020-02-19", "2020-03-23", "2020-11-16",
    ])
    c = pd.Series([1740.0, 1260.0, 1500.0, 1690.0, 990.0, 1750.0], index=idx)
    ev18 = us_risk.named_event_drawdown(c, date(2018, 9, 1), date(2018, 12, 31))
    ev20 = us_risk.named_event_drawdown(c, date(2020, 1, 1), date(2020, 6, 30))
    assert ev18["peak_date"] == "2018-08-31" and ev18["trough_date"] == "2018-12-24"
    assert ev18["dd"] == pytest.approx(1260 / 1740 - 1, abs=1e-4)
    assert ev20["peak_date"] == "2020-02-19" and ev20["trough_date"] == "2020-03-23"
    assert ev20["dd"] == pytest.approx(990 / 1690 - 1, abs=1e-4)
    # 窗口内无数据
    assert us_risk.named_event_drawdown(c, date(1995, 1, 1), date(1995, 12, 31)) is None

def test_event_context_no_lookahead_and_missing_fields():
    import us_risk_context as ctx
    # GDP published lag: Q3 2026 ends 9-30, +30d = 10-30, so 10-06 still uses Q2
    gdp = pd.Series({"2026Q2": 32563030.0, "2026Q3": 33010036.0})
    assert ctx.gdp_published_asof(gdp, date(2026, 10, 6))[1] == "2026Q2"
    assert ctx.gdp_published_asof(gdp, date(2026, 10, 30))[1] == "2026Q3"
    # Z.1 Q2 2026 ends 6-30, +75d = 9-13
    zlvl = pd.DataFrame({
        "date": ["2025:Q4", "2026:Q1", "2026:Q2"],
        "period": ["2025Q4", "2026Q1", "2026Q2"],
        "quarter_end": [date(2025, 12, 31), date(2026, 3, 31), date(2026, 6, 30)],
        "LM893064105.Q": [100.0, 110.0, 120.0],
        "LM153064105.Q": [40.0, 44.0, 50.4],
        "LM653064100.Q": [15.0, 16.0, 18.0],
        "LM563064100.Q": [10.0, 11.0, 12.0],
        "LM573064105.Q": [5.0, 5.0, 6.0],
        "LM343064105.Q": [1.0, 1.0, 1.0],
        "LM223064145.Q": [3.0, 3.0, 3.0],
        "LM263064105.Q": [18.0, 19.0, 21.6],
    })
    row = ctx.z1_row_published(zlvl, date(2026, 9, 1))
    assert row["period"] == "2026Q1"  # Q2 not yet published
    row = ctx.z1_row_published(zlvl, date(2026, 9, 13))
    assert row["period"] == "2026Q2"
    own = ctx.ownership_from_row(row)
    assert own["status"] == "ok" and own["hh_direct_pct"] == 42.0
    assert own["mf_etf_pct"] == 25.0 and own["foreign_pct"] == 18.0 and own["pension_pct"] == 8.3
    # flows SAAR/4
    zfl = pd.DataFrame({
        "date": ["2020:Q1", "2020:Q2"],
        "period": ["2020Q1", "2020Q2"],
        "quarter_end": [date(2020, 3, 31), date(2020, 6, 30)],
        "FA653064100.Q": [-400.0, -80.0],
        "FA563064100.Q": [200.0, 40.0],
        "FA153064105.Q": [100.0, 20.0],
        "FA263064105.Q": [40.0, 8.0],
        "FA573064105.Q": [-10.0, 0.0],
        "FA343064105.Q": [0.0, 0.0],
        "FA223064145.Q": [0.0, 0.0],
    })
    # 以谷底日为公布截止时，Q1 要到 2020-06-14 才发布 → 无数据（防止把当时未公布的季报提前写入）
    early = ctx.z1_flow_window(zfl, date(2020, 2, 19), date(2020, 3, 23))
    assert early["status"] == "missing"
    # 历史回填：以今日为公布截止，则可计入与窗口相交的 Q1
    filled = ctx.z1_flow_window(zfl, date(2020, 2, 19), date(2020, 3, 23), published_asof=date(2026, 11, 20))
    assert filled["status"] == "ok" and filled["mf_usd_millions"] == pytest.approx(-100.0)
    assert filled["etf_usd_millions"] == pytest.approx(50.0)
    # 窗口延伸到 8 月、以谷底/截止日 8-1 计：只有已公布的 Q1
    mid = ctx.z1_flow_window(zfl, date(2020, 2, 19), date(2020, 8, 1))
    assert mid["status"] == "ok" and mid["mf_usd_millions"] == pytest.approx(-100.0)
    assert mid["etf_usd_millions"] == pytest.approx(50.0)
    # gold missing before series start
    gold = pd.Series([300.0, 330.0], index=pd.to_datetime(["2000-08-30", "2002-10-09"]))
    w = ctx.series_window(gold, date(2000, 3, 24), date(2002, 10, 9))
    assert w["status"] == "missing" and w["trough"]["close"] == 330.0
    w2 = ctx.series_window(gold, date(2000, 8, 30), date(2002, 10, 9))
    assert w2["status"] == "ok" and w2["chg_pct"] == pytest.approx(330 / 300 - 1, abs=1e-4)


def test_cycle_row_and_payload_event_context():
    import us_risk_context as ctx
    idx = pd.bdate_range("1999-12-01", "2026-11-20")
    w = pd.Series(50000.0 + pd.RangeIndex(len(idx)) * 0.5, index=idx)
    # plant known points
    w.loc["2020-02-19"] = 35000.0
    w.loc["2020-03-23"] = 25000.0
    gdp = pd.Series({"2019Q4": 21000.0, "2020Q1": 19500.0})  # millions
    margin = pd.Series({"2020-02": 500000.0, "2020-03": 400000.0})
    gold = pd.Series([1600.0, 1500.0], index=pd.to_datetime(["2020-02-19", "2020-03-23"]))
    tnx = pd.Series([1.50, 0.80], index=pd.to_datetime(["2020-02-19", "2020-03-23"]))
    row = ctx.build_cycle_row(
        "2020-03 新冠", date(2020, 2, 19), date(2020, 3, 23),
        w5000=w, gdp=gdp, margin=margin, z1_levels=None, z1_flows=None,
        gold=gold, tnx=tnx, tlt=None, ongoing=False,
    )
    assert row["market"]["peak"]["status"] == "ok"
    assert row["market"]["peak"]["w5000"] == 35000.0
    assert row["leverage"]["peak"]["month"] == "2020-02"
    assert row["leverage"]["debit_chg_pct"] == pytest.approx(400000 / 500000 - 1, abs=1e-4)
    assert row["haven"]["gold"]["chg_pct"] == pytest.approx(1500 / 1600 - 1, abs=1e-4)
    assert row["haven"]["ust10y"]["chg_pp"] == pytest.approx(-0.70, abs=1e-3)
    assert row["haven"]["tlt"]["status"] == "missing"
    assert row["flows"]["ici"]["status"] == "missing"
    assert row["ownership"]["peak"]["status"] == "missing"
    # payload includes event_context
    spx = pd.Series([100.0, 120.0, 80.0, 130.0, 140.0, 130.0], index=pd.to_datetime(
        ["2019-12-31", "2020-02-19", "2020-03-23", "2020-08-18", "2026-11-01", "2026-11-20"]))
    yahoo = {"^GSPC": spx, "^NDX": spx, "^RUT": spx, "^DJI": spx, "^W5000": w, "GC=F": gold, "^TNX": tnx}
    p = us_risk.build_payload(yahoo=yahoo, margin=margin, gdp=gdp, now=NOW)
    assert p["event_context"]["status"] == "ok"
    labels = [c["label"] for c in p["event_context"]["cycles"]]
    assert "2020-03 新冠" in labels and "当前回撤" in labels


def test_parse_z1_period_colon_dates():
    import us_risk_context as ctx
    assert ctx.parse_z1_period("2026:Q2") == date(2026, 6, 30)
    assert ctx.normalize_period_label("2026:Q2") == "2026Q2"
    csv = b"date,LM893064105.Q,LM153064105.Q\n2026:Q1,110,44\n2026:Q2,120,50.4\n"
    df = ctx.parse_z1_table(csv)
    assert list(df["period"]) == ["2026Q1", "2026Q2"]
    assert list(df["quarter_end"]) == [date(2026, 3, 31), date(2026, 6, 30)]


def test_current_extras_latest_flow_uses_asof():
    import us_risk_context as ctx
    zfl = pd.DataFrame({
        "date": ["2025:Q3", "2025:Q4", "2026:Q1", "2026:Q2"],
        "period": ["2025Q3", "2025Q4", "2026Q1", "2026Q2"],
        "quarter_end": [date(2025, 9, 30), date(2025, 12, 31), date(2026, 3, 31), date(2026, 6, 30)],
        "FA653064100.Q": [80.0, 40.0, 20.0, 8.0],
        "FA563064100.Q": [0.0, 0.0, 0.0, 4.0],
        "FA153064105.Q": [0.0, 0.0, 0.0, 0.0],
        "FA263064105.Q": [0.0, 0.0, 0.0, 0.0],
        "FA573064105.Q": [0.0, 0.0, 0.0, 0.0],
        "FA343064105.Q": [0.0, 0.0, 0.0, 0.0],
        "FA223064145.Q": [0.0, 0.0, 0.0, 0.0],
    })
    out = ctx.current_extras(
        asof=date(2026, 11, 20), equity_peak=date(2026, 10, 6),
        w5000=None, z1_levels=None, z1_flows=zfl, gold=None, tnx=None, tlt=None,
    )
    assert out["flows_latest_quarter"]["status"] == "ok"
    assert out["flows_latest_quarter"]["quarters"] == ["2026Q2"]
    assert out["flows_latest_quarter"]["mf_etf_usd_millions"] == pytest.approx(3.0)
    assert out["flows_last_four_quarters"]["quarters"] == ["2025Q3", "2025Q4", "2026Q1", "2026Q2"]


def test_debit_chg_missing_when_trough_month_unpublished():
    import us_risk_context as ctx
    w = pd.Series([70000.0, 68000.0], index=pd.to_datetime(["2026-10-30", "2026-11-20"]))
    margin = pd.Series({"2026-10": 1448360.0})
    row = ctx.build_cycle_row(
        "当前回撤", date(2026, 10, 6), date(2026, 11, 20),
        w5000=w, gdp=None, margin=margin, z1_levels=None, z1_flows=None,
        gold=None, tnx=None, tlt=None, ongoing=True,
    )
    assert row["leverage"]["trough"]["month"] == "2026-10"
    assert row["leverage"]["trough"]["month_requested"] == "2026-11"
    assert row["leverage"]["debit_chg_pct"] is None


def test_dark_dix_gex_and_offexchange():
    import us_risk_dark as dark
    csv = (
        "date,price,dix,gex\n"
        "2011-05-02,1361.22,0.3788,1897312571\n"
        "2026-11-19,7175.83,0.430765,-900000756\n"
        "2026-11-20,6910.39,0.388471,-2159921683\n"
    )
    df = dark.parse_dix_csv(csv)
    assert str(df["date"].iloc[0].date()) == "2011-05-02"
    blk = dark.build_dix_gex(df)
    assert blk["status"] == "ok" and blk["asof"] == "2026-11-20"
    assert blk["dix"]["value_pct"] == pytest.approx(38.85, abs=0.02)
    assert blk["dix"]["chg_1d_pp"] == pytest.approx(-4.23, abs=0.02)
    assert blk["gex"]["value_billion"] == pytest.approx(-2.16, abs=0.01)
    # 2011 之前无数据
    early = dark.dark_at(date(2007, 10, 9), df, None)
    assert early["dix"]["status"] == "missing" and early["gex"]["status"] == "missing"
    late = dark.dark_at(date(2026, 11, 20), df, None)
    assert late["dix"]["status"] == "ok" and late["dix"]["value_pct"] == pytest.approx(38.85, abs=0.02)
    vol = pd.DataFrame({
        "date": pd.to_datetime(["2026-11-19", "2026-11-19", "2026-11-20", "2026-11-20"]),
        "participant": ["NASDAQ (Q)", "FINRA / Nasdaq TRF Carteret (DQ)", "NASDAQ (Q)", "FINRA / Nasdaq TRF Carteret (DQ)"],
        "shares": [100.0, 50.0, 100.0, 80.0],
    })
    off = dark.build_offexchange(vol)
    assert off["status"] == "ok" and off["share_pct"] == pytest.approx(44.44, abs=0.02)
    assert off["chg_1d_pp"] == pytest.approx((80 / 180 - 50 / 150) * 100, abs=0.05)
    j = dark.judge_dark(
        {"status": "ok", "percentile_1y": 1.2, "value_pct": 38.85},
        {"status": "ok", "value": -2159921683.0},
    )
    assert j["dix_band"] == "偏少" and "买盘偏少" in j["line"] and "GEX 为负" in j["line"]
    payload = dark.build_dark_payload(dix_df=df, volume=vol, ats_weekly={"status": "missing", "reason": "无数据"})
    assert payload["status"] == "ok" and payload["off_exchange"]["share_pct"] == pytest.approx(44.44, abs=0.02)
    # 判断写入 payload
    p = us_risk.build_payload(now=NOW)
    assert p["dark"]["status"] in {"missing", "no_data"} or p["dark"].get("reason")
    assert "暗盘" in (p["judgment"].get("line") or "")


def test_parse_cboe_offexchange_venues():
    import us_risk_dark as dark
    raw = (
        "Day,Market Participant,Tape A Shares,Tape B Shares,Tape C Shares,Total Shares,"
        "Tape A Notional,Tape B Notional,Tape C Notional,Total Notional,"
        "Tape A Trade Count,Tape B Trade Count,Tape C Trade Count,Total Trade Count\n"
        "2026-11-20,NYSE (N),1,1,1,50,0,0,0,0,0,0,0,0\n"
        "2026-11-20,FINRA / Nasdaq TRF Carteret (DQ),1,1,1,40,0,0,0,0,0,0,0,0\n"
        "2026-11-20,FINRA / NYSE TRF (DN),1,1,1,10,0,0,0,0,0,0,0,0\n"
    )
    vol = dark.parse_cboe_market_history(raw)
    daily = dark.offexchange_daily(vol)
    assert abs(float(daily.iloc[0]["share"]) - 0.5) < 1e-9
    import us_risk_dark as dark
    assert dark.is_offexchange_venue('NASDAQ (DQ)') and dark.is_offexchange_venue('NYSE (DN)')
    assert dark.is_offexchange_venue('ADF (D)') and dark.is_offexchange_venue('FINRA / Nasdaq TRF Carteret (DQ)')
    assert not dark.is_offexchange_venue('NASDAQ (Q)') and not dark.is_offexchange_venue('NYSE (N)')
