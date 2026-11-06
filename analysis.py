# -*- coding: utf-8 -*-
"""Historical high/low scoring and cost-aware analysis for the dashboard."""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any

import pandas as pd

from config import ANALYSIS_CACHE_TTL
from cost_benchmarks import cost_vs_price
from data_sources import get_daily_history
from deflation_tags import get_deflation
from supply_cycles import get_cycle
from universe import get_instrument, get_universe

logger = logging.getLogger(__name__)

_analysis_cache: tuple[float, dict] | None = None
_analysis_refresh_lock = __import__("threading").Lock()
_analysis_refresh_running = False



def _load_analysis_seed() -> dict | None:
    """Cold-start fallback from exported Pages snapshot (already API-slimmed)."""
    from pathlib import Path
    import json

    path = Path(__file__).resolve().parent / "docs" / "api" / "analysis_all.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and (data.get("lows") or data.get("highs") or data.get("all")):
            return data
    except Exception:
        logger.debug("analysis seed load failed", exc_info=True)
    return None


def _schedule_analysis_refresh() -> None:
    global _analysis_refresh_running
    if not _analysis_refresh_lock.acquire(blocking=False):
        return
    if _analysis_refresh_running:
        _analysis_refresh_lock.release()
        return
    _analysis_refresh_running = True
    _analysis_refresh_lock.release()

    import threading

    def _bg():
        global _analysis_refresh_running, _analysis_cache
        try:
            payload = _compute_full_analysis()
            if payload and (payload.get("all") or payload.get("lows") or payload.get("highs")):
                _analysis_cache = (time.time(), payload)
        except Exception:
            logger.exception("background analysis refresh failed")
        finally:
            with _analysis_refresh_lock:
                _analysis_refresh_running = False

    threading.Thread(target=_bg, daemon=True).start()


# Exclude pure financials from "commodity cost" narrative emphasis
NO_COST_SECTORS = {"股指", "外汇", "利率", "波动率", "加密", "航运"}


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _percentile(series: pd.Series, cur: float) -> float:
    s = series.dropna()
    if s.empty:
        return float("nan")
    return float((s < cur).mean() * 100)


def _window_stats(series: pd.Series, cur: float, years: int) -> dict:
    n = years * 252
    sub = series.iloc[-n:] if len(series) >= n else series
    if sub.empty:
        return {"pct": None, "min": None, "max": None}
    return {
        "pct": _percentile(sub, cur),
        "min": float(sub.min()),
        "max": float(sub.max()),
    }


def _ret(series: pd.Series, cur: float, days: int) -> float | None:
    if len(series) <= days:
        return None
    base = float(series.iloc[-days - 1])
    if base == 0:
        return None
    return (cur / base - 1) * 100


def compute_instrument_stats(instrument_id: str) -> dict | None:
    meta = get_instrument(instrument_id)
    if not meta:
        return None
    try:
        df = get_daily_history(instrument_id)
    except Exception as exc:
        logger.warning("history fail %s: %s", instrument_id, exc)
        return None
    if df is None or df.empty or len(df) < 60:
        return None

    s = df.set_index("date")["close"].dropna().astype(float)
    if len(s) < 60:
        return None

    cur = float(s.iloc[-1])
    hist_min = float(s.min())
    hist_max = float(s.max())
    hist_mean = float(s.mean())
    hist_median = float(s.median())
    rng = hist_max - hist_min
    pos_range = (cur - hist_min) / rng * 100 if rng > 0 else None
    pct_all = _percentile(s, cur)

    w3 = _window_stats(s, cur, 3)
    w5 = _window_stats(s, cur, 5)
    w10 = _window_stats(s, cur, 10)

    pct_all_v = pct_all if pct_all == pct_all else 50.0
    pct_3 = w3["pct"] if w3["pct"] is not None else pct_all_v
    pct_5 = w5["pct"] if w5["pct"] is not None else pct_all_v
    pct_10 = w10["pct"] if w10["pct"] is not None else pct_all_v

    # Lower = more at historical low
    low_score = 0.25 * pct_all_v + 0.25 * pct_10 + 0.30 * pct_5 + 0.20 * pct_3
    # Higher = more at historical high
    high_score = 0.25 * (100 - pct_all_v) + 0.25 * (100 - pct_10) + 0.30 * (100 - pct_5) + 0.20 * (100 - pct_3)
    # Invert naming: high_score_raw as proximity to high = average of high percentiles
    high_proximity = 0.25 * pct_all_v + 0.25 * pct_10 + 0.30 * pct_5 + 0.20 * pct_3
    # For ranking highs we want high percentiles → use high_proximity directly as "high_score"
    # and low_score as multi-horizon low score (low is low)

    from_min_pct = (cur / hist_min - 1) * 100 if hist_min else None
    from_max_pct = (cur / hist_max - 1) * 100 if hist_max else None

    cost = cost_vs_price(instrument_id, cur)

    tag = _classify_tag(low_score, high_proximity, pct_5, pct_all_v, cost)

    row = {
        "id": instrument_id,
        "symbol": meta["symbol"],
        "name": meta["name"],
        "market": meta["market"],
        "sector": meta["sector"],
        "exchange": meta.get("exchange", ""),
        "price": cur,
        "hist_min": hist_min,
        "hist_max": hist_max,
        "hist_mean": hist_mean,
        "hist_median": hist_median,
        "pct_all": round(pct_all_v, 2),
        "pct_3y": round(pct_3, 2),
        "pct_5y": round(pct_5, 2),
        "pct_10y": round(pct_10, 2),
        "min_5y": w5["min"],
        "max_5y": w5["max"],
        "min_10y": w10["min"],
        "max_10y": w10["max"],
        "pos_range": round(pos_range, 2) if pos_range is not None else None,
        "low_score": round(low_score, 2),
        "high_score": round(high_proximity, 2),  # higher = closer to highs
        "from_min_pct": round(from_min_pct, 2) if from_min_pct is not None else None,
        "from_max_pct": round(from_max_pct, 2) if from_max_pct is not None else None,
        "vs_median_pct": round((cur / hist_median - 1) * 100, 2) if hist_median else None,
        "ret_1m": _round(_ret(s, cur, 21)),
        "ret_3m": _round(_ret(s, cur, 63)),
        "ret_6m": _round(_ret(s, cur, 126)),
        "ret_1y": _round(_ret(s, cur, 252)),
        "n_obs": len(s),
        "start": str(s.index[0].date()),
        "end": str(s.index[-1].date()),
        "tag": tag,
        "deflation_tag": (get_deflation(instrument_id) or {}).get("label"),
        "deflation_note": (get_deflation(instrument_id) or {}).get("note"),
        "has_cost": cost is not None,
        "cost": cost,
        "seasonality": _seasonality(s, cur),
        "analysis": build_analysis_text(meta, cur, {
            "pct_all": pct_all_v,
            "pct_3y": pct_3,
            "pct_5y": pct_5,
            "pct_10y": pct_10,
            "hist_min": hist_min,
            "hist_max": hist_max,
            "from_min_pct": from_min_pct,
            "from_max_pct": from_max_pct,
            "low_score": low_score,
            "high_score": high_proximity,
            "ret_1y": _ret(s, cur, 252),
            "tag": tag,
            "seasonality": _seasonality(s, cur),
        }, cost),
    }
    return row


def _seasonality(series: pd.Series, cur: float) -> dict:
    """Same-month historical percentile and typical monthly return."""
    if series is None or series.empty or not isinstance(series.index, pd.DatetimeIndex):
        return {}
    month = int(series.index[-1].month)
    same = series[series.index.month == month]
    pct = float((same < cur).mean() * 100) if len(same) else None
    monthly = series.resample("ME").last().pct_change() * 100
    same_ret = monthly[monthly.index.month == month].dropna()
    return {
        "month": month,
        "same_month_pct": round(pct, 1) if pct is not None else None,
        "avg_month_ret": round(float(same_ret.mean()), 2) if len(same_ret) else None,
        "n_seasons": int(len(same_ret)),
    }


def _round(v: float | None, n: int = 2) -> float | None:
    if v is None:
        return None
    return round(v, n)


MOVER_PERIODS = {
    "W": {"days": 5, "label": "近一周"},
    "M": {"days": 21, "label": "近一月"},
    "Y": {"days": 252, "label": "近一年"},
}


def compute_futures_movers(
    period: str = "W", market: str = "all", limit: int = 20
) -> dict[str, Any]:
    """Rank commodity futures by weekly / monthly / yearly return."""
    period = (period or "W").upper()
    if period not in MOVER_PERIODS:
        period = "W"
    market = (market or "all").lower()
    days = MOVER_PERIODS[period]["days"]
    limit = max(5, min(int(limit or 20), 50))

    items, _ = get_universe()
    if market in {"cn", "us"}:
        items = [x for x in items if x["market"] == market]

    rows: list[dict] = []
    for it in items:
        try:
            df = get_daily_history(it["id"])
        except Exception:
            continue
        if df is None or df.empty:
            continue
        s = df.set_index("date")["close"].dropna().astype(float)
        if len(s) < days + 1:
            continue
        win = s.iloc[-(days + 1) :]
        daily = win.pct_change().dropna()
        if daily.empty:
            continue
        # continuous-contract roll glitches, not real 40%+ days
        if float(daily.min()) < -0.40 or float(daily.max()) > 0.40:
            continue
        cur = float(win.iloc[-1])
        base = float(win.iloc[0])
        if base == 0:
            continue
        chg = (cur / base - 1.0) * 100.0
        rows.append(
            {
                "id": it["id"],
                "symbol": it["symbol"],
                "name": it["name"],
                "market": it["market"],
                "sector": it["sector"],
                "exchange": it.get("exchange", ""),
                "deflation_tag": it.get("deflation_tag"),
                "price": round(cur, 4 if abs(cur) < 1 else 2),
                "change_pct": round(chg, 2),
                "asof": str(win.index[-1].date()),
            }
        )

    rows.sort(key=lambda r: r["change_pct"], reverse=True)
    last_asof = None
    if rows:
        last_asof = max((r["asof"] for r in rows if r.get("asof")), default=None)
    return {
        "period": period,
        "period_label": MOVER_PERIODS[period]["label"],
        "market": market,
        "asof": last_asof,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "universe": len(rows),
        "gainers": rows[:limit],
        "losers": list(reversed(rows[-limit:])) if rows else [],
        "note": "期货涨跌按连续合约交易日估算：周=5日、月=21日、年=252日。换月可能有跳空。",
    }


def _classify_tag(
    low_score: float,
    high_score: float,
    pct_5y: float,
    pct_all: float,
    cost: dict | None,
) -> str:
    """Return: 历史低位 / 历史高位 / 偏低 / 偏高 / 中性"""
    is_low = low_score <= 28 or pct_5y <= 20 or pct_all <= 15
    is_high = high_score >= 72 or pct_5y >= 80 or pct_all >= 85

    if is_low and not is_high:
        if cost and cost.get("below_cash"):
            return "历史低位·破现金成本"
        if cost and cost.get("below_primary"):
            return "历史低位·贴完全成本"
        return "历史低位"
    if is_high and not is_low:
        if cost and cost.get("above_high"):
            return "历史高位·超激励上沿"
        return "历史高位"
    if low_score <= 40 or pct_5y <= 30:
        return "偏低"
    if high_score >= 60 or pct_5y >= 70:
        return "偏高"
    return "中性"


def build_analysis_text(meta: dict, price: float, st: dict, cost: dict | None) -> dict:
    name = meta["name"]
    tag = st["tag"]
    lines = []

    dtag = get_deflation(meta.get("id") or "")
    defl_txt = f" 通缩标签：{dtag['label']}。" if dtag else ""
    lines.append(
        f"【定位】{name} 现价 {price:.4g}，全样本分位 {st['pct_all']:.1f}%，"
        f"近5年分位 {st['pct_5y']:.1f}%，近10年分位 {st['pct_10y']:.1f}%。"
        f"标签：{tag}。{defl_txt}"
    )
    if dtag:
        lines.append(f"【通缩消费】{dtag['label']}。{dtag['note']}")
    lines.append(
        f"【区间】历史最低 {st['hist_min']:.4g}，历史最高 {st['hist_max']:.4g}；"
        f"距历史低点 {st['from_min_pct']:+.1f}%，距历史高点 {st['from_max_pct']:+.1f}%。"
    )
    if st.get("ret_1y") is not None:
        lines.append(f"【动量】近1年涨跌约 {st['ret_1y']:+.1f}%。")

    cost_block = None
    if cost:
        cost_block = {
            "primary_cost": cost["primary_cost"],
            "cash_cost": cost["cash_cost"],
            "comfort_high": cost["comfort_high"],
            "unit": cost["unit"],
            "label": cost["label"],
            "vs_primary_pct": cost["vs_primary_pct"],
            "vs_cash_pct": cost["vs_cash_pct"],
            "position_vs_band": cost["position_vs_band"],
            "source": cost["source"],
            "notes": cost["notes"],
        }
        lines.append(
            f"【成本】主要成本中枢（{cost['label']}）约 {cost['primary_cost']:.4g} {cost['unit']}，"
            f"现金成本约 {cost['cash_cost']:.4g}，激励上沿约 {cost['comfort_high']:.4g}。"
            f"现价相对完全成本 {cost['vs_primary_pct']:+.1f}%，相对现金成本 {cost['vs_cash_pct']:+.1f}%，"
            f"处于「{cost['position_vs_band']}」。"
        )
        if cost["below_cash"]:
            lines.append(
                "【成本解读】价格已低于现金成本带，中长期若持续，边际产能收缩弹性上升；"
                "需验证是否为短暂冲击或供需实质宽松。"
            )
        elif cost["below_primary"]:
            lines.append(
                "【成本解读】价格位于现金成本与完全成本之间：现金尚可覆盖、资本开支激励偏弱，"
                "常见于磨底或累库周期，反弹高度取决于去库与事件催化。"
            )
        elif cost["above_high"]:
            lines.append(
                "【成本解读】价格高于成本激励上沿，利润丰厚易刺激供应/利润回吐；"
                "高位更应关注估值拥挤与基本面证伪风险，而非简单外推上涨。"
            )
        else:
            lines.append(
                "【成本解读】价格覆盖完全成本并处于合理利润带，成本端不构成极端底部或泡沫结论，"
                "需结合库存、季节性与宏观驱动。"
            )
        lines.append(f"【成本来源】{cost['source']}（行业估计，非实时工厂成本）。")
    else:
        lines.append(
            "【成本】该品种为金融指数/无标准生产成本锚，或暂未配置成本模型；"
            "高低位判断主要依据历史价格分位与回撤。"
        )

    cycle = get_cycle(meta.get("id") or "", meta.get("sector"))
    lines.append(
        f"【减产周期】从持续亏损到供给可见下降大约 {cycle['lag']}；"
        f"价格升值段更常见于「{cycle['to_price']}」。减产方式：{cycle['cut']}。"
    )
    seas = st.get("seasonality") or {}
    if seas.get("avg_month_ret") is not None:
        lines.append(
            f"【季节性】当前为 {seas.get('month')} 月；历史同月均涨跌约 {seas['avg_month_ret']:+.1f}%"
            f"（{seas.get('n_seasons', 0)} 个季节），同月价格分位 {seas.get('same_month_pct')}%。"
        )

    if cycle.get("status"):
        ev = cycle.get("evidence") or ""
        src = cycle.get("source") or ""
        asof = cycle.get("asof") or ""
        lines.append(
            f"【当前减产】{cycle['status']}。"
            + (f"{ev}" if ev else "")
            + (f"（截至 {asof}，{src}）" if asof or src else "")
        )

    # Actionable observation (not investment advice)
    if "历史低位" in tag:
        lines.append(
            "【观察要点】历史低位关注：①是否贴近/跌破成本 ②库存与仓单是否仍在累 ③"
            "供应是否开始收缩 ④有无明确事件催化。低位不等于立即反转，可能长期磨底。"
        )
    elif "历史高位" in tag:
        lines.append(
            "【观察要点】历史高位关注：①利润是否刺激扩产 ②需求是否透支 ③"
            "宏观/汇率/利率扰动 ④持仓拥挤。高位波动加大，趋势与均值回归需分开看。"
        )
    else:
        lines.append(
            "【观察要点】当前非极端高低位，更适合作为相关品种对冲/配对或趋势跟踪的背景仓，"
            "等待分位进入极端区或成本带被有效突破后再提高关注权重。"
        )

    lines.append("※ 以上为量化分位 + 成本框架的研究观察，不构成投资建议。")

    return {
        "summary": lines[0],
        "paragraphs": lines,
        "tag": tag,
        "cost": cost_block,
        "cycle": {
            "lag": cycle["lag"],
            "to_price": cycle["to_price"],
            "cut": cycle["cut"],
            "status": cycle.get("status"),
            "evidence": cycle.get("evidence"),
            "asof": cycle.get("asof"),
            "source": cycle.get("source"),
        },
    }


def build_full_analysis(force: bool = False) -> dict:
    """Return extremes analysis fast. Heavy history pulls never block HTTP.

    force=True only schedules a background recompute; callers get cache or
    docs/api/analysis_all.json seed immediately.
    """
    global _analysis_cache

    if not force and _analysis_cache and time.time() - _analysis_cache[0] < ANALYSIS_CACHE_TTL:
        return _analysis_cache[1]

    snapshot = None
    if _analysis_cache is not None:
        snapshot = _analysis_cache[1]
    else:
        seed = _load_analysis_seed()
        if seed is not None:
            # Seed is already slimmed API shape; good enough for UI.
            _analysis_cache = (time.time(), seed)
            snapshot = seed

    if snapshot is not None:
        _schedule_analysis_refresh()
        return snapshot

    # No seed — last resort sync compute (local/dev).
    return _compute_full_analysis()


def _compute_full_analysis() -> dict:
    global _analysis_cache

    _, by_id = get_universe()
    ids = list(by_id.keys())
    rows: list[dict] = []

    def one(i: str):
        return compute_instrument_stats(i)

    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(one, i): i for i in ids}
        for fut in as_completed(futs):
            try:
                r = fut.result()
                if r:
                    rows.append(r)
            except Exception as exc:
                logger.warning("stats error %s: %s", futs[fut], exc)

    if not rows:
        payload = {
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "all": [],
            "lows": [],
            "highs": [],
            "note": "暂无足够历史数据",
        }
        _analysis_cache = (time.time(), payload)
        return payload

    # Rank
    lows = sorted(rows, key=lambda x: x["low_score"])
    highs = sorted(rows, key=lambda x: x["high_score"], reverse=True)

    # Extreme sets (commodity-only; include 偏低 so NG-class names are not dropped)
    low_pool = [
        r for r in lows
        if r["low_score"] <= 42
        or r["pct_5y"] <= 38
        or r["pct_all"] <= 32
        or "历史低位" in r["tag"]
        or r["tag"] == "偏低"
    ]
    high_pool = [
        r for r in highs
        if r["high_score"] >= 60
        or r["pct_5y"] >= 70
        or r["pct_all"] >= 75
        or "历史高位" in r["tag"]
        or r["tag"] == "偏高"
    ]
    if len(low_pool) < 8:
        low_pool = lows[:12]
    if len(high_pool) < 8:
        high_pool = highs[:12]

    def low_key(r):
        # Prefer below-cost names slightly; keep score primary
        bonus = -5 if r.get("cost") and r["cost"].get("below_primary") else 0
        return r["low_score"] + bonus

    low_pool = sorted(low_pool, key=low_key)
    high_pool = sorted(high_pool, key=lambda r: r["high_score"], reverse=True)

    def _mix_cn_us(pool: list, score_key, reverse: bool, cn_n: int = 12, us_n: int = 8) -> list:
        """Pick top CN and top US separately so NA commodities (e.g. NG) are not crowded out."""
        ordered = sorted(pool, key=score_key, reverse=reverse)
        cn = [r for r in ordered if r.get("market") == "cn"][:cn_n]
        us = [r for r in ordered if r.get("market") == "us"][:us_n]
        # interleave-ish: keep CN first by extremity then US block (clear for watchlist)
        return cn + us

    low_extreme = _mix_cn_us(low_pool, low_key, reverse=False, cn_n=12, us_n=8)
    high_extreme = _mix_cn_us(high_pool, lambda r: r["high_score"], reverse=True, cn_n=12, us_n=8)

    payload = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "all": rows,
        "lows": low_extreme,
        "highs": high_extreme,
        "criteria": {
            "low": "综合低位分 low_score=0.25*全样本+0.25*10年+0.30*5年+0.20*3年分位；或近5年分位≤25% / 全样本≤20%",
            "high": "高位用对称高分位；或近5年分位≥75% / 全样本≥80%",
            "cost": "成本为行业静态估计，非实时工厂成本；见各品种 cost.source",
        },
        "note": "历史高低位基于连续合约日线分位；连续合约换月可能产生跳跃，解读时请结合主力合约。",
    }
    _analysis_cache = (time.time(), payload)
    return payload


def get_instrument_analysis(instrument_id: str, force: bool = False) -> dict | None:
    full = build_full_analysis(force=force)
    for r in full.get("all", []):
        if r["id"] == instrument_id:
            return r
    # compute on the fly
    return compute_instrument_stats(instrument_id)


def extremes_summary(force: bool = False) -> dict:
    full = build_full_analysis(force=force)

    def slim(r: dict) -> dict:
        c = r.get("cost") or {}
        return {
            "id": r["id"],
            "name": r["name"],
            "symbol": r["symbol"],
            "market": r["market"],
            "sector": r["sector"],
            "exchange": r.get("exchange", ""),
            "price": r["price"],
            "tag": r["tag"],
            "deflation_tag": r.get("deflation_tag"),
            "deflation_note": r.get("deflation_note"),
            "pct_all": r["pct_all"],
            "pct_5y": r["pct_5y"],
            "pct_10y": r["pct_10y"],
            "low_score": r["low_score"],
            "high_score": r["high_score"],
            "from_max_pct": r["from_max_pct"],
            "from_min_pct": r["from_min_pct"],
            "ret_1y": r["ret_1y"],
            "primary_cost": c.get("primary_cost"),
            "cash_cost": c.get("cash_cost"),
            "cost_unit": c.get("unit"),
            "vs_primary_pct": c.get("vs_primary_pct"),
            "vs_cash_pct": c.get("vs_cash_pct"),
            "cost_band": c.get("position_vs_band"),
            "analysis_summary": (r.get("analysis") or {}).get("summary"),
        }

    return {
        "updated_at": full["updated_at"],
        "lows": [slim(r) for r in full["lows"]],
        "highs": [slim(r) for r in full["highs"]],
        "criteria": full["criteria"],
        "note": full["note"],
    }
