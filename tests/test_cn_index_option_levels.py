# -*- coding: utf-8 -*-
"""期权倾向 → 参考价位：真实挂牌行权价、保守选档、缺数据 → 无数据。"""
import copy
import json
import math
from pathlib import Path

import pytest

import cn_index_option_levels as L
from cn_index_advice import advice_for_index

FX = json.loads((Path(__file__).parent / "fixtures" / "cn_index_options_io.json").read_text(encoding="utf-8"))
LEVELS = {"support": [4000.0, 3986.0, 3950.0], "resistance": [4083.0, 4100.0, 4200.0]}


def _strikes(month, kind):
    return {r["strike"]: r[kind] for r in month["strikes"] if r.get(kind)}


def test_bs_roundtrip_and_delta_signs():
    p = L.bs_price("put", 4000, 3800, 37 / 365, 0.2)
    assert abs(L.implied_vol("put", p, 4000, 3800, 37 / 365) - 0.2) < 1e-3
    assert L.bs_delta("put", 4000, 3800, 0.1, 0.2) < 0 < L.bs_delta("call", 4000, 4200, 0.1, 0.2)
    assert L.implied_vol("call", 0.0, 4000, 4200, 0.1) is None
    assert L.implied_vol("put", 1.0, 4000, 4500, 0.1) is None  # below intrinsic


def test_pick_month_prefers_30ish_dte():
    m = L.pick_month(FX["by_index"]["csi300"]["months"])
    assert m["code"] == "io2612" and m["days"] == 37
    only_front = [dict(FX["by_index"]["csi300"]["months"][0])]
    assert L.pick_month(only_front)["code"] == "io2611"  # 9 天 ≥ 7 天，作后备
    short = [dict(only_front[0], days=3)]
    assert L.pick_month(short) is None


def test_sell_put_uses_real_strike_and_most_conservative_rule():
    o = L.build_option_levels("csi300", "偏卖Put", FX, LEVELS, 4075.7)
    assert o["status"] == "ok" and o["label_cn"] == "参考价位，不构成下单建议"
    assert o["asof"] == FX["updated_at"]
    leg = o["legs"]["put"]
    month = L.pick_month(FX["by_index"]["csi300"]["months"])
    real = _strikes(month, "put")
    assert leg["strike"] in real and leg["code"] == real[leg["strike"]]["code"]
    # all three ceilings hold
    assert leg["strike"] <= 4000.0
    assert leg["strike"] <= leg["bounds"]["sigma"]
    assert abs(leg["delta"]) <= 0.25
    # premium = real mid, breakeven = K - premium
    q = real[leg["strike"]]
    assert leg["premium"] == round((q["bid"] + q["ask"]) / 2, 2)
    assert math.isclose(leg["breakeven"], leg["strike"] - leg["premium"], abs_tol=0.01)
    assert leg["dist_pct"] < 0
    # next listed strike up must violate at least one rule (it's the highest qualifying)
    higher = sorted(k for k in real if k > leg["strike"])[0]
    t = o["dte"] / 365
    sigma_raw = o["spot"] * (1 - L.month_atm_iv(month, month["spot"]) * math.sqrt(t))
    hq = real[higher]
    h_iv = L.implied_vol("put", round((hq["bid"] + hq["ask"]) / 2, 2), month["spot"], higher, t)
    h_delta = abs(L.bs_delta("put", month["spot"], higher, t, h_iv))
    assert higher > sigma_raw or higher > 4000.0 or h_delta > 0.25
    assert "2倍" in leg["stop_cn"] and "50%" in leg["take_profit_cn"]
    sp = leg["spread"]
    assert sp["buy_strike"] < leg["strike"] and sp["buy_strike"] in real
    assert math.isclose(sp["max_loss_pts"], sp["width"] - sp["net_credit"], abs_tol=0.01)


def test_sell_call_mirror():
    o = L.build_option_levels("csi300", "偏卖Call", FX, LEVELS, 4075.7)
    leg = o["legs"]["call"]
    assert set(o["legs"]) == {"call"}
    assert leg["strike"] >= 4083.0 and leg["strike"] >= leg["bounds"]["sigma"] and leg["delta"] <= 0.25
    assert leg["breakeven"] == round(leg["strike"] + leg["premium"], 2)
    assert leg["spread"]["buy_strike"] > leg["strike"]


def test_condor_combines_both_spreads():
    o = L.build_option_levels("csi300", "铁秃鹰/价差", FX, LEVELS, 4075.7)
    c = o["condor"]
    p, k = o["legs"]["put"], o["legs"]["call"]
    assert o["defined_risk_required"] is True
    assert math.isclose(c["net_credit"], p["spread"]["net_credit"] + k["spread"]["net_credit"], abs_tol=0.01)
    assert c["breakeven_low"] < p["strike"] and c["breakeven_high"] > k["strike"]


def test_missing_chain_or_quotes_gives_nodata_not_fabricated():
    assert L.build_option_levels("csi300", "偏卖Put", None, LEVELS, 4000)["status"] == "nodata"
    assert "无数据" in L.build_option_levels("sse50", "偏卖Put", FX, LEVELS, 2700)["reason_cn"]
    fx = copy.deepcopy(FX)
    for m in fx["by_index"]["csi300"]["months"]:
        for r in m["strikes"]:
            for side in ("put", "call"):
                if r.get(side):
                    r[side].update(bid=None, ask=None, last=None)
        m["atm_iv_est"] = None
    o = L.build_option_levels("csi300", "偏卖Put", fx, LEVELS, 4075.7)
    assert o["status"] == "nodata" and "无数据" in o["reason_cn"]
    assert "legs" not in o


def test_unmatched_bias_has_no_levels():
    o = L.build_option_levels("csi300", "—", FX, LEVELS, 4075.7)
    assert o["status"] == "nodata" and "legs" not in o


def test_advice_embeds_option_levels_for_final_bias():
    row = {"id": "csi300", "name": "沪深300", "price": 4075.7, "vs_ma20_pct": 1.0, "vs_ma60_pct": 0.5,
           "rsi14": 52, "chg_21d": 1.0, "hv20": 14.0, "hv_pctile": 50}
    a = advice_for_index(row, FX, None)
    ol = a["option_levels"]
    assert ol["bias_cn"] == a["option_bias_cn"]
    assert ol["label_cn"] == "参考价位，不构成下单建议"
    assert "510300" in (ol.get("etf_note_cn") or "") and "无数据" in ol["etf_note_cn"]
