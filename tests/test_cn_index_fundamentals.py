# -*- coding: utf-8 -*-
"""A股指数基本面：因子、政策方向、两级权重/重新归一、事件窗口、旧快照、接入建议（全部离线）。"""
from __future__ import annotations

import json
import socket
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

import cn_index_fundamentals as F
from cn_index_advice import advice_for_index, build_cn_index_advice

TODAY = date(2026, 11, 9)
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def deny(*a, **k):
        raise OSError("network disabled in tests")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "create_connection", deny)


def daily(n, start_val, step, end=TODAY):
    out = []
    d = end
    vals = [start_val + step * i for i in range(n)]
    days = []
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    days.reverse()
    return list(zip(days, vals))


def monthly(vals, end=(2026, 10)):
    y, m = end
    out = []
    for v in reversed(vals):
        out.append((F._month_end(y, m), v))
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return list(reversed(out))


# ---------------------------------------------------------------- parsing

def test_to_date_formats():
    assert F._to_date("2026年10月份") == date(2026, 10, 31)
    assert F._to_date("2025年05月15日") == date(2025, 5, 15)
    assert F._to_date("20261106") == date(2026, 11, 6)
    assert F._to_date("202604") == date(2026, 4, 30)
    assert F._to_date("2026-11-09") == date(2026, 11, 9)
    assert F._to_date("nan") is None and F._to_date(None) is None


def test_series_from_drops_nan_and_sorts():
    rows = [{"d": "2026-11-02", "v": 2}, {"d": "2026-11-01", "v": 1}, {"d": "2026-11-03", "v": float("nan")},
            {"d": None, "v": 3}]
    assert F.series_from(rows, "d", "v") == [(date(2026, 11, 1), 1.0), (date(2026, 11, 2), 2.0)]


# ---------------------------------------------------------------- policy direction

def test_lpr_cut_is_bullish_hike_bearish_flat_neutral():
    cut = [(date(2026, 4, 20), 3.10), (date(2026, 8, 20), 3.00), (date(2026, 10, 20), 3.00)]
    f = F.lpr_factor(cut, TODAY)
    assert f["status"] == "ok" and f["signal"] == pytest.approx(0.5) and f["value"] == pytest.approx(-10.0)
    assert f["asof"] == "2026-10-20" and f["source_url"].startswith("https://") and f["group"] == "policy"
    hike = [(date(2026, 4, 20), 3.00), (date(2026, 10, 20), 3.25)]
    assert F.lpr_factor(hike, TODAY)["signal"] == -1.0
    flat = [(date(2025, 5, 20), 3.00), (date(2026, 10, 20), 3.00)]
    assert F.lpr_factor(flat, TODAY)["signal"] == 0.0


def test_lpr_stale_or_missing_is_no_data():
    assert F.lpr_factor([], TODAY)["status"] == "missing"
    old = [(date(2026, 1, 20), 3.0), (date(2026, 8, 20), 3.0)]
    f = F.lpr_factor(old, TODAY)
    assert f["status"] == "missing" and "过期" in f["reason"] and f["signal"] is None and f["value"] is None


def test_rrr_cut_within_year_bullish_old_cut_neutral():
    f = F.rrr_factor([(date(2026, 3, 1), -0.5)], TODAY)
    assert f["signal"] == 1.0 and f["value"] == -0.5
    g = F.rrr_factor([(date(2025, 5, 15), -0.5)], TODAY)
    assert g["status"] == "ok" and g["signal"] == 0.0 and "无调整" in g["value_text"]
    h = F.rrr_factor([(date(2026, 6, 1), 0.25)], TODAY)
    assert h["signal"] == -0.5


def test_money_rate_falling_is_easing():
    falling = daily(80, 1.6, -0.004)
    f = F.money_rate_factor(falling, TODAY)
    assert f["status"] == "ok" and f["signal"] > 0
    rising = daily(80, 1.2, 0.004)
    assert F.money_rate_factor(rising, TODAY)["signal"] < 0
    assert F.money_rate_factor(daily(30, 1.4, 0), TODAY)["status"] == "missing"


def test_cgb_falling_bullish_and_stale():
    f = F.cgb10y_factor(daily(70, 1.80, -0.005), TODAY)
    assert f["signal"] == 1.0
    stale = daily(70, 1.8, 0, end=date(2026, 9, 1))
    assert F.cgb10y_factor(stale, TODAY)["status"] == "missing"


# ---------------------------------------------------------------- valuation

def _pe_hist(last):
    pts = [(F._month_end(y, m), 10.0 + (i % 10)) for i, (y, m) in
           enumerate([(y, m) for y in range(2016, 2027) for m in range(1, 13)]) if F._month_end(y, m) < TODAY]
    return pts + [(TODAY, last)]


def test_pe_low_percentile_bullish_high_bearish():
    lo = F.pctile_factor("pe_pctile", "PE", _pe_hist(10.0), TODAY, F._src_pe("csi300"), metric="PE")
    hi = F.pctile_factor("pe_pctile", "PE", _pe_hist(19.5), TODAY, F._src_pe("csi300"), metric="PE")
    assert lo["signal"] > 0.5 and hi["signal"] == -1.0
    assert lo["scope"] == "index" and lo["group"] == "valuation" and "legulegu.com" in lo["source_url"]


def test_pe_insufficient_history_no_data():
    short = [(TODAY - timedelta(days=30 * i), 12.0) for i in range(10)][::-1]
    assert F.pctile_factor("pe_pctile", "PE", short, TODAY, F._src_pe("csi300"), metric="PE")["status"] == "missing"


def test_erp_high_is_cheap_bullish():
    pe = _pe_hist(10.0)
    cgb = [(d, 2.5) for d, _ in pe]
    f = F.erp_factor(pe, cgb, TODAY, F._src_erp("csi300"))
    assert f["status"] == "ok" and f["signal"] > 0 and f["value"] == pytest.approx(10.0 - 2.5)
    assert F.erp_factor(pe, [], TODAY, F._src_erp("csi300"))["status"] == "missing"


# ---------------------------------------------------------------- liquidity / macro

def test_margin_and_m1m2():
    up = F.margin_factor(daily(30, 20000, 50), TODAY)
    assert up["signal"] > 0
    dn = F.margin_factor(daily(30, 25000, -150), TODAY)
    assert dn["signal"] == -1.0
    m1 = monthly([1.0, 1.5, 2.0, 3.0, 4.0], end=(2026, 9))
    m2 = monthly([7.0, 7.0, 7.0, 7.0, 7.0], end=(2026, 9))
    f = F.m1m2_factor(m1, m2, TODAY)
    assert f["status"] == "ok" and f["signal"] == 1.0 and f["value"] == -3.0


def test_monthly_staleness_rule():
    pmi = monthly([50, 50, 50, 51], end=(2026, 4))
    f = F.pmi_factor(pmi, TODAY)
    assert f["status"] == "missing" and "过期" in f["reason"]


def test_pmi_prices_credit_directions():
    assert F.pmi_factor(monthly([49.0, 49.2, 49.4, 51.0]), TODAY)["signal"] > 0
    assert F.pmi_factor(monthly([50.5, 50.3, 50.1, 49.0]), TODAY)["signal"] < 0
    p = F.prices_factor(monthly([1.0, 2.0, 2.5, 3.0]), monthly([0.5]), TODAY)
    assert p["signal"] == 1.0 and "CPI" in p["value_text"]
    vals = [1000.0] * 12 + [2000.0, 2000.0, 2000.0]
    c = F.credit_factor(monthly(vals, end=(2026, 9)), TODAY)
    assert c["signal"] == 1.0
    assert F.credit_factor(monthly([1.0] * 5, end=(2026, 9)), TODAY)["status"] == "missing"


# ---------------------------------------------------------------- weighting

def _ok(key, sig):
    return F._f(key, key, {"label": "x", "url": "https://x", "function": "f"}, value=1, value_text="v",
                asof="2026-11-09", signal=sig)


def _miss(key):
    return F._missing(key, key, {"label": "x"}, "无")


def test_weights_constants_policy_largest_and_sum_to_one():
    assert sum(F.GROUP_WEIGHTS.values()) == pytest.approx(1.0)
    assert max(F.GROUP_WEIGHTS, key=F.GROUP_WEIGHTS.get) == "policy"
    for g, ws in F.FACTOR_WEIGHTS.items():
        assert sum(ws.values()) == pytest.approx(1.0), g


def test_full_data_weighting_and_contributions_sum():
    facs = [_ok(k, 1.0 if F.FACTOR_GROUP[k] == "policy" else 0.0) for k in F.FACTOR_GROUP]
    sc = F.score_index_factors(facs)
    assert sc["fund_score"] == pytest.approx(50 + 50 * 0.35, abs=0.05)
    assert sc["fund_status"] == "ok"
    assert sum(f["weight"] for f in sc["fund_factors"]) == pytest.approx(1.0, abs=0.01)
    assert sc["fund_factors"][0]["group"] == "policy"  # policy listed first
    pol = [g for g in sc["fund_groups"] if g["key"] == "policy"][0]
    assert pol["emphasis"] and pol["weight"] == pytest.approx(0.35)


def test_missing_within_group_keeps_group_weight():
    facs = [_ok("lpr", 1.0), _miss("rrr"), _miss("money_rate"), _miss("cgb10y")]
    facs += [_ok(k, 0.0) for k in F.FACTOR_GROUP if F.FACTOR_GROUP[k] != "policy"]
    sc = F.score_index_factors(facs)
    lpr = [f for f in sc["fund_factors"] if f["key"] == "lpr"][0]
    assert lpr["weight"] == pytest.approx(0.35, abs=0.001)
    assert sc["fund_score"] == pytest.approx(67.5, abs=0.05)
    assert sc["fund_status"] == "partial" and "rrr" in sc["fund_status_cn"]
    for f in sc["fund_factors"]:
        if f["status"] != "ok":
            assert f["contribution"] is None and f["weight"] == 0.0 and f["value"] is None


def test_missing_whole_group_reweights_across_groups():
    facs = [_miss(k) for k in F.FACTOR_WEIGHTS["policy"]]
    facs += [_ok(k, 1.0 if F.FACTOR_GROUP[k] == "valuation" else 0.0) for k in F.FACTOR_GROUP
             if F.FACTOR_GROUP[k] != "policy"]
    sc = F.score_index_factors(facs)
    val = [g for g in sc["fund_groups"] if g["key"] == "valuation"][0]
    assert val["weight"] == pytest.approx(0.25 / 0.65, abs=0.001)
    assert sc["fund_score"] == pytest.approx(50 + 50 * 0.25 / 0.65, abs=0.05)


def test_all_missing_gives_none_not_imputed():
    sc = F.score_index_factors([_miss(k) for k in F.FACTOR_GROUP])
    assert sc["fund_score"] is None and sc["fund_status_cn"] == "基本面无数据"


def test_tech_score_mapping():
    assert F.tech_score_from_advice(0) == 50.0
    assert F.tech_score_from_advice(-35) == 32.5
    assert F.tech_score_from_advice(250) == 100.0
    assert F.tech_score_from_advice(None) is None


# ---------------------------------------------------------------- event window

def test_lpr_dates_weekend_shift():
    ds = F.lpr_dates(date(2026, 11, 9), date(2027, 1, 31))
    assert ds == [date(2026, 11, 20), date(2026, 12, 21), date(2027, 1, 20)]


def test_event_window_with_expiry_and_default():
    ev = F.policy_events(TODAY)
    assert ev and all(e["sources"][0]["url"].startswith("https://www.pbc.gov.cn/") for e in ev)
    w = F.event_window(ev, TODAY, date(2026, 11, 20))
    assert w["in_window"] and w["events"][0]["date"] == "2026-11-20" and "不因此改分" in w["text"]
    w2 = F.event_window(ev, TODAY, date(2026, 11, 19))
    assert not w2["in_window"]
    w3 = F.event_window(ev, TODAY, None)
    assert w3["in_window"] and "30 天" in w3["basis"]


# ---------------------------------------------------------------- option-bias adjustment

@pytest.mark.parametrize("tech,stance,fund,comb,ev,expect", [
    ("偏卖Put", "防守区间", 39.0, 40.0, False, "铁秃鹰/价差"),
    ("偏卖Put", "防守区间", 41.0, 40.0, False, "偏卖Put"),
    ("偏卖Call", "卖波动", 61.0, 50.0, False, "铁秃鹰/价差"),
    ("偏卖Call", "卖波动", 38.2, 41.1, False, "偏卖Call"),
    ("铁秃鹰/价差", "偏多", 65.0, 62.0, False, "偏卖Put"),
    ("铁秃鹰/价差", "偏多", 65.0, 62.0, True, "铁秃鹰/价差"),
    ("铁秃鹰/价差", "偏空", 35.0, 38.0, False, "偏卖Call"),
    ("铁秃鹰/价差", "观望高波", 80.0, 70.0, False, "铁秃鹰/价差"),
    ("不做裸卖", "观望高波", 90.0, 80.0, False, "不做裸卖"),
    ("偏卖Put", "防守区间", None, None, False, "偏卖Put"),
])
def test_adjust_option_bias(tech, stance, fund, comb, ev, expect):
    out, why = F.adjust_option_bias(tech, stance, fund, comb, event_in_window=ev)
    assert out == expect
    if out != tech:
        assert why


# ---------------------------------------------------------------- integration with advice

def _bundle(fund_sig=0.0, fetched=None):
    mf = [_ok(k, fund_sig) for k in F.FACTOR_GROUP if F.FACTOR_GROUP[k] != "valuation"]
    idx = {"csi300": [F._f(k, k, F._src_pe("csi300"), value=1, value_text="v", asof="2026-11-09", signal=fund_sig)
                      for k in F.FACTOR_WEIGHTS["valuation"]]}
    return {"fetched_at": fetched or datetime.now(F.CST).isoformat(timespec="seconds"),
            "market_factors": mf, "index_factors": idx, "events": F.policy_events(TODAY), "notes": ["n"]}


ROW = {"id": "csi300", "name": "沪深300", "price": 4056.11, "vs_ma20_pct": -1.0, "vs_ma60_pct": -3.0, "rsi14": 42,
       "chg_21d": -6.0, "chg_5d": -0.5, "from_52w_high_pct": -10, "from_52w_low_pct": 12, "bias_cn": "中性",
       "hv20": 17.5, "hv_pctile": 65.5}
OPT = {"by_index": {"csi300": {"months": [{"code": "io2611", "expire": "2026-11-20", "days": 11,
                                           "atm_iv_est": 20.2, "atm_strike": 4050, "atm_band": []}]}}}


def test_old_seed_without_bundle_marks_not_computed_and_keeps_technical():
    a = advice_for_index(ROW, OPT)
    base_keys = {"stance_cn", "score", "confidence", "option_bias_cn", "levels", "bullets_cn"}
    assert base_keys <= set(a)
    assert a["fund_status"] == "not_computed" and a["fund_status_cn"] == "基本面未计算"
    assert a["fund_score"] is None and a["combined_score"] == a["tech_score"]
    assert a["option_bias_cn"] == a["option_bias_tech_cn"]


def test_bundle_changes_only_bias_not_stance_score_confidence():
    plain = advice_for_index(ROW, OPT)
    bear = advice_for_index(ROW, OPT, _bundle(-1.0))
    assert (bear["stance_cn"], bear["score"], bear["confidence"]) == (plain["stance_cn"], plain["score"], plain["confidence"])
    assert bear["fund_score"] == 0.0
    assert bear["combined_score"] == pytest.approx(0.6 * bear["tech_score"], abs=0.05)
    if plain["option_bias_cn"] == "偏卖Put":
        assert bear["option_bias_cn"] == "铁秃鹰/价差" and bear["fund_adjust_cn"]
        assert any("铁秃鹰/价差" in b for b in bear["bullets_cn"])
    assert bear["policy_event_window"]["in_window"]  # LPR 2026-11-20 == io2611 expiry
    assert [f["group"] for f in bear["fund_factors"]][0] == "policy"


def test_stale_bundle_not_used():
    old = (datetime.now(F.CST) - timedelta(hours=60)).isoformat(timespec="seconds")
    a = advice_for_index(ROW, OPT, _bundle(1.0, fetched=old))
    assert a["fund_score"] is None and "48" in a["fund_status_cn"]


def test_index_without_valuation_marks_missing():
    row = dict(ROW, id="sse50")
    a = advice_for_index(row, None, _bundle(0.5))
    vals = [f for f in a["fund_factors"] if f["group"] == "valuation"]
    assert vals and all(f["status"] == "missing" for f in vals)
    assert a["fund_score"] is not None


def test_build_uses_embedded_bundle_and_method():
    payload = {"indices": [ROW], "index_fundamentals": _bundle(0.2)}
    b = build_cn_index_advice(payload, OPT)
    assert b["advices"]["csi300"]["fund_score"] is not None
    assert "政策 35%" in b["method"]["fundamental"] and b["fund_notes"] == ["n"]


def test_real_seed_renders_not_computed():
    seed = json.loads((ROOT / "docs" / "api" / "cn_indices.json").read_text(encoding="utf-8"))
    opt = json.loads((ROOT / "docs" / "api" / "cn_index_options.json").read_text(encoding="utf-8"))
    seed.pop("index_fundamentals", None)
    b = build_cn_index_advice(seed, opt)
    for a in b["advices"].values():
        assert a["fund_status"] == "not_computed" and a["fund_score"] is None


# ---------------------------------------------------------------- provider

def test_provider_nonfatal_failures_and_cache():
    calls = {"n": 0}

    def boom():
        raise RuntimeError("down")

    def lpr():
        calls["n"] += 1
        return [(date(2026, 4, 20), 3.1), (date(2026, 10, 20), 3.0)]

    fetchers = {"lpr": lpr, "rrr": boom, "valuation": lambda: {"errors": {"pe:csi300": "Timeout"}}}
    p = F.IndexFundProvider(fetchers=fetchers, index_ids=["csi300"])
    assert p.get_cached() is None
    b = p.refresh()
    assert calls["n"] == 1 and "rrr" in b["errors"] and b["errors"]["pe:csi300"] == "Timeout"
    m = {f["key"]: f for f in b["market_factors"]}
    assert m["lpr"]["status"] == "ok" and m["rrr"]["status"] == "missing" and "down" in m["rrr"]["reason"]
    pe = b["index_factors"]["csi300"][0]
    assert pe["status"] == "missing" and "Timeout" in pe["reason"]
    assert p.get_cached() is b and p.refresh() is b and calls["n"] == 1
    json.dumps(b, ensure_ascii=False, default=str)


def test_pick_bundle_freshest():
    now = datetime.now(F.CST)
    a = {"fetched_at": (now - timedelta(hours=5)).isoformat()}
    b = {"fetched_at": (now - timedelta(hours=1)).isoformat()}
    assert F.pick_bundle(a, b, None) is b
    assert F.pick_bundle(None, {}) is None


def test_static_docs_sync_and_script_order_for_index_view():
    for name in ("cn_index_fund_view.js", "app.js", "style.css"):
        assert (ROOT / "static" / name).read_bytes() == (ROOT / "docs" / "static" / name).read_bytes(), name
    for html, prefix in ((ROOT / "templates" / "index.html", "/static/"), (ROOT / "docs" / "index.html", "./static/")):
        t = html.read_text(encoding="utf-8")
        assert t.index(prefix + "cn_index_fund_view.js") < t.index(prefix + "app.js")
