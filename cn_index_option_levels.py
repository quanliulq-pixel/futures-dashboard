# -*- coding: utf-8 -*-
"""A股指数「期权倾向」→ 具体参考价位（合约/行权价/权利金/盈亏平衡/止损止盈/保护腿）。

纯计算、无网络：只读 cffex_options 快照（docs/api/cn_index_options.json）里真实挂牌的行权价与报价。
缺链/缺报价/算不出 IV → 输出「无数据」，绝不编造行权价或权利金。

选合约规则（事先固定，见 RULES_CN）：
  * 月份：优先 DTE 落在 [MIN_DTE_PREFERRED, MAX_DTE] 中最接近 TARGET_DTE 的月份；否则取 DTE≥MIN_DTE 的最近月；都没有→无数据。
  * 卖 Put：行权价同时满足 ① ≤ 最近支撑 ② |Δ|≤0.25（BS，用该档虚值报价反解 IV，失败用 ATM IV）
    ③ ≤ 现价×(1−1σ)，σ=ATM IV×√(DTE/365)。三条都是行权价上限 → 取满足全部条件的最高档（=最保守的那条起作用）。
    且该档 bid>0（可成交）。
  * 卖 Call：镜像——≥ 最近阻力、Δ≤0.25、≥ 现价×(1+1σ)，取满足全部条件的最低档。
  * 保护腿（价差）：向虚值方向再走约 WING_STEPS 个挂牌档位，买入同月同类型期权。
  * 止损：只看权利金——涨到开仓价 STOP_MULT 倍即止损（价差/铁秃鹰按净权利金 STOP_MULT 倍）；不再用标的破位止损。
  * 止盈：权利金衰减 TAKE_PROFIT_PCT（买回价 = 开仓价×(1−50%)）；剩余 DTE≤EXIT_DTE 平仓不博到期。
"""
from __future__ import annotations

import math
from typing import Any

LABEL_CN = "参考价位，不构成下单建议"
NO_DATA = "无数据"

RISK_FREE = 0.015          # 年化无风险利率（近似）
TARGET_DTE = 30
MIN_DTE_PREFERRED = 14
MAX_DTE = 60
MIN_DTE = 7
DELTA_MAX = 0.25
SIGMA_MULT = 1.0
WING_STEPS = 2
STOP_MULT = 2.0
TAKE_PROFIT_PCT = 0.5
EXIT_DTE = 5
MIN_PREMIUM_PTS = 2.0      # 低于此权利金（指数点）提示「权利金过薄」
MULTIPLIER = {"csi300": 100, "sse50": 100, "csi1000": 100}  # 中金所股指期权合约乘数 100 元/点

RULES_CN = [
    f"月份：优先剩余 {MIN_DTE_PREFERRED}–{MAX_DTE} 天中最接近 {TARGET_DTE} 天的月份，否则取剩余≥{MIN_DTE} 天的最近月",
    f"卖Put：行权价 ≤ 最近支撑、|Δ|≤{DELTA_MAX}、≤ 现价×(1−{SIGMA_MULT:g}σ)，三条同时满足取最高档（最保守条件生效）",
    f"卖Call：行权价 ≥ 最近阻力、Δ≤{DELTA_MAX}、≥ 现价×(1+{SIGMA_MULT:g}σ)，三条同时满足取最低档",
    "σ = ATM IV × √(DTE/365)；Δ 用 Black-Scholes（欧式、r=1.5%），IV 由该档中间价反解，反解失败用 ATM IV",
    f"保护腿：向虚值方向再走 {WING_STEPS} 档买入同月期权，组成价差（最大亏损有限）",
    f"止损：仅按权利金——涨到开仓价 {STOP_MULT:g} 倍止损（价差/铁秃鹰按净权利金 {STOP_MULT:g} 倍）",
    f"止盈：权利金衰减 {int(TAKE_PROFIT_PCT*100)}%；剩余≤{EXIT_DTE} 天平仓",
]

BIAS_PLAN = {
    "偏卖Put": ("put",),
    "偏卖Call": ("call",),
    "铁秃鹰/价差": ("put", "call"),
    "不做裸卖": ("put", "call"),
}


def _f(x: Any) -> float | None:
    try:
        if x is None or x == "":
            return None
        v = float(x)
        if v != v or math.isinf(v):
            return None
        return v
    except Exception:
        return None


# ---------- Black-Scholes ----------
def _ncdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(kind: str, s: float, k: float, t: float, vol: float, r: float = RISK_FREE) -> float:
    if t <= 0 or vol <= 0:
        return max(0.0, (s - k) if kind == "call" else (k - s))
    sq = vol * math.sqrt(t)
    d1 = (math.log(s / k) + (r + 0.5 * vol * vol) * t) / sq
    d2 = d1 - sq
    if kind == "call":
        return s * _ncdf(d1) - k * math.exp(-r * t) * _ncdf(d2)
    return k * math.exp(-r * t) * _ncdf(-d2) - s * _ncdf(-d1)


def bs_delta(kind: str, s: float, k: float, t: float, vol: float, r: float = RISK_FREE) -> float | None:
    if t <= 0 or vol <= 0 or s <= 0 or k <= 0:
        return None
    d1 = (math.log(s / k) + (r + 0.5 * vol * vol) * t) / (vol * math.sqrt(t))
    return _ncdf(d1) if kind == "call" else _ncdf(d1) - 1.0


def implied_vol(kind: str, price: float, s: float, k: float, t: float, r: float = RISK_FREE) -> float | None:
    """Bisection IV (decimal). None if price outside no-arbitrage bounds."""
    if price is None or price <= 0 or s <= 0 or k <= 0 or t <= 0:
        return None
    intrinsic = max(0.0, (s - k * math.exp(-r * t)) if kind == "call" else (k * math.exp(-r * t) - s))
    if price <= intrinsic + 1e-9:
        return None
    lo, hi = 0.005, 3.0
    if bs_price(kind, s, k, t, hi, r) < price:
        return None
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if bs_price(kind, s, k, t, mid, r) > price:
            hi = mid
        else:
            lo = mid
    return round(0.5 * (lo + hi), 4)


# ---------- chain helpers ----------
def _board(options_payload: dict | None, index_id: str) -> dict | None:
    if not options_payload:
        return None
    b = (options_payload.get("by_index") or {}).get(index_id)
    if b:
        return b
    for x in options_payload.get("products") or []:
        if x.get("index_id") == index_id:
            return x
    return None


def pick_month(months: list[dict]) -> dict | None:
    good = [m for m in months or [] if (m.get("days") or 0) >= MIN_DTE and (m.get("strikes") or [])]
    if not good:
        return None
    pref = [m for m in good if MIN_DTE_PREFERRED <= (m.get("days") or 0) <= MAX_DTE]
    if pref:
        return min(pref, key=lambda m: abs((m.get("days") or 0) - TARGET_DTE))
    return min(good, key=lambda m: m.get("days") or 0)


def _quote(leg: dict | None) -> dict | None:
    """Reference premium: mid of bid/ask when both >0, else last. None when unusable."""
    if not leg:
        return None
    bid, ask, last = _f(leg.get("bid")), _f(leg.get("ask")), _f(leg.get("last"))
    if bid is not None and ask is not None and bid > 0 and ask >= bid:
        return {"premium": round((bid + ask) / 2.0, 2), "basis": "买卖中间价", "bid": bid, "ask": ask, "last": last}
    if last is not None and last > 0:
        return {"premium": round(last, 2), "basis": "最新价", "bid": bid, "ask": ask, "last": last}
    return None


def month_atm_iv(month: dict, spot: float) -> float | None:
    """ATM IV (decimal) by BS-inverting ATM call/put mids; fallback to the crawler's atm_iv_est."""
    t = (month.get("days") or 0) / 365.0
    rows = month.get("strikes") or []
    if not rows or t <= 0:
        return None
    atm = min(rows, key=lambda r: abs(_f(r.get("strike")) - spot) if _f(r.get("strike")) else 1e18)
    k = _f(atm.get("strike"))
    vals = []
    for kind in ("call", "put"):
        q = _quote(atm.get(kind))
        if q and k:
            iv = implied_vol(kind, q["premium"], spot, k, t)
            if iv:
                vals.append(iv)
    if vals:
        return round(sum(vals) / len(vals), 4)
    est = _f(month.get("atm_iv_est"))
    if est and est > 0 and atm.get("strike") == month.get("atm_strike"):
        return round(est / 100.0, 4)
    return None


def _legs(month: dict, kind: str, spot: float, atm_iv: float) -> list[dict]:
    t = (month.get("days") or 0) / 365.0
    out = []
    for r in month.get("strikes") or []:
        k = _f(r.get("strike"))
        leg = r.get(kind)
        q = _quote(leg)
        if not k or not q:
            continue
        if kind == "put" and k >= spot:
            continue
        if kind == "call" and k <= spot:
            continue
        iv = implied_vol(kind, q["premium"], spot, k, t)
        iv_src = "该档反解"
        if iv is None:
            iv, iv_src = atm_iv, "ATM IV"
        d = bs_delta(kind, spot, k, t, iv)
        out.append({"strike": k, "code": (leg or {}).get("code"), "oi": (leg or {}).get("oi"),
                    "quote": q, "iv": iv, "iv_src": iv_src, "delta": d})
    out.sort(key=lambda x: x["strike"])
    return out


def _fmt_k(k: float) -> str:
    return str(int(k)) if float(k).is_integer() else f"{k:g}"


def _short_leg(kind: str, legs: list[dict], spot: float, atm_iv: float, dte: int,
               support: float | None, resistance: float | None) -> tuple[dict | None, dict]:
    sig = atm_iv * math.sqrt(dte / 365.0) * SIGMA_MULT
    if kind == "put":
        bounds = {"sigma": spot * (1 - sig), "level": support}
        ok = [x for x in legs
              if x["delta"] is not None and abs(x["delta"]) <= DELTA_MAX
              and x["strike"] <= bounds["sigma"]
              and (support is None or x["strike"] <= support)
              and (x["quote"].get("bid") or 0) > 0]
        pick = max(ok, key=lambda x: x["strike"]) if ok else None
    else:
        bounds = {"sigma": spot * (1 + sig), "level": resistance}
        ok = [x for x in legs
              if x["delta"] is not None and abs(x["delta"]) <= DELTA_MAX
              and x["strike"] >= bounds["sigma"]
              and (resistance is None or x["strike"] >= resistance)
              and (x["quote"].get("bid") or 0) > 0]
        pick = min(ok, key=lambda x: x["strike"]) if ok else None
    bounds["sigma_pct"] = round(sig * 100, 2)
    return pick, bounds


def _wing(kind: str, legs: list[dict], short: dict) -> dict | None:
    if kind == "put":
        further = sorted([x for x in legs if x["strike"] < short["strike"]], key=lambda x: -x["strike"])
    else:
        further = sorted([x for x in legs if x["strike"] > short["strike"]], key=lambda x: x["strike"])
    if not further:
        return None
    return further[min(WING_STEPS, len(further)) - 1]


def _leg_plan(kind: str, legs: list[dict], spot: float, atm_iv: float, dte: int,
              support: float | None, resistance: float | None, mult: int) -> dict:
    side_cn = "卖Put" if kind == "put" else "卖Call"
    if not legs:
        return {"side_cn": side_cn, "status": "nodata", "reason_cn": f"{NO_DATA}（该月无可用{'Put' if kind=='put' else 'Call'}报价）"}
    short, bounds = _short_leg(kind, legs, spot, atm_iv, dte, support, resistance)
    level = support if kind == "put" else resistance
    if not short:
        return {"side_cn": side_cn, "status": "none",
                "reason_cn": "无同时满足条件的挂牌行权价（支撑/阻力、Δ≤0.25、1σ 外且有买价）",
                "bounds": {k: (round(v, 2) if isinstance(v, float) else v) for k, v in bounds.items()}}
    prem = short["quote"]["premium"]
    k = short["strike"]
    be = k - prem if kind == "put" else k + prem
    dist = (k / spot - 1.0) * 100.0
    plan: dict[str, Any] = {
        "side_cn": side_cn,
        "status": "ok",
        "code": short["code"],
        "strike": k,
        "premium": prem,
        "premium_basis": short["quote"]["basis"],
        "bid": short["quote"].get("bid"),
        "ask": short["quote"].get("ask"),
        "oi": short.get("oi"),
        "breakeven": round(be, 2),
        "dist_pct": round(dist, 2),
        "be_dist_pct": round((be / spot - 1.0) * 100.0, 2),
        "delta": round(short["delta"], 3) if short["delta"] is not None else None,
        "iv_pct": round(short["iv"] * 100, 1) if short.get("iv") else None,
        "iv_src": short.get("iv_src"),
        "premium_yuan": round(prem * mult, 0),
        "bounds": {"sigma": round(bounds["sigma"], 2), "sigma_pct": bounds["sigma_pct"], "level": level},
        "take_profit_cn": f"权利金回落至 {prem * (1 - TAKE_PROFIT_PCT):.1f} 点附近（衰减{int(TAKE_PROFIT_PCT*100)}%）买回；剩余≤{EXIT_DTE}天平仓",
        "stop_cn": f"权利金升至 {prem * STOP_MULT:.1f} 点（开仓价 {STOP_MULT:g} 倍）止损",
    }
    if prem < MIN_PREMIUM_PTS:
        plan["thin_cn"] = f"权利金仅 {prem:g} 点，过薄，性价比低"
    w = _wing(kind, legs, short)
    if w:
        credit = round(prem - w["quote"]["premium"], 2)
        width = abs(k - w["strike"])
        if credit > 0:
            plan["spread"] = {
                "buy_code": w["code"],
                "buy_strike": w["strike"],
                "buy_premium": w["quote"]["premium"],
                "net_credit": credit,
                "width": width,
                "max_loss_pts": round(width - credit, 2),
                "max_loss_yuan": round((width - credit) * mult, 0),
                "breakeven": round(k - credit if kind == "put" else k + credit, 2),
                "stop_cn": f"价差净权利金升至 {credit * STOP_MULT:.1f} 点（{STOP_MULT:g} 倍）止损",
            }
    return plan


ETF_OPTIONS = {"csi300": ["510300", "159919"], "sse50": ["510050"]}


def etf_options_note(index_id: str) -> str | None:
    codes = ETF_OPTIONS.get(index_id)
    if not codes:
        return None
    return f"ETF期权（{'/'.join(codes)}）：{NO_DATA}（看板未接入其期权链，不给价位）"


def build_option_levels(index_id: str, option_bias: str, options_payload: dict | None,
                        levels: dict | None, spot_fallback: float | None = None) -> dict:
    """Return structured reference levels for one index card."""
    base: dict[str, Any] = {
        "label_cn": LABEL_CN,
        "bias_cn": option_bias or "—",
        "rules_cn": RULES_CN,
        "asof": (options_payload or {}).get("updated_at"),
        "source_cn": "新浪转播中金所延时行情（快照）",
    }
    plan_sides = BIAS_PLAN.get(option_bias or "")
    if not plan_sides:
        base.update(status="nodata", reason_cn=f"期权倾向「{option_bias or '—'}」无对应卖方结构")
        return base
    board = _board(options_payload, index_id)
    if not board or not board.get("months"):
        base.update(status="nodata", reason_cn=f"{NO_DATA}（期权链快照缺失）")
        return base
    month = pick_month(board.get("months") or [])
    if not month:
        base.update(status="nodata", reason_cn=f"{NO_DATA}（无剩余≥{MIN_DTE}天且有报价的月份）")
        return base
    spot = _f(month.get("spot")) or _f(spot_fallback)
    if not spot:
        base.update(status="nodata", reason_cn=f"{NO_DATA}（标的现价缺失）")
        return base
    atm_iv = month_atm_iv(month, spot)
    if not atm_iv:
        base.update(status="nodata", reason_cn=f"{NO_DATA}（ATM IV 无法计算）")
        return base
    dte = int(month.get("days") or 0)
    lv = levels or {}
    sup = [float(x) for x in (lv.get("support") or []) if _f(x) is not None and float(x) < spot]
    res = [float(x) for x in (lv.get("resistance") or []) if _f(x) is not None and float(x) > spot]
    support = max(sup) if sup else None
    resistance = min(res) if res else None
    mult = MULTIPLIER.get(index_id, 100)

    base.update(
        status="ok",
        product=board.get("short"),
        month=month.get("code"),
        expire=month.get("expire"),
        dte=dte,
        spot=round(spot, 2),
        atm_iv_pct=round(atm_iv * 100, 1),
        support=support,
        resistance=resistance,
        multiplier=mult,
    )
    legs = {}
    for side in plan_sides:
        legs[side] = _leg_plan(side, _legs(month, side, spot, atm_iv), spot, atm_iv, dte,
                               support, resistance, mult)
    base["legs"] = legs

    if option_bias == "铁秃鹰/价差":
        base["structure_cn"] = "铁秃鹰（卖Put价差 + 卖Call价差，务必带保护腿）"
        base["defined_risk_required"] = True
    elif option_bias == "不做裸卖":
        base["structure_cn"] = "观望为主；如做仅限有限风险价差（下列两腿必须带保护腿）"
        base["defined_risk_required"] = True
    elif option_bias == "偏卖Put":
        base["structure_cn"] = "卖虚值Put（可选买更低档Put组成牛市Put价差）"
        base["defined_risk_required"] = False
    else:
        base["structure_cn"] = "卖虚值Call（可选买更高档Call组成熊市Call价差）"
        base["defined_risk_required"] = False

    if option_bias in ("铁秃鹰/价差", "不做裸卖"):
        p, c = legs.get("put") or {}, legs.get("call") or {}
        ps, cs = p.get("spread"), c.get("spread")
        if p.get("status") == "ok" and c.get("status") == "ok" and ps and cs:
            credit = round(ps["net_credit"] + cs["net_credit"], 2)
            width = max(ps["width"], cs["width"])
            base["condor"] = {
                "net_credit": credit,
                "net_credit_yuan": round(credit * mult, 0),
                "max_loss_pts": round(width - credit, 2),
                "max_loss_yuan": round((width - credit) * mult, 0),
                "breakeven_low": round(p["strike"] - credit, 2),
                "breakeven_high": round(c["strike"] + credit, 2),
                "take_profit_cn": f"组合权利金回落至 {credit * (1 - TAKE_PROFIT_PCT):.1f} 点附近买回",
                "stop_cn": f"组合净权利金升至 {credit * STOP_MULT:.1f} 点（{STOP_MULT:g} 倍）止损",
            }
    if all((legs.get(s) or {}).get("status") != "ok" for s in plan_sides):
        base["status"] = "none"
    return base
