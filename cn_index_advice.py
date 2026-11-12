# -*- coding: utf-8 -*-
"""A-share index trading advice from CN index stats + CFFEX near-month option chain.

Runs offline from seed JSON (docs/api/cn_indices.json + cn_index_options.json).
Score formula (brief):
  dir = clamp(-50..50) from vs_ma20/60, RSI14, chg_21d, from_52w_high/low, bias_cn
  vrp = ATM_IV - HV20; hv_pctile and VRP drive stance overlay
  score = dir + vol_overlay(-20..+20) where positive vol_overlay favors selling rich IV
  stance_cn picks among 卖波动 / 防守区间 / 观望高波 / 偏多 / 偏空
Fundamentals (cn_index_fundamentals): tech_score = 50 + score/2, fund_score (政策35/估值25/流动性25/宏观15),
  combined = 0.6·tech + 0.4·fund. Fundamentals only adjust option_bias_cn (see adjust_option_bias);
  stance, score, confidence and all technical thresholds are unchanged.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
SEED_INDICES = ROOT / "docs" / "api" / "cn_indices.json"
SEED_OPTIONS = ROOT / "docs" / "api" / "cn_index_options.json"
SEED_ADVICE = ROOT / "docs" / "api" / "cn_index_advice.json"

DISCLAIMER = "研究用途，非投资建议"

# Round-number / OI-derived structural levels (fallback when chain sparse)
DEFAULT_LEVELS = {
    "csi300": {"support": [4000.0, 3950.0], "resistance": [4100.0, 4200.0]},
    "sse50": {"support": [2750.0, 2725.0], "resistance": [2800.0, 2850.0]},
    "csi1000": {"support": [6300.0, 6000.0], "resistance": [6700.0, 7000.0]},
}


def _f(x: Any) -> float | None:
    try:
        if x is None or x == "":
            return None
        v = float(x)
        if v != v:  # NaN
            return None
        return v
    except Exception:
        return None


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _round_levels(vals: list[float | None], nd: int = 0) -> list[float]:
    out: list[float] = []
    for v in vals:
        if v is None:
            continue
        x = round(float(v), nd)
        if x not in out:
            out.append(x)
    return out


def _front_month(options_payload: dict | None, index_id: str) -> dict | None:
    if not options_payload:
        return None
    by = options_payload.get("by_index") or {}
    board = by.get(index_id)
    if not board:
        for b in options_payload.get("products") or []:
            if b.get("index_id") == index_id:
                board = b
                break
    if not board:
        return None
    months = board.get("months") or []
    return months[0] if months else None


def _atm_band_oi(month: dict | None) -> tuple[int, int, float | None]:
    """Return (call_oi, put_oi, call_put_ratio) near ATM band."""
    if not month:
        return 0, 0, None
    band = month.get("atm_band") or []
    coi = 0
    poi = 0
    for s in band:
        coi += int((s.get("call") or {}).get("oi") or 0)
        poi += int((s.get("put") or {}).get("oi") or 0)
    ratio = (coi / poi) if poi > 0 else (None if coi == 0 else 99.0)
    return coi, poi, ratio


def _direction_score(row: dict) -> tuple[float, list[str]]:
    """Directional lean -50..+50 with brief reasons."""
    score = 0.0
    notes: list[str] = []
    vs20 = _f(row.get("vs_ma20_pct"))
    vs60 = _f(row.get("vs_ma60_pct"))
    rsi = _f(row.get("rsi14"))
    chg21 = _f(row.get("chg_21d"))
    chg5 = _f(row.get("chg_5d"))
    from_hi = _f(row.get("from_52w_high_pct"))
    from_lo = _f(row.get("from_52w_low_pct"))
    bias = row.get("bias_cn") or "中性"

    if vs20 is not None:
        score += _clamp(vs20 * 2.5, -15, 15)
    if vs60 is not None:
        score += _clamp(vs60 * 1.5, -12, 12)
    if rsi is not None:
        score += _clamp((rsi - 50) * 0.6, -18, 18)
    if chg21 is not None:
        score += _clamp(chg21 * 1.2, -15, 15)
    if chg5 is not None:
        score += _clamp(chg5 * 0.6, -8, 8)
    if from_hi is not None and from_hi <= -15:
        score -= 6
        notes.append(f"距52周高{from_hi:.1f}%")
    if from_lo is not None and from_lo <= 5:
        score -= 4
        notes.append(f"贴近52周低(+{from_lo:.1f}%)")
    if bias == "偏强":
        score += 8
    elif bias == "偏弱":
        score -= 8

    return _clamp(score, -50, 50), notes


def _vol_overlay(row: dict, month: dict | None) -> tuple[float, dict]:
    """Vol overlay -20..+20; positive favors selling rich IV / low realized vol."""
    hv20 = _f(row.get("hv20"))
    hv_pctile = _f(row.get("hv_pctile"))
    atm_iv = _f((month or {}).get("atm_iv_est"))
    vrp = (atm_iv - hv20) if (atm_iv is not None and hv20 is not None) else None
    overlay = 0.0
    meta = {
        "hv20": hv20,
        "hv_pctile": hv_pctile,
        "atm_iv": atm_iv,
        "vrp": round(vrp, 1) if vrp is not None else None,
        "atm_strike": (month or {}).get("atm_strike"),
        "front_code": (month or {}).get("code"),
        "spot_opt": (month or {}).get("spot"),
    }
    if vrp is not None:
        # rich IV → sell-vol lean
        overlay += _clamp(vrp * 1.5, -12, 15)
    if hv_pctile is not None:
        if hv_pctile <= 35:
            overlay += 8  # low realized → sell vol friendlier
        elif hv_pctile >= 85:
            overlay -= 12  # extreme realized → avoid naked short vol
        elif hv_pctile >= 70:
            overlay -= 5
    return _clamp(overlay, -20, 20), meta


def _pick_stance(
    index_id: str,
    dir_score: float,
    vol_overlay: float,
    meta: dict,
    oi_ratio: float | None,
    *,
    near_low: bool = False,
) -> tuple[str, str, int]:
    """Return (stance_cn, option_bias_cn, confidence 0..100)."""
    hv_pctile = meta.get("hv_pctile")
    vrp = meta.get("vrp")
    hv20 = meta.get("hv20")
    atm_iv = meta.get("atm_iv")

    # High-vol regime first (CSI1000-like)
    if hv_pctile is not None and hv_pctile >= 85 and (vrp is None or abs(vrp) < 6):
        conf = int(_clamp(55 + (hv_pctile - 85) * 0.8, 55, 90))
        return "观望高波", "不做裸卖", conf

    # Low HV + positive VRP → sell vol (SSE50-like)
    if (
        hv_pctile is not None
        and hv_pctile <= 40
        and vrp is not None
        and vrp >= 2.5
        and abs(dir_score) < 25
    ):
        conf = int(_clamp(60 + vrp * 3 + (40 - hv_pctile) * 0.4, 55, 92))
        bias = "偏卖Call" if dir_score <= 5 else "铁秃鹰/价差"
        if oi_ratio is not None and oi_ratio >= 1.4 and dir_score <= 10:
            bias = "偏卖Call"
        return "卖波动", bias, conf

    # Weak / near lows / mild VRP → defensive range (CSI300-like)
    if dir_score <= -8 or (hv_pctile is not None and 40 < hv_pctile < 85 and dir_score < 5):
        # mild VRP near support → prefer selling lower puts over naked ATM
        if vrp is not None and vrp >= 2 and (hv_pctile is None or hv_pctile < 80):
            opt = "偏卖Put"
        else:
            opt = "铁秃鹰/价差"
        if hv_pctile is not None and hv_pctile >= 75:
            opt = "不做裸卖" if abs(vrp or 0) < 3 else "铁秃鹰/价差"
        conf = int(_clamp(50 + abs(dir_score) * 0.6, 45, 85))
        # Near 52w lows: label 防守区间 even if dir is very negative (avoid chase-short)
        if near_low and dir_score > -55:
            return "防守区间", opt, conf
        if dir_score <= -28 and not near_low:
            return "偏空", ("铁秃鹰/价差" if opt == "偏卖Put" else opt), conf
        if dir_score <= -22 and not near_low:
            return "偏空", ("铁秃鹰/价差" if opt == "偏卖Put" else opt), conf
        return "防守区间", opt, conf

    # Directional
    if dir_score >= 18:
        conf = int(_clamp(50 + dir_score * 0.7, 50, 88))
        opt = "偏卖Put" if (vrp is not None and vrp >= 1.5 and (hv_pctile or 50) < 70) else "铁秃鹰/价差"
        return "偏多", opt, conf
    if dir_score <= -18:
        conf = int(_clamp(50 + abs(dir_score) * 0.7, 50, 88))
        opt = "偏卖Call" if (vrp is not None and vrp >= 1.5 and (hv_pctile or 50) < 70) else "铁秃鹰/价差"
        return "偏空", opt, conf

    # Mild middle
    if vrp is not None and vrp >= 3 and (hv_pctile is None or hv_pctile < 60):
        return "卖波动", "铁秃鹰/价差", 58
    if atm_iv is not None and hv20 is not None and abs(atm_iv - hv20) < 2 and (hv_pctile or 50) >= 70:
        return "观望高波", "不做裸卖", 55
    return "防守区间", "铁秃鹰/价差", 52


def _levels_for(row: dict, month: dict | None) -> dict:
    idx = row.get("id") or ""
    base = dict(DEFAULT_LEVELS.get(idx, {"support": [], "resistance": []}))
    px = _f(row.get("price"))
    lo52 = _f(row.get("low_52w"))
    hi52 = _f(row.get("high_52w"))
    # Derive MA20/MA60 approx from vs_ma pcts
    vs20 = _f(row.get("vs_ma20_pct"))
    vs60 = _f(row.get("vs_ma60_pct"))
    ma20 = (px / (1 + vs20 / 100.0)) if (px and vs20 is not None) else None
    ma60 = (px / (1 + vs60 / 100.0)) if (px and vs60 is not None) else None

    supports = list(base.get("support") or [])
    resists = list(base.get("resistance") or [])
    if lo52:
        supports.append(lo52)
    if ma20 and px and ma20 < px:
        supports.append(ma20)
    if ma60 and px and ma60 < px:
        supports.append(ma60)
    if ma20 and px and ma20 > px:
        resists.append(ma20)
    if ma60 and px and ma60 > px:
        resists.append(ma60)
    atm = _f((month or {}).get("atm_strike"))
    if atm and px:
        # next round strikes as soft levels
        if atm < px:
            supports.append(atm)
        else:
            resists.append(atm)

    # Keep levels on the correct side of spot, sorted, top 2–3
    if px:
        supports = sorted({s for s in supports if s is not None and s <= px * 1.002}, reverse=True)[:3]
        resists = sorted({r for r in resists if r is not None and r >= px * 0.998})[:3]
    else:
        supports = sorted(supports, reverse=True)[:3]
        resists = sorted(resists)[:3]

    nd = 0 if (px or 0) >= 500 else 2
    return {
        "support": _round_levels(supports, nd),
        "resistance": _round_levels(resists, nd),
    }


def _bullets(
    row: dict,
    stance: str,
    option_bias: str,
    meta: dict,
    levels: dict,
    oi_ratio: float | None,
    dir_notes: list[str],
) -> list[str]:
    name = row.get("name") or row.get("short") or row.get("id")
    px = _f(row.get("price"))
    hv20 = meta.get("hv20")
    hv_pctile = meta.get("hv_pctile")
    atm_iv = meta.get("atm_iv")
    vrp = meta.get("vrp")
    front = meta.get("front_code") or "近月"
    bullets: list[str] = []

    loc = []
    if px is not None:
        loc.append(f"现价约 {px:.0f}" if px >= 100 else f"现价约 {px:.2f}")
    if row.get("bias_cn"):
        loc.append(f"趋势标「{row['bias_cn']}」")
    if hv20 is not None:
        loc.append(f"HV20 {hv20:.1f}%（分位 {hv_pctile:.0f}）" if hv_pctile is not None else f"HV20 {hv20:.1f}%")
    if loc:
        bullets.append("；".join(loc) + "。")

    if atm_iv is not None:
        vrp_txt = f"，VRP≈{vrp:+.1f}pt" if vrp is not None else ""
        bullets.append(f"{front} ATM IV≈{atm_iv:.1f}%{vrp_txt}。")

    supp = levels.get("support") or []
    res = levels.get("resistance") or []
    if supp or res:
        s = "/".join(str(int(x) if float(x).is_integer() else x) for x in supp[:2]) or "—"
        r = "/".join(str(int(x) if float(x).is_integer() else x) for x in res[:2]) or "—"
        bullets.append(f"关键位：支撑 {s}，阻力 {r}。")

    if stance == "卖波动":
        bullets.append(f"低已实现波动+IV溢价，结构偏向{option_bias}，控制保证金与止损。")
    elif stance == "防守区间":
        bullets.append(f"趋势偏弱或靠支撑，先当区间；{option_bias}，避免裸卖 ATM 跨式。")
    elif stance == "观望高波":
        bullets.append("HV 分位极高且 IV≈HV，卖波动赚头薄；优先观望或仅做有限风险价差。")
    elif stance == "偏多":
        bullets.append(f"动量偏强；期权侧 {option_bias}，现货/期货只轻仓顺势。")
    elif stance == "偏空":
        bullets.append(f"动量偏弱；期权侧 {option_bias}，反弹减仓优于追空。")

    if oi_ratio is not None and oi_ratio >= 1.35:
        bullets.append(f"近月 ATM 带 Call 持仓偏重（C/P≈{oi_ratio:.2f}），上方供给更密。")
    elif oi_ratio is not None and oi_ratio <= 0.75:
        bullets.append(f"近月 ATM 带 Put 持仓偏重（C/P≈{oi_ratio:.2f}），下方防护更密。")

    # Keep 2–4
    out = [b for b in bullets if b][:4]
    if len(out) < 2:
        out.append(f"{name}：按规则给出「{stance}」立场（{option_bias}）。")
    return out


def _expiry_of(month: dict | None):
    from cn_index_fundamentals import _to_date

    return _to_date((month or {}).get("expire"))


def advice_for_index(row: dict, options_payload: dict | None = None, fund_bundle: dict | None = None) -> dict:
    month = _front_month(options_payload, row.get("id") or "")
    dir_score, dir_notes = _direction_score(row)
    vol_overlay, meta = _vol_overlay(row, month)
    coi, poi, oi_ratio = _atm_band_oi(month)
    meta["call_oi_atm"] = coi
    meta["put_oi_atm"] = poi
    meta["oi_cp_ratio"] = round(oi_ratio, 2) if oi_ratio is not None else None

    from_lo = _f(row.get("from_52w_low_pct"))
    near_low = from_lo is not None and from_lo <= 8.0
    stance, option_bias, confidence = _pick_stance(
        row.get("id") or "", dir_score, vol_overlay, meta, oi_ratio, near_low=near_low
    )
    # Composite score: direction primary, vol overlay secondary (sell-vol does not flip sign alone)
    score = int(round(_clamp(dir_score + vol_overlay * 0.35, -100, 100)))
    # For pure 卖波动 with flat dir, nudge score toward mild negative (sell calls / range)
    if stance == "卖波动" and abs(score) < 8:
        score = -max(5, int(abs(vol_overlay)))
    if stance == "观望高波":
        score = int(round(_clamp(dir_score * 0.5, -25, 25)))

    levels = _levels_for(row, month)
    bullets = _bullets(row, stance, option_bias, meta, levels, oi_ratio, dir_notes)

    out = {
        "stance_cn": stance,
        "score": score,
        "confidence": confidence,
        "bullets_cn": bullets,
        "levels": levels,
        "option_bias_cn": option_bias,
        "disclaimer_cn": DISCLAIMER,
        "inputs": {
            "price": row.get("price"),
            "bias_cn": row.get("bias_cn"),
            "hv20": meta.get("hv20"),
            "hv_pctile": meta.get("hv_pctile"),
            "atm_iv": meta.get("atm_iv"),
            "vrp": meta.get("vrp"),
            "front": meta.get("front_code"),
            "dir_score": round(dir_score, 1),
            "vol_overlay": round(vol_overlay, 1),
            "oi_cp_ratio": meta.get("oi_cp_ratio"),
        },
    }
    try:
        from cn_index_fundamentals import apply_to_advice

        apply_to_advice(out, row.get("id") or "", fund_bundle, expiry=_expiry_of(month))
        if out.get("option_bias_cn") != option_bias:
            # keep bullet wording consistent with the fundamentals-adjusted option bias
            out["bullets_cn"] = _bullets(row, stance, out["option_bias_cn"], meta, levels, oi_ratio, dir_notes)
    except Exception:  # noqa: BLE001 — fundamentals never break technical advice
        logger.debug("index fundamentals failed for %s", row.get("id"), exc_info=True)
        out.setdefault("fund_status", "not_computed")
        out.setdefault("fund_status_cn", "基本面未计算")
    # Concrete reference levels for the (final, fundamentals-adjusted) option bias. Pure CPU on the
    # cached chain snapshot; missing chain/quotes → 无数据, never fabricated.
    try:
        from cn_index_option_levels import build_option_levels, etf_options_note

        out["option_levels"] = build_option_levels(
            row.get("id") or "", out.get("option_bias_cn") or "", options_payload, levels, _f(row.get("price"))
        )
        out["option_levels"]["etf_note_cn"] = etf_options_note(row.get("id") or "")
    except Exception:  # noqa: BLE001 — levels never break the advice
        logger.debug("option levels failed for %s", row.get("id"), exc_info=True)
        out["option_levels"] = {"status": "nodata", "reason_cn": "无数据（价位计算失败）",
                                "label_cn": "参考价位，不构成下单建议"}
    return out


def build_cn_index_advice(
    indices_payload: dict | None = None,
    options_payload: dict | None = None,
    fund_bundle: dict | None = None,
) -> dict:
    """Build full advice bundle. Loads seeds when payloads omitted.

    fund_bundle: A股指数基本面 bundle (cn_index_fundamentals). When omitted, uses the one embedded in
    indices_payload["index_fundamentals"] (snapshot); never fetches from the network here.
    """
    if indices_payload is None:
        indices_payload = _load_json(SEED_INDICES) or {"indices": []}
    if options_payload is None:
        options_payload = _load_json(SEED_OPTIONS)
    if fund_bundle is None:
        fund_bundle = indices_payload.get("index_fundamentals")

    advices: dict[str, dict] = {}
    for row in indices_payload.get("indices") or []:
        iid = row.get("id")
        if not iid:
            continue
        advices[iid] = advice_for_index(row, options_payload, fund_bundle)
    try:
        from cn_index_fundamentals import METHOD_TEXT
    except Exception:  # noqa: BLE001
        METHOD_TEXT = ""

    return {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "method": {
            "goal": "根据指数位置/已实现波动 + 近月 ATM IV/持仓，给出结构化交易立场",
            "score": "dir(-50..50:均线/RSI/涨跌/距52周/bias) + 0.35*vol_overlay(-20..20: VRP与HV分位)；stance 另据规则覆盖",
            "stances": "卖波动 | 防守区间 | 观望高波 | 偏多 | 偏空",
            "options": "近月链 ATM IV 与 ATM 带 Call/Put 持仓比（中金所新浪转播）",
            "option_levels": "期权倾向→参考价位：真实挂牌行权价，支撑/阻力、|Δ|≤0.25、1σ 外取最保守档；止损破位或权利金2倍，止盈衰减50%（参考价位，不构成下单建议）",
            "fundamental": METHOD_TEXT,
        },
        "fund_asof": (fund_bundle or {}).get("fetched_at"),
        "fund_notes": (fund_bundle or {}).get("notes") or [],
        "advices": advices,
        "disclaimer_cn": DISCLAIMER,
        "source_indices_at": indices_payload.get("updated_at"),
        "source_options_at": (options_payload or {}).get("updated_at"),
    }


def attach_advice_to_indices(
    indices_payload: dict,
    options_payload: dict | None = None,
    *,
    advice_bundle: dict | None = None,
    fund_bundle: dict | None = None,
) -> dict:
    """Return a shallow-copied indices payload with per-row advice + top-level advices."""
    if options_payload is None:
        options_payload = _load_json(SEED_OPTIONS)
    bundle = advice_bundle or build_cn_index_advice(indices_payload, options_payload, fund_bundle)
    out = dict(indices_payload)
    advices = bundle.get("advices") or {}
    rows = []
    for row in out.get("indices") or []:
        r = dict(row)
        iid = r.get("id")
        if iid and iid in advices:
            r["advice"] = advices[iid]
        rows.append(r)
    out["indices"] = rows
    out["advices"] = advices
    out["advice_updated_at"] = bundle.get("updated_at")
    out["advice_method"] = bundle.get("method")
    out["advice_fund_asof"] = bundle.get("fund_asof")
    out["advice_fund_notes"] = bundle.get("fund_notes") or []
    # Keep research disclaimer visible
    disc = out.get("disclaimer") or ""
    if DISCLAIMER not in disc:
        out["disclaimer"] = (disc + " · " if disc else "") + DISCLAIMER
    return out


def write_advice_snapshot(
    path: Path | None = None,
    indices_payload: dict | None = None,
    options_payload: dict | None = None,
) -> dict:
    path = path or SEED_ADVICE
    bundle = build_cn_index_advice(indices_payload, options_payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(bundle, ensure_ascii=False, default=str), encoding="utf-8")
    return bundle


def load_advice_seed() -> dict | None:
    return _load_json(SEED_ADVICE)


def _load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        logger.debug("load %s failed", path, exc_info=True)
        return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    out = write_advice_snapshot()
    print("updated", out["updated_at"])
    for k, a in (out.get("advices") or {}).items():
        print(k, a["stance_cn"], "score", a["score"], "conf", a["confidence"], a["option_bias_cn"],
              "tech", a.get("tech_score"), "fund", a.get("fund_score"), "comb", a.get("combined_score"))
        for b in a["bullets_cn"]:
            print(" -", b)
