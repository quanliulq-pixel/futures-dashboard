# -*- coding: utf-8 -*-
"""Theta-Harvest-style edge score + PutStrike-style pre-trade checklist for CSP."""
from __future__ import annotations

import math
from typing import Any


DEFAULT_WEIGHTS = {
    "vrp": 30,
    "iv_pctile": 25,
    "term": 20,
    "rv_stable": 15,
    "skew": 10,
}

# PutStrike-ish factor weights for wheel/CSP composite (sum≈100)
DEFAULT_PUT_FACTORS = {
    "premium": 22,
    "theta": 8,
    "delta_proxy": 13,
    "dte": 10,
    "liquidity": 10,
    "otm": 10,
    "iv_rank": 10,
    "stability": 17,
}


def _clip(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, float(x)))


def _r(x: Any, n: int = 1) -> Any:
    if x is None:
        return None
    try:
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return None
        return round(v, n)
    except Exception:
        return None


def score_vrp(iv: float | None, hv20: float | None) -> tuple[float, str]:
    """VRP quality 0-30 (and normalized 0-100 for display)."""
    if iv is None or hv20 is None or hv20 <= 0:
        return 0.0, "VRP未知"
    spread = iv - hv20
    ratio = iv / hv20
    # map: negative VRP ~0, rich >=8pts / ratio>=1.25 ~满分
    pts = 0.0
    if spread <= 0 or ratio < 1.0:
        pts = max(0.0, 10 + spread)  # soft
        note = "负VRP/无溢价"
    elif ratio < 1.15:
        pts = 12 + spread * 0.8
        note = "VRP偏薄"
    else:
        pts = 18 + min(12.0, spread * 0.9 + (ratio - 1.15) * 20)
        note = "VRP偏厚"
    return _clip(pts * (100 / 30) * 0.3 * 10 / 10), note  # keep raw 0-30 below


def vrp_points(iv: float | None, hv20: float | None) -> tuple[float, str, float | None]:
    if iv is None or hv20 is None or hv20 <= 0:
        return 0.0, "VRP未知", None
    spread = iv - hv20
    ratio = iv / hv20
    if spread <= 0 or ratio < 1.0:
        return _clip(max(0.0, 8 + spread), 0, 30), "负VRP", _r(spread, 1)
    if ratio < 1.15:
        return _clip(10 + min(8.0, spread * 0.7), 0, 30), "VRP偏薄", _r(spread, 1)
    pts = 18 + min(12.0, spread * 0.85 + (ratio - 1.15) * 25)
    return _clip(pts, 0, 30), "VRP偏厚", _r(spread, 1)


def iv_pctile_points(iv_pctile: float | None, hv_pctile: float | None = None) -> tuple[float, str]:
    p = iv_pctile if iv_pctile is not None else hv_pctile
    if p is None:
        return 0.0, "IV分位未知"
    # high percentile = options expensive historically → good for selling
    if p >= 80:
        return 25.0, "IV分位很高"
    if p >= 60:
        return 18.0 + (p - 60) * 0.35, "IV分位偏高"
    if p >= 40:
        return 10.0 + (p - 40) * 0.3, "IV分位中性"
    return max(0.0, p * 0.2), "IV分位偏低"


def term_points(near_iv: float | None, far_iv: float | None) -> tuple[float, str, str]:
    """Contango (far>near) good for selling; backwardation danger."""
    if near_iv is not None and near_iv <= 1:
        near_iv = None
    if far_iv is not None and far_iv <= 1:
        far_iv = None
    if near_iv is None or far_iv is None or near_iv <= 0:
        return 8.0, "期限结构未知", "unknown"
    slope = (far_iv - near_iv) / near_iv * 100
    if slope >= 5:
        return 20.0, "期限结构contango", "contango"
    if slope >= 0:
        return 14.0, "期限结构温和contango", "contango"
    if slope > -8:
        return 6.0, "期限结构偏平/轻倒挂", "flat"
    return 0.0, "期限结构倒挂(危险)", "backwardation"


def rv_stable_points(hv20: float | None, hv60: float | None) -> tuple[float, str, float | None]:
    if hv20 is None or hv60 is None or hv60 <= 0:
        return 7.0, "RV稳定性未知", None
    accel = hv20 / hv60
    if accel <= 0.9:
        return 15.0, "RV在回落", _r(accel, 2)
    if accel <= 1.05:
        return 11.0, "RV平稳", _r(accel, 2)
    if accel <= 1.15:
        return 5.0, "RV温和加速", _r(accel, 2)
    return 0.0, "RV加速中(风暴)", _r(accel, 2)


def skew_points(atm_iv: float | None, otm_put_iv: float | None) -> tuple[float, str, float | None]:
    if atm_iv is None or otm_put_iv is None or atm_iv <= 0:
        return 4.0, "Skew未知", None
    skew = otm_put_iv - atm_iv
    if skew >= 4:
        return 10.0, "Put偏斜丰厚", _r(skew, 1)
    if skew >= 1:
        return 7.0, "Put偏斜正常", _r(skew, 1)
    if skew >= -1:
        return 4.0, "Skew平坦", _r(skew, 1)
    return 1.0, "Skew反向", _r(skew, 1)


def compute_edge(
    *,
    iv: float | None,
    hv20: float | None,
    hv60: float | None,
    iv_pctile: float | None = None,
    hv_pctile: float | None = None,
    near_iv: float | None = None,
    far_iv: float | None = None,
    atm_iv: float | None = None,
    otm_put_iv: float | None = None,
    weights: dict | None = None,
) -> dict:
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    # normalize weights to sum 100
    s = sum(max(0.0, float(w[k])) for k in DEFAULT_WEIGHTS) or 100.0
    wn = {k: max(0.0, float(w[k])) / s * 100.0 for k in DEFAULT_WEIGHTS}

    vrp_pts, vrp_note, vrp_spread = vrp_points(iv, hv20)
    ivp_pts, ivp_note = iv_pctile_points(iv_pctile, hv_pctile)
    term_pts, term_note, term_reg = term_points(near_iv if near_iv is not None else iv, far_iv)
    rv_pts, rv_note, rv_accel = rv_stable_points(hv20, hv60)
    sk_pts, sk_note, skew = skew_points(atm_iv if atm_iv is not None else iv, otm_put_iv)

    # scale each bucket to its weight max
    def scale(pts, vmax, wmax):
        return (pts / vmax) * wmax if vmax else 0.0

    parts = {
        "vrp": _r(scale(vrp_pts, 30, wn["vrp"]), 1),
        "iv_pctile": _r(scale(ivp_pts, 25, wn["iv_pctile"]), 1),
        "term": _r(scale(term_pts, 20, wn["term"]), 1),
        "rv_stable": _r(scale(rv_pts, 15, wn["rv_stable"]), 1),
        "skew": _r(scale(sk_pts, 10, wn["skew"]), 1),
    }
    edge = _r(sum(parts.values()), 1)

    # regime / posture
    if term_reg == "backwardation" or (rv_accel is not None and rv_accel > 1.35):
        posture = "回避"
        posture_en = "AVOID"
    elif vrp_spread is not None and vrp_spread < 0:
        posture = "观望"
        posture_en = "WATCH"
        edge = _r(min(edge or 0, 54), 1)
    elif (edge or 0) >= 70:
        posture = "积极收租"
        posture_en = "AGGRESSIVE"
    elif (edge or 0) >= 55:
        posture = "可做"
        posture_en = "NORMAL"
    elif (edge or 0) >= 40:
        posture = "仅限定义风险"
        posture_en = "DEFINED_RISK"
    else:
        posture = "观望"
        posture_en = "WATCH"

    return {
        "edge": edge,
        "parts": parts,
        "weights": {k: _r(wn[k], 1) for k in wn},
        "notes": {
            "vrp": vrp_note,
            "iv_pctile": ivp_note,
            "term": term_note,
            "rv_stable": rv_note,
            "skew": sk_note,
        },
        "metrics": {
            "vrp_spread": vrp_spread,
            "term_regime": term_reg,
            "rv_accel": rv_accel,
            "skew": skew,
            "iv": _r(iv, 1),
            "hv20": _r(hv20, 1),
            "hv60": _r(hv60, 1),
            "iv_pctile": _r(iv_pctile if iv_pctile is not None else hv_pctile, 1),
        },
        "posture": posture,
        "posture_en": posture_en,
    }


def build_checklist(row: dict) -> dict:
    """14-ish rule pre-trade checklist; any hard veto → blocked."""
    put = row.get("put") or {}
    risk = row.get("risk") or {}
    edge = row.get("edge") or {}
    metrics = edge.get("metrics") or {}
    checks = []

    def add(ok: bool, hard: bool, code: str, title: str, detail: str):
        checks.append({"ok": ok, "hard": hard, "code": code, "title": title, "detail": detail})

    # 1 earnings
    earn = row.get("earnings") or risk.get("earnings")
    days = earn.get("days") if isinstance(earn, dict) else None
    add(not (days is not None and days <= 7), True, "earnings", "财报窗口",
        f"距财报 {days} 天" if days is not None else "无近期财报/ETF")

    # 2 liquidity
    oi = put.get("open_interest") or 0
    vol = put.get("volume") or 0
    if oi >= 100 or vol >= 50:
        add(True, False, "liquidity", "期权流动性", f"OI={oi} Vol={vol}")
    elif oi >= 20 or vol >= 10:
        add(False, False, "liquidity", "期权流动性", f"OI={oi} Vol={vol} · 偏薄")
    else:
        add(False, True, "liquidity", "期权流动性", f"OI={oi} Vol={vol}")

    # 3 bid-ask
    bid, ask = put.get("bid"), put.get("ask")
    mid = put.get("mid") or 0
    spread_ok = True
    spread_txt = "—"
    if bid is not None and ask is not None and mid:
        spr = ask - bid
        spread_ok = spr <= max(0.15, mid * 0.25)
        spread_txt = f"买卖价差 {spr:.2f}"
    add(spread_ok, False, "spread", "买卖价差", spread_txt)

    # 4 OTM cushion
    otm = abs(put.get("otm_pct") or 0)
    add(5 <= otm <= 15, False, "otm", "OTM安全垫", f"OTM {otm:.1f}%")

    # 5 DTE
    dte = put.get("dte")
    add(dte is not None and 14 <= dte <= 45, False, "dte", "到期日甜区", f"DTE {dte}")

    # 6 IV rich
    iv_tag = risk.get("iv_tag") or ""
    add(iv_tag in ("IV偏贵", "IV中性"), False, "iv_rich", "IV相对HV", iv_tag or "未知")

    # 7 VRP — 缺IV报价时不硬否决（Yahoo常给坏IV）
    vrp = metrics.get("vrp_spread")
    if vrp is None:
        add(True, False, "vrp", "波动率溢价VRP", "IV报价不可用·改看HV分位")
    else:
        add(vrp >= 0, True, "vrp", "波动率溢价VRP", f"VRP {vrp}")

    # 8 term structure
    term = metrics.get("term_regime")
    if term == "backwardation":
        add(False, True, "term", "波动率期限结构", edge.get("notes", {}).get("term") or "倒挂")
    elif term in (None, "unknown"):
        add(True, False, "term", "波动率期限结构", "期限结构未知")
    else:
        add(True, False, "term", "波动率期限结构", edge.get("notes", {}).get("term") or term or "—")

    # 9 RV accel — 高波动池允许温和加速；极端加速才硬否决
    accel = metrics.get("rv_accel")
    if accel is not None and accel > 1.35:
        add(False, True, "rv_accel", "已实现波动加速", f"HV20/HV60={accel}")
    elif accel is not None and accel > 1.15:
        add(False, False, "rv_accel", "已实现波动加速", f"HV20/HV60={accel} · 温和加速")
    else:
        add(True, False, "rv_accel", "已实现波动加速", f"HV20/HV60={accel}" if accel else "—")

    # 10 RSI knife
    rsi = row.get("rsi14")
    add(rsi is None or rsi >= 28, False, "rsi", "RSI非刀口", f"RSI {rsi}")

    # 11 free fall — 高波动池常见急跌，仅极端才硬否决
    chg5 = row.get("chg_5d")
    if chg5 is not None and chg5 <= -25:
        add(False, True, "freefall", "非自由落体", f"5日 {chg5}%")
    elif chg5 is not None and chg5 <= -15:
        add(False, False, "freefall", "非自由落体", f"5日 {chg5}% · 偏弱")
    else:
        add(True, False, "freefall", "非自由落体", f"5日 {chg5}%" if chg5 is not None else "—")

    # 12 capital budget
    flags = risk.get("flags") or []
    add("单笔超预算" not in flags, False, "budget", "账户预算", f"CSP ${risk.get('capital_per_contract')}")

    # 13 edge posture
    post = edge.get("posture_en")
    add(post not in ("AVOID",), True, "posture", "Edge姿态", edge.get("posture") or "—")

    # 14 yield floor
    yld = put.get("yield_ann_pct")
    add(yld is None or yld >= 8, False, "yield", "静态年化", f"{yld}%" if yld is not None else "—")

    hard_fail = [c for c in checks if c["hard"] and not c["ok"]]
    soft_fail = [c for c in checks if (not c["hard"]) and not c["ok"]]
    passed = sum(1 for c in checks if c["ok"])
    blocked = len(hard_fail) > 0
    if blocked:
        verdict = "否决·先别开"
        severity = "block"
    elif len(soft_fail) >= 3:
        verdict = "谨慎·多项警告"
        severity = "warn"
    elif soft_fail:
        severity = "caution"
        verdict = "可考虑·留意警告"
    else:
        verdict = "通过·条件较好"
        severity = "pass"

    return {
        "checks": checks,
        "passed": passed,
        "total": len(checks),
        "hard_fails": [c["code"] for c in hard_fail],
        "soft_fails": [c["code"] for c in soft_fail],
        "blocked": blocked,
        "verdict": verdict,
        "severity": severity,
    }


def reweight_edge(edge: dict, weights: dict) -> dict:
    """Recompute edge total from stored raw-ish parts using new weights (approx)."""
    if not edge:
        return edge
    # We stored scaled parts already; rebuild from metrics notes is lossy.
    # Prefer recomputing from metrics if present.
    m = edge.get("metrics") or {}
    return compute_edge(
        iv=m.get("iv"),
        hv20=m.get("hv20"),
        hv60=m.get("hv60"),
        iv_pctile=m.get("iv_pctile"),
        near_iv=m.get("near_iv"),
        far_iv=m.get("far_iv"),
        atm_iv=m.get("atm_iv"),
        otm_put_iv=m.get("otm_put_iv"),
        weights=weights,
    )
